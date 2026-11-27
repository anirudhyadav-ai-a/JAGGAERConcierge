"""Tool contract. Specs are MCP-compatible (name / description / input_schema), so the same
tools can be exposed through an MCP server or passed directly to an LLM tool-use API.

Identity (customer_id, session) is injected from ToolContext by the platform — the LLM never
chooses whose data a tool reads.
"""
from __future__ import annotations

import inspect
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from ..models import ToolInvocation


@dataclass
class ToolContext:
    customer_id: str
    session_id: str
    trace_id: str
    upstream: dict[str, Any] = field(default_factory=dict)  # outputs of tasks this one depends on
    data: Any = None  # the running agent's SourceView: only the data sources that agent declares


class ToolError(Exception):
    pass


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., Any | Awaitable[Any]]
    # Arguments safe to reuse verbatim when a cached plan is replayed for a *different* question.
    # Everything else the LLM chose (merchant, txn id, category…) is dropped; `query` is re-bound.
    replay_args: tuple[str, ...] = ()

    def spec(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.input_schema}

    def _validate(self, args: dict) -> dict:
        props = self.input_schema.get("properties", {})
        unknown = set(args) - set(props)
        if unknown:
            raise ToolError(f"unknown arguments {sorted(unknown)}")
        missing = [r for r in self.input_schema.get("required", []) if r not in args]
        if missing:
            raise ToolError(f"missing required arguments {missing}")
        for key, schema in props.items():
            if key in args and "enum" in schema and args[key] not in schema["enum"]:
                raise ToolError(f"{key} must be one of {schema['enum']}")
        return args

    async def run(self, args: dict, ctx: ToolContext) -> ToolInvocation:
        start = time.perf_counter()
        try:
            clean = self._validate(dict(args or {}))
            out = self.fn(ctx=ctx, **clean)
            if inspect.isawaitable(out):
                out = await out
            ok = True
        except Exception as exc:  # tool failures are returned to the agent, not raised
            out, ok = {"error": str(exc)}, False
        return ToolInvocation(self.name, dict(args or {}), ok, out, int((time.perf_counter() - start) * 1000))


def tool(name: str, description: str, properties: dict | None = None, required: list[str] | None = None,
         replay_args: tuple[str, ...] = ()):
    """Decorator: @tool("search_transactions", "…", {"query": {"type": "string"}})

    replay_args: arguments a cached plan may reuse as they are (settings like top_k, never customer data)."""
    def wrap(fn):
        return Tool(name, description,
                    {"type": "object", "properties": properties or {}, "required": required or []}, fn,
                    replay_args=tuple(replay_args))
    return wrap
