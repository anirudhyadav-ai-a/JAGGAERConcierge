"""Fast path: answer from a cached plan.

The plan says which agent runs which tool with which slot arguments, stage by stage. The tools run live for
this customer (through each agent's own data view, so isolation still holds), then one collate call writes
the answer. No intent scoring, orchestrator, executor or agent LLM loop. If any tool fails, the caller falls
back to the full graph within the same request.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from ..agents import AgentRegistry
from ..audit import AuditLogger
from ..llm import LLM
from ..models import FinalResponse, Intent
from ..orchestrator.collator import COLLATE_SYSTEM
from ..tools import ToolContext
from .plan_cache import CacheHit, fill

FAST_PATH_SUFFIX = """
These results come straight from each agent's tools (a cached plan ran them for this question), together with
the instructions that agent follows. Apply those instructions when you write the reply."""


class ToolFailed(Exception):
    pass


class FastPath:
    def __init__(self, llm: LLM, registry: AgentRegistry, audit: AuditLogger):
        self.llm = llm
        self.registry = registry
        self.audit = audit

    async def run(self, hit: CacheHit, intents: list[Intent], entities: dict[str, Any], message: str,
                  customer_id: str, session_id: str, trace_id: str) -> FinalResponse | None:
        plan = hit.entry
        sub_queries = {i.agent_id: i.sub_query for i in intents}
        outputs: dict[str, dict[str, Any]] = {a: {} for a in plan.agents}   # agent -> tool -> output
        tools_used: list[str] = []

        async def run_agent(agent_id: str, steps) -> None:
            agent = self.registry.get(agent_id)
            upstream = {d: {"output": _summaries(outputs[d]), "data": outputs[d]}
                        for d in plan.depends_on.get(agent_id, [])}
            ctx = ToolContext(customer_id, session_id, trace_id, upstream, data=agent.data)
            for step in steps:
                tool = agent._tool_map.get(step.tool)
                if tool is None:
                    raise ToolFailed(f"{step.tool} is not a tool of {agent_id}")
                args = fill(step.arguments, entities, sub_queries.get(agent_id, message))
                inv = await tool.run(args, ctx)
                self.audit.event(trace_id, session_id, "tool", "invoked", agent=agent_id, task_id="cache",
                                 tool=step.tool, arguments=args, ok=inv.ok, latency_ms=inv.latency_ms)
                if not inv.ok:
                    raise ToolFailed(f"{agent_id}.{step.tool}: {inv.output.get('error')}")
                outputs[agent_id][step.tool] = inv.output
                tools_used.append(step.tool)

        try:
            for stage in sorted({s.stage for s in plan.steps}):
                by_agent: dict[str, list] = {}
                for s in plan.steps:
                    if s.stage == stage:
                        by_agent.setdefault(s.agent_id, []).append(s)
                await asyncio.gather(*(run_agent(a, steps) for a, steps in by_agent.items()))
        except ToolFailed as exc:
            self.audit.event(trace_id, session_id, "cache", "fallback", plan_id=plan.id, reason=str(exc))
            return None

        payload = [{"agent_name": self.registry.get(a).name,
                    "instructions": self.registry.get(a).system_prompt,
                    "status": "ok",
                    "output": _summaries(outputs[a]),
                    "tool_results": outputs[a]} for a in plan.agents]
        content = f"Customer message: {message}\n\nAGENT_RESULTS_JSON:{json.dumps(payload, default=str)}"
        resp = await self.llm.complete(COLLATE_SYSTEM + FAST_PATH_SUFFIX, [{"role": "user", "content": content}],
                                       purpose="collate")
        return FinalResponse(
            session_id=session_id, trace_id=trace_id, status="ok", response=resp.text.strip(), path="cache",
            context={
                "intents": [{"agent_id": i.agent_id, "sub_query": i.sub_query} for i in intents],
                "agents": [{"agent_id": a, "name": self.registry.get(a).name, "status": "ok", "attempts": 1}
                           for a in plan.agents],
                "tools_used": sorted(set(tools_used)),
                "orchestrator_loops": 0,
            },
        )


def _summaries(tool_outputs: dict[str, Any]) -> str:
    return " ".join(str(o.get("summary", "")) for o in tool_outputs.values() if isinstance(o, dict)).strip()
