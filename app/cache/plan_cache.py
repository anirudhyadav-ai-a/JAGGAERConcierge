"""Plan cache: stores which agent runs which tool (with slot arguments, in which stage), never an answer.

Lookup rules (all must hold, otherwise it's a miss):
  - the same set of agents as the router chose for this question;
  - every slot the plan needs is present in this question (a missing slot is a miss, never a guess);
  - cosine similarity of the slot-masked questions >= CACHE_SIMILARITY;
  - younger than CACHE_TTL_S, built against the current tool catalog, fewer than CACHE_MAX_NEGATIVE
    "more details" requests against it.

A plan is written only after a full run that ended with status ok, from the tools that actually ran. An argument
becomes a slot if its value came from the question; `query` is re-bound to the agent's sub-query; a tool's
`replay_args` (e.g. top_k) are kept as they are. Any other value (say, an id read from a previous tool's output)
would tie the plan to one customer's data, so such a run is not cached at all.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .embedder import Embedder, HashingEmbedder, cosine

SUB_QUERY = "{sub_query}"


@dataclass
class PlanStep:
    agent_id: str
    tool: str
    arguments: dict[str, Any]   # values are slots like "{category}" / "{sub_query}", or replay-safe literals
    stage: int                  # steps in the same stage run in parallel; later stages get upstream outputs


@dataclass
class PlanEntry:
    id: str
    agents: list[str]                       # sorted agent ids
    depends_on: dict[str, list[str]]        # agent -> agents it needs first
    slots: list[str]                        # slot names the plan needs from the question
    steps: list[PlanStep]
    question: str                           # slot-masked, redacted question (no customer data)
    embedding: list[float]
    catalog_version: str
    created_at: float
    first_run: dict[str, Any] = field(default_factory=dict)   # llm_calls, latency_ms, cost_usd of the run that built it
    hits: int = 0
    negative: int = 0
    last_hit_at: float | None = None

    @staticmethod
    def from_dict(d: dict) -> "PlanEntry":
        d = dict(d)
        d["steps"] = [PlanStep(**s) for s in d["steps"]]
        return PlanEntry(**d)

    def summary(self) -> dict:
        """What GET /v1/cache shows: the question-to-tool mapping, without the embedding."""
        out = asdict(self)
        out.pop("embedding")
        return out


class PlanStore(Protocol):
    def all(self) -> list[PlanEntry]: ...
    def get(self, plan_id: str) -> PlanEntry | None: ...
    def put(self, entry: PlanEntry) -> None: ...
    def delete(self, plan_id: str) -> None: ...
    def clear(self) -> None: ...


class InMemoryPlanStore:
    def __init__(self):
        self._entries: dict[str, PlanEntry] = {}
        self._lock = threading.Lock()

    def all(self) -> list[PlanEntry]:
        with self._lock:
            return list(self._entries.values())

    def get(self, plan_id: str) -> PlanEntry | None:
        return self._entries.get(plan_id)

    def put(self, entry: PlanEntry) -> None:
        with self._lock:
            self._entries[entry.id] = entry
            self._saved()

    def delete(self, plan_id: str) -> None:
        with self._lock:
            self._entries.pop(plan_id, None)
            self._saved()

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._saved()

    def _saved(self) -> None:   # hook for persistent stores
        pass


class JsonFilePlanStore(InMemoryPlanStore):
    """Plans persisted to one JSON file, so they survive restarts. Redis (with a vector index) is the production
    implementation of the same interface."""

    def __init__(self, path: str | Path):
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            try:
                for d in json.loads(self.path.read_text()):
                    e = PlanEntry.from_dict(d)
                    self._entries[e.id] = e
            except (ValueError, TypeError, KeyError):
                self._entries = {}   # unreadable cache: start empty rather than fail start-up

    def _saved(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps([asdict(e) for e in self._entries.values()])
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".plans-")
        with os.fdopen(fd, "w") as f:
            f.write(data)
        os.replace(tmp, self.path)   # atomic: a crash never leaves half a file


@dataclass
class CacheHit:
    entry: PlanEntry
    similarity: float


@dataclass
class Lookup:
    hit: CacheHit | None
    reason: str          # why it missed, or "hit"
    best_similarity: float | None = None


def catalog_version(agents) -> str:
    """Hash of every agent's tool specs: a plan built against other tools is never replayed."""
    spec = [[a.id, [t.spec() for t in a.tools]] for a in sorted(agents, key=lambda a: a.id)]
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()[:16]


class PlanCache:
    def __init__(self, store: PlanStore, version: str, similarity: float = 0.80, ttl_s: float = 86400,
                 max_negative: int = 2, embedder: Embedder | None = None, clock=time.time):
        self.store = store
        self.version = version
        self.similarity = similarity
        self.ttl_s = ttl_s
        self.max_negative = max_negative
        self.embedder = embedder or HashingEmbedder()
        self.clock = clock

    # ---- read ----
    def lookup(self, agents: list[str], entities: dict[str, Any], masked_question: str) -> Lookup:
        wanted = sorted(set(agents))
        now = self.clock()
        candidates = [e for e in self.store.all() if e.agents == wanted]
        if not candidates:
            return Lookup(None, "no plan for this set of agents")
        usable, reasons = [], []
        for e in candidates:
            if e.catalog_version != self.version:
                reasons.append("tools changed since the plan was built")
            elif now - e.created_at > self.ttl_s:
                reasons.append("plan expired")
            elif e.negative >= self.max_negative:
                reasons.append("plan got too many 'more details' requests")
            elif missing := [s for s in e.slots if s not in entities]:
                reasons.append(f"question has no {', '.join(missing)}")
            else:
                usable.append(e)
        if not usable:
            return Lookup(None, reasons[0])
        q = self.embedder.embed(masked_question)
        best, score = max(((e, cosine(q, e.embedding)) for e in usable), key=lambda x: x[1])
        if score < self.similarity:
            return Lookup(None, f"closest plan is {score:.2f} similar, below {self.similarity:.2f}", round(score, 3))
        return Lookup(CacheHit(best, round(score, 3)), "hit", round(score, 3))

    def record_hit(self, entry: PlanEntry) -> None:
        entry.hits += 1
        entry.last_hit_at = self.clock()
        self.store.put(entry)

    def add_negative(self, plan_id: str) -> PlanEntry | None:
        e = self.store.get(plan_id)
        if e:
            e.negative += 1
            self.store.put(e)
        return e

    # ---- write ----
    def build(self, *, agents: list[str], depends_on: dict[str, list[str]], invocations: list[tuple[str, int, Any]],
              tools: dict[tuple[str, str], Any], entities: dict[str, Any], masked_question: str,
              first_run: dict[str, Any]) -> tuple[PlanEntry | None, str]:
        """invocations: (agent_id, stage, ToolInvocation) in the order they ran. Returns (entry, reason)."""
        steps, slots = [], set()
        for agent_id, stage, inv in invocations:
            if not inv.ok:
                continue
            tool = tools.get((agent_id, inv.tool))
            if tool is None:
                return None, f"{inv.tool} is not a tool of {agent_id}"
            args = {}
            for k, v in (inv.arguments or {}).items():
                slot = _slot_for(k, v, entities)
                if slot:
                    args[k] = slot
                    if slot != SUB_QUERY:
                        slots.add(slot.strip("{}"))
                elif k in tool.replay_args:
                    args[k] = v
                else:
                    return None, f"{inv.tool}({k}={v!r}) isn't from the question, so the plan would not be reusable"
            steps.append(PlanStep(agent_id, inv.tool, args, stage))
        if not steps:
            return None, "no tool ran successfully"
        signature = json.dumps([sorted(set(agents)), [asdict(s) for s in steps], masked_question], sort_keys=True)
        entry = PlanEntry(
            id="plan_" + hashlib.sha256(signature.encode()).hexdigest()[:12],
            agents=sorted(set(agents)), depends_on=depends_on, slots=sorted(slots), steps=steps,
            question=masked_question, embedding=self.embedder.embed(masked_question),
            catalog_version=self.version, created_at=self.clock(), first_run=first_run)
        return entry, "ok"

    def save(self, entry: PlanEntry, replaces: str | None = None) -> None:
        """Store a plan. A plan rebuilt after 'more details' replaces the old one and keeps its negative count."""
        old = self.store.get(replaces) if replaces else self.store.get(entry.id)
        if old:
            entry.negative = max(entry.negative, old.negative)
            if replaces and replaces != entry.id:
                self.store.delete(replaces)
        self.store.put(entry)


def _slot_for(key: str, value: Any, entities: dict[str, Any]) -> str | None:
    if key == "query":
        return SUB_QUERY
    for name, ent in entities.items():
        if isinstance(ent, (int, float)) and not isinstance(ent, bool):
            try:
                if float(value) == float(ent):
                    return "{" + name + "}"
            except (TypeError, ValueError):
                continue
        elif isinstance(value, str) and value.strip().lower() == str(ent).lower():
            return "{" + name + "}"
    return None


def fill(arguments: dict[str, Any], entities: dict[str, Any], sub_query: str) -> dict[str, Any]:
    """A plan step's arguments for this question: slots replaced by this question's values."""
    out = {}
    for k, v in arguments.items():
        if v == SUB_QUERY:
            out[k] = sub_query
        elif isinstance(v, str) and v.startswith("{") and v.endswith("}"):
            out[k] = entities[v[1:-1]]
        else:
            out[k] = v
    return out


def build_store(path: str | None) -> PlanStore:
    return JsonFilePlanStore(path) if path else InMemoryPlanStore()
