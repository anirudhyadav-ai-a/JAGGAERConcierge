"""Executor — dispatches every ready task to its agent (A11, A12, A13, …) concurrently.

A task is ready when it is PENDING and all its dependencies are OK. Upstream outputs are passed
to the agent through ToolContext.upstream. Each agent call is bounded by a timeout.
"""
from __future__ import annotations

import asyncio

from ..agents import AgentRegistry
from ..audit import AuditLogger
from ..models import AgentResult, Task, TaskStatus
from ..tools import ToolContext
from .state import OrchestrationState


class Executor:
    def __init__(self, registry: AgentRegistry, audit: AuditLogger, timeout_s: float):
        self.registry = registry
        self.audit = audit
        self.timeout_s = timeout_s

    def ready(self, state: OrchestrationState) -> list[Task]:
        return [t for t in state.tasks.values()
                if t.status == TaskStatus.PENDING
                and all(state.tasks[d].status == TaskStatus.OK for d in t.depends_on)]

    async def _run_one(self, task: Task, state: OrchestrationState) -> AgentResult:
        agent = self.registry.get(task.agent_id)
        upstream = {}
        for d in task.depends_on:
            r = state.results[d]
            upstream[r.agent_id] = {"output": r.output, "data": r.data}
        ctx = ToolContext(state.customer_id, state.session_id, state.trace_id, upstream)
        task.status = TaskStatus.RUNNING
        self.audit.event(state.trace_id, state.session_id, "executor", "dispatch",
                         task_id=task.id, agent=task.agent_id, attempt=task.attempt, query=task.query)
        try:
            result = await asyncio.wait_for(agent.run(task, ctx, self.audit), timeout=self.timeout_s)
        except asyncio.TimeoutError:
            result = AgentResult(task.id, task.agent_id, TaskStatus.FAILED, error=f"timeout after {self.timeout_s}s")
        except Exception as exc:  # an agent crash must never take down the request
            result = AgentResult(task.id, task.agent_id, TaskStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        self.audit.event(state.trace_id, state.session_id, "agent", "completed",
                         task_id=task.id, agent=task.agent_id, status=result.status.value,
                         error=result.error, latency_ms=result.latency_ms,
                         tools=[i.tool for i in result.tool_calls])
        return result

    async def __call__(self, state: OrchestrationState) -> None:
        batch = self.ready(state)
        for t in batch:
            t.wave = state.waves
        state.waves += 1
        results = await asyncio.gather(*(self._run_one(t, state) for t in batch))
        for task, result in zip(batch, results):
            task.status = result.status
            state.results[task.id] = result
