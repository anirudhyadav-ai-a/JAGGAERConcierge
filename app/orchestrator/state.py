from __future__ import annotations

from dataclasses import dataclass, field

from ..models import AgentResult, FinalResponse, Intent, Task


@dataclass
class OrchestrationState:
    trace_id: str
    session_id: str
    customer_id: str
    message: str
    intents: list[Intent]
    intent_scores: dict[str, float] = field(default_factory=dict)
    tasks: dict[str, Task] = field(default_factory=dict)
    results: dict[str, AgentResult] = field(default_factory=dict)   # task_id -> latest result
    loop: int = 0
    waves: int = 0
    detail: bool = False          # customer asked for more detail -> fuller answer from the collator
    final: FinalResponse | None = None
