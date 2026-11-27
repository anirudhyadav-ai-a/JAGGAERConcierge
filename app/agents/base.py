"""Agent = system prompt + its own tool set + an LLM tool-use loop.

Each agent (A11, A12, …) is self-contained: the Executor only knows `await agent.run(task, ctx)`.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace

from ..audit import AuditLogger
from ..llm import LLM
from ..models import AgentResult, Task, TaskStatus
from ..tools import Tool, ToolContext


# Appended to every agent's prompt: fewer, focused tool calls make answers steadier and cheaper.
TOOL_USE_RULE = ("\nCall only the tools needed to answer the question you were given, and no others. "
                 "Answer only that question.")


@dataclass
class Agent:
    id: str
    name: str
    description: str
    keywords: list[str]
    system_prompt: str
    tools: list[Tool]
    sources: list[str] = field(default_factory=list)  # data sources under data/ this agent may read
    data: object | None = None             # SourceView over `sources`, injected by the registry
    llm: LLM | None = None                 # injected by the registry; an agent may use its own model
    enabled: bool = True
    max_steps: int = 4
    _tool_map: dict[str, Tool] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self):
        self._tool_map = {t.name: t for t in self.tools}

    def card(self) -> dict:
        """What the intent layer sees about this agent."""
        return {"id": self.id, "name": self.name, "description": self.description,
                "keywords": self.keywords, "tools": [t.name for t in self.tools]}

    async def run(self, task: Task, ctx: ToolContext, audit: AuditLogger) -> AgentResult:
        start = time.perf_counter()
        ctx = replace(ctx, data=self.data)   # tools see only this agent's data sources
        prompt = task.query
        if ctx.upstream:
            prompt += "\n\nUpstream results you may use:\n" + json.dumps(ctx.upstream, default=str)[:4000]
        messages: list[dict] = [{"role": "user", "content": prompt}]
        specs = [t.spec() for t in self.tools]
        invocations = []
        data: dict = {}

        for _ in range(self.max_steps):
            resp = await self.llm.complete(self.system_prompt + TOOL_USE_RULE, messages, tools=specs, purpose="agent")
            if not resp.tool_calls:
                return AgentResult(task.id, self.id, TaskStatus.OK, resp.text.strip(), data, invocations,
                                   latency_ms=int((time.perf_counter() - start) * 1000))
            messages.append(resp.as_assistant_message())
            results = []
            for call in resp.tool_calls:
                t = self._tool_map.get(call.name)
                if t is None:
                    out = {"error": f"tool {call.name} not available to {self.id}"}
                    results.append({"type": "tool_result", "tool_use_id": call.id,
                                    "content": json.dumps(out), "is_error": True})
                    continue
                inv = await t.run(call.arguments, ctx)
                invocations.append(inv)
                if inv.ok:
                    data[call.name] = inv.output
                audit.event(ctx.trace_id, ctx.session_id, "tool", "invoked", agent=self.id,
                            task_id=task.id, tool=call.name, arguments=call.arguments,
                            ok=inv.ok, latency_ms=inv.latency_ms)
                results.append({"type": "tool_result", "tool_use_id": call.id,
                                 "content": json.dumps(inv.output, default=str), "is_error": not inv.ok})
            messages.append({"role": "user", "content": results})

        return AgentResult(task.id, self.id, TaskStatus.INCOMPLETE, "", data, invocations,
                           error="tool step limit reached",
                           latency_ms=int((time.perf_counter() - start) * 1000))
