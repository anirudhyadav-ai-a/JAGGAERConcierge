"""Response Collator — gathers agent results, decides whether to loop back to the
Orchestrator (pending dependants / retries) or to compose the final response for the client."""
from __future__ import annotations

import json

from ..agents import AgentRegistry
from ..audit import AuditLogger
from ..llm import LLM
from ..models import FinalResponse, TaskStatus
from .graph import END
from .state import OrchestrationState

COLLATE_SYSTEM = """You compose the final reply to a banking customer from specialist agent results.
- Answer every part of the customer's question that an agent answered, in a natural order.
- Use only facts from the agent results; keep numbers and policy citations exactly.
- If an agent failed, say briefly that part could not be completed right now.
- Be concise and friendly. No internal ids for agents or tasks."""

DETAIL_SUFFIX = """
The customer found a previous short answer insufficient and asked for more detail: give a fuller,
itemised answer that uses all relevant figures from the agent results."""


class ResponseCollator:
    def __init__(self, llm: LLM, registry: AgentRegistry, audit: AuditLogger, max_loops: int, max_attempts: int):
        self.llm = llm
        self.registry = registry
        self.audit = audit
        self.max_loops = max_loops
        self.max_attempts = max_attempts

    def needs_another_pass(self, state: OrchestrationState) -> bool:
        if state.loop >= self.max_loops:
            return False
        for t in state.tasks.values():
            if t.status == TaskStatus.PENDING:
                return True
            if t.status in (TaskStatus.FAILED, TaskStatus.INCOMPLETE) and t.attempt + 1 < self.max_attempts:
                return True
        return False

    async def __call__(self, state: OrchestrationState) -> None:
        if self.needs_another_pass(state):
            self.audit.event(state.trace_id, state.session_id, "collator", "loop_back", loop=state.loop)
            return

        ordered = [state.results.get(t.id) for t in state.tasks.values()]
        payload = []
        for t, r in zip(state.tasks.values(), ordered):
            payload.append({"agent_name": self.registry.get(t.agent_id).name,
                            "status": t.status.value,
                            "output": r.output if r else "",
                            "error": (r.error if r else "not executed")})
        ok = [p for p in payload if p["status"] == "ok"]

        if not ok:
            # nothing to combine: say so without paying for an LLM call
            names = ", ".join(p["agent_name"] for p in payload) or "the agents"
            text = f"Sorry, I couldn't complete that request right now ({names} didn't finish). Please try again shortly."
        elif len(ok) == 1 and len(payload) == 1 and not state.detail:
            text = ok[0]["output"]               # single agent: no extra LLM hop needed
        else:
            system = COLLATE_SYSTEM + (DETAIL_SUFFIX if state.detail else "")
            content = f"Customer message: {state.message}\n\nAGENT_RESULTS_JSON:{json.dumps(payload)}"
            text = (await self.llm.complete(system, [{"role": "user", "content": content}],
                                            purpose="collate")).text.strip()

        status = "ok" if len(ok) == len(payload) else ("partial" if ok else "error")
        tools_used = sorted({i.tool for r in state.results.values() for i in r.tool_calls})
        state.final = FinalResponse(
            session_id=state.session_id, trace_id=state.trace_id, status=status, response=text,
            context={
                "intents": [{"agent_id": i.agent_id, "sub_query": i.sub_query,
                             "score": state.intent_scores.get(i.agent_id)} for i in state.intents],
                "agents": [{"agent_id": t.agent_id, "name": self.registry.get(t.agent_id).name,
                            "status": t.status.value, "attempts": t.attempt + 1}
                           for t in state.tasks.values()],
                "tools_used": tools_used,
                "orchestrator_loops": state.loop,
            },
        )
        self.audit.event(state.trace_id, state.session_id, "collator", "final",
                         status=status, agents=state.final.context["agents"])

    def route(self, state: OrchestrationState) -> str:
        return END if state.final else "orchestrator"
