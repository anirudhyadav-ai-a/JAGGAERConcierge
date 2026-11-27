"""A stand-in for anthropic.AsyncAnthropic used in tests.

It returns real SDK `Message` objects (with signed thinking blocks and a usage block), so the real
AnthropicLLM request-building, thinking replay, parsing and cost accounting are exercised end to end.
Behaviour is borrowed from the deterministic MockLLM, and it validates each request against the
model rules the real API enforces (so a bad request fails the test the way it would fail live).
"""
from __future__ import annotations

import asyncio

from anthropic.types import Message, TextBlock, ThinkingBlock, ToolUseBlock, Usage

from app.llm.mock import MockLLM


class FakeBadRequest(Exception):
    pass


FakeBadRequest.__name__ = "BadRequestError"


class _Messages:
    def __init__(self, owner):
        self.owner = owner

    async def create(self, **kw):
        self.owner.requests.append(kw)
        model, thinking, choice = kw["model"], kw.get("thinking"), kw.get("tool_choice")
        # Rules from the Claude docs that the live API enforces with a 400:
        if model == "claude-sonnet-5-5" and thinking and thinking.get("type") == "disabled":
            raise FakeBadRequest("thinking.type disabled is not supported; use between_tools")
        if model == "claude-sonnet-5-5" and choice and choice.get("type") == "tool":
            raise FakeBadRequest("forced tool_choice is not supported on this model")
        # Thinking blocks must come back unchanged in tool loops:
        for m in kw["messages"]:
            if m["role"] == "assistant" and any(b.get("type") == "tool_use" for b in m["content"]):
                if not any(b.get("type") == "thinking" and b.get("signature") == "sig-test" for b in m["content"]):
                    raise FakeBadRequest("assistant turn is missing its thinking block")

        tools = kw.get("tools") or []
        purpose = "intent" if any(t["name"] == "route_intents" for t in tools) else ("agent" if tools else "collate")
        await asyncio.sleep(0)
        r = await self.owner.brain.complete(kw["system"], kw["messages"], tools=tools, purpose=purpose)
        content = [ThinkingBlock(type="thinking", thinking="(reasoning)", signature="sig-test")]
        content += [ToolUseBlock(type="tool_use", id=c.id, name=c.name, input=c.arguments) for c in r.tool_calls]
        if r.text:
            content.append(TextBlock(type="text", text=r.text))
        return Message(id="msg_test", type="message", role="assistant", model=model, content=content,
                       stop_reason="tool_use" if r.tool_calls else "end_turn", stop_sequence=None,
                       usage=Usage(input_tokens=self.owner.input_tokens, output_tokens=self.owner.output_tokens))


class FakeAsyncAnthropic:
    def __init__(self, input_tokens: int = 1200, output_tokens: int = 300):
        self.requests: list[dict] = []
        self.brain = MockLLM()
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.messages = _Messages(self)
