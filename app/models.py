"""Shared data contracts that flow between the layers on the whiteboard."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------- Intent layer (box 1) ----------
@dataclass
class Intent:
    agent_id: str          # which agent should handle it (A11..A15)
    sub_query: str         # the slice of the user message for that agent
    confidence: float      # LLM-reported confidence
    depends_on: list[str] = field(default_factory=list)  # other agent_ids it needs first
    rationale: str = ""


@dataclass
class ScoredIntent:
    intent: Intent
    score: float
    accepted: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class IntentDecision:
    """Output of Intent Eval/Score: either route to orchestrator or answer the client directly."""
    route: str                                # "orchestrate" | "clarify" | "out_of_scope"
    scored: list[ScoredIntent]
    message: str = ""                         # clarification text when not orchestrating

    @property
    def accepted(self) -> list[Intent]:
        return [s.intent for s in self.scored if s.accepted]


# ---------- Orchestration layer (box 2/3) ----------
class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    OK = "ok"
    FAILED = "failed"
    INCOMPLETE = "incomplete"
    SKIPPED = "skipped"


@dataclass
class Task:
    id: str
    agent_id: str
    query: str
    depends_on: list[str] = field(default_factory=list)   # task ids
    attempt: int = 0
    status: TaskStatus = TaskStatus.PENDING
    wave: int = 0            # executor wave it last ran in (becomes the cached plan's stage)


@dataclass
class ToolInvocation:
    tool: str
    arguments: dict[str, Any]
    ok: bool
    output: Any
    latency_ms: int


@dataclass
class AgentResult:
    task_id: str
    agent_id: str
    status: TaskStatus
    output: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolInvocation] = field(default_factory=list)
    error: str = ""
    latency_ms: int = 0


@dataclass
class FinalResponse:
    """Right-hand box on the board: Context / ... / Response."""
    session_id: str
    trace_id: str
    status: str                    # "ok" | "partial" | "clarify" | "out_of_scope" | "error"
    response: str
    context: dict[str, Any] = field(default_factory=dict)
    path: str = "full"             # "cache" (fast path) | "full" (orchestration graph) | "intent" (gated)
