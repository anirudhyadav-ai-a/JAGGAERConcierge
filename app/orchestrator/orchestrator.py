"""Orchestrator (graph) — box 2.

First pass: turns accepted intents into a task DAG (one task per agent, dependencies mapped).
Later passes (looped back from the Response Collator): schedules retries for failed/incomplete
tasks and skips tasks whose dependencies can no longer succeed.
"""
from __future__ import annotations

from ..audit import AuditLogger
from ..models import Task, TaskStatus, new_id
from .state import OrchestrationState

_DONE_BAD = {TaskStatus.FAILED, TaskStatus.INCOMPLETE, TaskStatus.SKIPPED}


class Orchestrator:
    def __init__(self, audit: AuditLogger, max_task_attempts: int):
        self.audit = audit
        self.max_task_attempts = max_task_attempts

    def _plan(self, state: OrchestrationState) -> None:
        by_agent: dict[str, str] = {}
        for intent in state.intents:
            tid = new_id("task")
            by_agent[intent.agent_id] = tid
            state.tasks[tid] = Task(tid, intent.agent_id, intent.sub_query)
        for intent in state.intents:
            task = state.tasks[by_agent[intent.agent_id]]
            task.depends_on = [by_agent[d] for d in intent.depends_on if d in by_agent and d != intent.agent_id]
        self.audit.event(state.trace_id, state.session_id, "orchestrator", "planned",
                         tasks=[{"id": t.id, "agent": t.agent_id, "depends_on": t.depends_on}
                                for t in state.tasks.values()])

    def _replan(self, state: OrchestrationState) -> None:
        retried, skipped = [], []
        for t in state.tasks.values():
            if t.status in (TaskStatus.FAILED, TaskStatus.INCOMPLETE) and t.attempt + 1 < self.max_task_attempts:
                t.attempt += 1
                t.status = TaskStatus.PENDING
                retried.append(t.id)
        # skip pending tasks blocked by permanently failed dependencies
        changed = True
        while changed:
            changed = False
            for t in state.tasks.values():
                if t.status == TaskStatus.PENDING and any(state.tasks[d].status in _DONE_BAD for d in t.depends_on):
                    t.status = TaskStatus.SKIPPED
                    skipped.append(t.id)
                    changed = True
        self.audit.event(state.trace_id, state.session_id, "orchestrator", "replanned",
                         loop=state.loop, retried=retried, skipped=skipped)

    async def __call__(self, state: OrchestrationState) -> None:
        if not state.tasks:
            self._plan(state)
        else:
            self._replan(state)
        state.loop += 1
