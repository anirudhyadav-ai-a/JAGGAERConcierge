"""Provider-neutral LLM interface.

Messages use the Anthropic Messages format as the canonical shape:
  {"role": "user", "content": "text" | [blocks]}
  {"role": "assistant", "content": [{"type": "text"...}, {"type": "tool_use", ...}]}
Tool specs use the MCP / Anthropic shape: {"name", "description", "input_schema"}.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0


class LLMRefusal(RuntimeError):
    """The model declined to answer. Raised by MeteredLLM after the call's cost is recorded."""


@dataclass
class LLMResponse:
    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    # Exact content blocks as returned by the provider (incl. signed thinking blocks).
    # Claude requires these to be sent back unchanged in tool-use loops.
    raw_content: list[dict] | None = None
    refused: bool = False   # the model declined the request (Claude stop_reason "refusal")

    def as_assistant_message(self) -> dict:
        if self.raw_content is not None:
            return {"role": "assistant", "content": self.raw_content}
        blocks: list[dict] = []
        if self.text:
            blocks.append({"type": "text", "text": self.text})
        for c in self.tool_calls:
            blocks.append({"type": "tool_use", "id": c.id, "name": c.name, "input": c.arguments})
        return {"role": "assistant", "content": blocks}


class LLM(Protocol):
    name: str

    async def complete(
        self,
        system: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        purpose: str = "",
        tool_choice: dict | None = None,
    ) -> LLMResponse: ...
