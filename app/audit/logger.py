"""Audit | Log box. Append-only JSONL audit trail + standard logging.

Every layer writes an event keyed by trace_id so one request can be replayed end to end
(intent -> score -> plan -> each agent/tool call -> collation -> response).
Sensitive values are redacted before they are persisted.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("agent_platform")

_REDACTIONS = [
    (re.compile(r"\b\d{12,19}\b"), "[REDACTED_PAN]"),          # card / account numbers
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[REDACTED_EMAIL]"),
]


def redact(value: Any) -> Any:
    if isinstance(value, str):
        for pattern, repl in _REDACTIONS:
            value = pattern.sub(repl, value)
        return value
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    return value


class AuditLogger:
    def __init__(self, path: str | None):
        self.path = Path(path) if path else None
        self._lock = threading.Lock()
        self._by_trace: dict[str, list[dict]] = defaultdict(list)

    def event(self, trace_id: str, session_id: str, stage: str, action: str, **data: Any) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "trace_id": trace_id,
            "session_id": session_id,
            "stage": stage,          # client | intent | intent_score | orchestrator | executor | agent | tool | collator
            "action": action,
            "data": redact(data),
        }
        with self._lock:
            self._by_trace[trace_id].append(record)
            if self.path:
                with self.path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
        log.info("%s %s.%s %s", trace_id, stage, action, json.dumps(record["data"], default=str)[:400])

    def trace(self, trace_id: str) -> list[dict]:
        return list(self._by_trace.get(trace_id, []))
