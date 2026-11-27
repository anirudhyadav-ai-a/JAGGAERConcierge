"""User Session box next to the Client. In-memory; swap for Redis in production."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .models import new_id


@dataclass
class Turn:
    role: str      # "user" | "assistant"
    text: str
    trace_id: str
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class Session:
    id: str
    customer_id: str
    history: list[Turn] = field(default_factory=list)
    memory: dict = field(default_factory=dict)   # cross-turn facts (e.g. last agents used)

    def recent(self, n: int) -> list[Turn]:
        return self.history[-2 * n:]


class SessionStore:
    def __init__(self):
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    def get_or_create(self, session_id: str | None, customer_id: str) -> Session:
        with self._lock:
            if session_id and session_id in self._sessions:
                s = self._sessions[session_id]
                if s.customer_id != customer_id:
                    raise PermissionError("Session does not belong to this customer")
                return s
            s = Session(id=session_id or new_id("sess"), customer_id=customer_id)
            self._sessions[s.id] = s
            return s

    def get(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)
