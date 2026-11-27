from __future__ import annotations

import os

from ..config import Settings, settings as default_settings
from .base import LLM, LLMRefusal, LLMResponse, ToolCall, Usage
from .budget import BudgetExceeded, BudgetGuard, CallLimitExceeded
from .metered import MeteredLLM, start_request


def build_llm(cfg: Settings | None = None, client=None) -> MeteredLLM:
    cfg = cfg or default_settings
    if cfg.llm_provider == "anthropic":
        if client is None and not os.getenv("ANTHROPIC_API_KEY"):
            raise RuntimeError("ANTHROPIC_API_KEY is not set. Copy .env.example to .env and put your Claude API "
                               "key in it (see docs/running.md). The app makes real Claude calls; the offline "
                               "mock (LLM_PROVIDER=mock) is only for the test suite.")
        from .anthropic_llm import AnthropicLLM
        inner = AnthropicLLM({"intent": cfg.intent_model, "agent": cfg.agent_model,
                              "collate": cfg.collate_model, "default": cfg.agent_model},
                             cfg.llm_max_tokens, cfg.llm_timeout_s, client=client)
        budget = BudgetGuard(cfg.budget_usd, cfg.usage_ledger_path)
        return MeteredLLM(inner, budget, cfg.llm_max_tokens)
    from .mock import MockLLM
    return MeteredLLM(MockLLM(cfg.mock_llm_latency_ms), None, cfg.llm_max_tokens)


__all__ = ["LLM", "LLMRefusal", "LLMResponse", "ToolCall", "Usage", "BudgetExceeded", "BudgetGuard",
           "CallLimitExceeded", "MeteredLLM", "build_llm", "start_request"]
