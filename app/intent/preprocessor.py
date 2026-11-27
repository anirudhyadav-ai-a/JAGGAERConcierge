"""Intent Pre-processing (box 1, top).

Inputs:  system instructions + content (agent catalog, session history) + user message
Process: normalise -> mask PII before it reaches the LLM -> LLM routes through the `route_intents` tool
Output:  IntentResult: one intent per agent (sub-query, confidence, dependencies) + an ambiguity flag

Using a tool with a JSON schema (instead of free text) makes the router's output structured and
validated by the API, which is what keeps routing robust rather than "parse whatever came back".
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from ..audit import AuditLogger, redact
from ..llm import LLM
from ..models import Intent
from ..session import Session

SYSTEM_INSTRUCTIONS = """You are the intent router for a retail-banking assistant.
Your only job is to decide which specialist agents must answer the customer's message. Never answer it yourself.
Always respond by calling the route_intents tool exactly once.

Rules:
- Use only agent ids from the catalog below. One intent per agent; merge everything for that agent into one sub_query.
- sub_query: a self-contained rewrite of the part of the message for that agent, resolving pronouns from history.
- confidence (0..1): how sure you are that this agent is the right one and can answer the sub_query.
- depends_on: agent ids whose answer this intent needs first (e.g. a card recommendation that should use the
  customer's spending analysis). Leave empty when independent.
- ambiguous: true when the message could reasonably mean different agents and you cannot tell which
  (for example "what about my card?" could be a card recommendation, card transactions or card policy).
  Then list the candidate agents with confidence below 0.5 and write a short clarifying_question that offers
  those options in plain words. Do not guess.
- A message unrelated to banking: return an empty intents list and ambiguous=false.
"""

ROUTE_TOOL = {
    "name": "route_intents",
    "description": "Report which specialist agents should handle the customer's message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "intents": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "agent_id": {"type": "string"},
                        "sub_query": {"type": "string"},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "depends_on": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                    },
                    "required": ["agent_id", "sub_query", "confidence"],
                },
            },
            "ambiguous": {"type": "boolean"},
            "clarifying_question": {"type": "string"},
        },
        "required": ["intents", "ambiguous"],
    },
}


@dataclass
class IntentResult:
    intents: list[Intent] = field(default_factory=list)
    ambiguous: bool = False
    clarifying_question: str = ""


def _extract_json(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.S)
    if not match:
        return {}
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}


class IntentPreprocessor:
    def __init__(self, llm: LLM, catalog: list[dict], audit: AuditLogger, history_turns: int = 4):
        self.llm = llm
        self.catalog = catalog
        self.audit = audit
        self.history_turns = history_turns

    def _system_prompt(self, allowed: list[str] | None = None) -> str:
        catalog = [a for a in self.catalog if allowed is None or a["id"] in allowed]
        extra = ("\nThe customer has already chosen the agents below. Split the message between ONLY these agents. "
                 "Do not mark it ambiguous.\n") if allowed is not None else ""
        return SYSTEM_INSTRUCTIONS + extra + "\nAGENT_CATALOG_JSON:" + json.dumps(catalog) + "\nEND_CATALOG"

    def _content(self, message: str, session: Session) -> str:
        history = "\n".join(f"{t.role}: {t.text}" for t in session.recent(self.history_turns))
        clean = redact(" ".join(message.split()))  # normalise whitespace + mask PII before the LLM sees it
        return f"{clean}\n\nConversation history:\n{history}" if history else clean

    async def run(self, message: str, session: Session, trace_id: str,
                  allowed: list[str] | None = None) -> IntentResult:
        resp = await self.llm.complete(
            self._system_prompt(allowed), [{"role": "user", "content": self._content(message, session)}],
            tools=[ROUTE_TOOL], tool_choice={"type": "tool", "name": "route_intents"}, purpose="intent",
        )
        call = next((c for c in resp.tool_calls if c.name == "route_intents"), None)
        parsed = call.arguments if call else _extract_json(resp.text)

        result = IntentResult(ambiguous=bool(parsed.get("ambiguous")),
                              clarifying_question=str(parsed.get("clarifying_question") or ""))
        for raw in parsed.get("intents") or []:
            try:
                result.intents.append(Intent(
                    agent_id=str(raw["agent_id"]),
                    sub_query=str(raw.get("sub_query", "")).strip(),
                    confidence=float(raw.get("confidence", 0)),
                    depends_on=[str(d) for d in raw.get("depends_on") or []],
                    rationale=str(raw.get("rationale", "")),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        self.audit.event(trace_id, session.id, "intent", "classified", llm=self.llm.name,
                         ambiguous=result.ambiguous, intents=[i.__dict__ for i in result.intents])
        return result
