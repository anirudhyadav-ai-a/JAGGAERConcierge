"""MeteredLLM wraps any provider: counts calls per request, enforces the per-request call cap,
and (for paid providers) reserves and settles cost against the BudgetGuard."""
from __future__ import annotations

import contextvars
import json
from dataclasses import dataclass, field

from .base import LLM, LLMRefusal, LLMResponse
from .budget import BudgetGuard, CallLimitExceeded


@dataclass
class RequestMeter:
    max_calls: int
    calls: int = 0
    cost_usd: float = 0.0
    by_purpose: dict = field(default_factory=dict)


_current: contextvars.ContextVar[RequestMeter | None] = contextvars.ContextVar("request_meter", default=None)


def start_request(max_calls: int) -> RequestMeter:
    meter = RequestMeter(max_calls)
    _current.set(meter)          # asyncio tasks spawned later copy this context and share the same meter
    return meter


def _estimate_tokens(system: str, messages: list[dict], tools: list[dict] | None) -> int:
    chars = len(system) + len(json.dumps(messages, default=str)) + len(json.dumps(tools or []))
    return chars // 3 + 50     # conservative: ~3 chars per token


class MeteredLLM:
    def __init__(self, inner: LLM, budget: BudgetGuard | None, max_tokens: int):
        self.inner = inner
        self.budget = budget
        self.max_tokens = max_tokens
        self.name = inner.name

    async def complete(self, system, messages, tools=None, purpose="", tool_choice=None) -> LLMResponse:
        meter = _current.get()
        if meter is not None:
            if meter.calls >= meter.max_calls:
                raise CallLimitExceeded(f"per-request LLM call limit ({meter.max_calls}) reached")
            meter.calls += 1
            meter.by_purpose[purpose or "other"] = meter.by_purpose.get(purpose or "other", 0) + 1

        reserved, model = 0.0, ""
        if self.budget is not None:
            model = self.inner.model_for(purpose)
            reserved = self.budget.reserve(model, _estimate_tokens(system, messages, tools), self.max_tokens)
        try:
            resp = await self.inner.complete(system, messages, tools=tools, purpose=purpose, tool_choice=tool_choice)
        except BaseException:
            if self.budget is not None:
                self.budget.release(reserved)
            raise
        if self.budget is not None:
            cost = self.budget.settle(reserved, resp.model or model, resp.usage.input_tokens, resp.usage.output_tokens)
            if meter is not None:
                meter.cost_usd += cost
        if resp.refused:
            raise LLMRefusal(f"{resp.model or purpose} declined this request")
        return resp
