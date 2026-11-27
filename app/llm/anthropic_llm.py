"""Claude provider (Anthropic Messages API).

Model rules this class handles for you (from the Claude API docs):
  - Claude Sonnet 5.5 runs adaptive thinking by default, rejects thinking={"type":"disabled"} and forced
    tool choice ("tool" and "any"); {"type":"between_tools"} turns off up-front thinking, and a forced
    choice becomes "auto" (the system prompt already tells the model which tool to call).
  - Claude Haiku 5.5 accepts thinking={"type":"disabled"} and forced tool choice.
  - Thinking blocks must be passed back unchanged in tool loops -> we keep raw content blocks.
  - A response with stop_reason "refusal" means Claude declined the request: flagged as `refused`,
    and MeteredLLM raises LLMRefusal once the (billed) tokens are recorded.
If the API still rejects a thinking/tool_choice setting, the call is retried once without it.
"""
from __future__ import annotations

import logging

from .base import LLMResponse, ToolCall, Usage

log = logging.getLogger("agent_platform.llm")

_BETWEEN_TOOLS = ("claude-sonnet-5-5",)
_DISABLE_OK = ("claude-haiku-5-5", "claude-sonnet-5", "claude-opus-5")
_NO_FORCED_TOOL = ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable", "claude-mythos")


def thinking_for(model: str) -> dict | None:
    """Lowest-thinking setting per known model id; unknown models get the API default."""
    if model in _BETWEEN_TOOLS:
        return {"type": "between_tools"}
    if model in _DISABLE_OK:
        return {"type": "disabled"}
    return None


def supports_forced_tool(model: str) -> bool:
    return not any(model.startswith(p) for p in _NO_FORCED_TOOL)


class AnthropicLLM:
    name = "anthropic"

    def __init__(self, models: dict[str, str], max_tokens: int = 2048, timeout_s: float = 60, client=None):
        if client is None:
            from anthropic import AsyncAnthropic  # lazy: mock mode needs no SDK
            client = AsyncAnthropic(timeout=timeout_s, max_retries=2)
        self.client = client
        self.models = models          # purpose -> model id
        self.max_tokens = max_tokens

    def model_for(self, purpose: str) -> str:
        return self.models.get(purpose) or self.models["default"]

    async def complete(self, system, messages, tools=None, purpose="", tool_choice=None):
        model = self.model_for(purpose)
        kwargs = dict(model=model, max_tokens=self.max_tokens, system=system, messages=messages)
        if tools:
            kwargs["tools"] = tools
        if tool_choice and tool_choice.get("type") == "tool" and not supports_forced_tool(model):
            tool_choice = {"type": "auto"}
        if tool_choice and tools:
            kwargs["tool_choice"] = tool_choice
        thinking = thinking_for(model)
        if thinking:
            kwargs["thinking"] = thinking

        try:
            resp = await self.client.messages.create(**kwargs)
        except Exception as exc:
            if type(exc).__name__ == "BadRequestError" and ("thinking" in str(exc) or "tool_choice" in str(exc)):
                log.warning("retrying %s without thinking/tool_choice overrides: %s", model, exc)
                kwargs.pop("thinking", None)
                if kwargs.get("tool_choice", {}).get("type") in ("tool", "any"):
                    kwargs["tool_choice"] = {"type": "auto"}
                resp = await self.client.messages.create(**kwargs)
            else:
                raise

        raw = [b.model_dump(exclude_none=True) for b in resp.content]
        text = "".join(b.text for b in resp.content if b.type == "text")
        calls = [ToolCall(b.id, b.name, dict(b.input)) for b in resp.content if b.type == "tool_use"]
        usage = Usage(getattr(resp.usage, "input_tokens", 0) or 0, getattr(resp.usage, "output_tokens", 0) or 0)
        return LLMResponse(text=text, tool_calls=calls, usage=usage, model=model, raw_content=raw,
                           refused=getattr(resp, "stop_reason", None) == "refusal")
