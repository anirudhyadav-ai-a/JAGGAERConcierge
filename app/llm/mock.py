"""Deterministic offline LLM, so the whole pipeline runs and is testable without an API key.

It mimics what Claude does at each step, using simple rules:
  - "intent":  keyword-scores each clause against the agent catalog and answers through the same
               `route_intents` tool Claude uses. A tie between agents -> ambiguous -> clarify.
  - "agent":   picks the tool whose description best matches the sub-query, fills enum arguments
               found in the text, chains a second tool when its name is mentioned, then summarises.
  - "collate": stitches agent outputs together.
"""
from __future__ import annotations

import json
import re
import uuid

from .base import LLMResponse, ToolCall

_STOP = {"what", "whats", "my", "the", "and", "for", "with", "show", "me", "is", "are", "a", "an", "of", "to",
         "in", "on", "i", "do", "can", "you", "please", "about", "how", "much", "did", "this", "that", "there",
         "use", "get", "where", "why", "which", "should"}
_GENERIC_NAME = {"get", "search", "status", "list"}


def _stems(text: str) -> set[str]:
    return {w[:5] for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in _STOP and len(w) > 2}


def _split_clauses(text: str) -> list[str]:
    parts = re.split(r"\?|;|\.\s|\balso\b|\band then\b|\band\b|,", text, flags=re.I)
    return [p.strip() for p in parts if p and p.strip()]


def _tid() -> str:
    return f"toolu_{uuid.uuid4().hex[:10]}"


class MockLLM:
    name = "mock"

    def model_for(self, purpose: str) -> str:
        return "mock"

    async def complete(self, system, messages, tools=None, purpose="", tool_choice=None):
        if purpose == "intent":
            return self._intent(system, messages)
        if purpose == "collate":
            return self._collate(messages)
        return self._agent(messages, tools or [])

    # ---- intent routing ----
    def _intent(self, system: str, messages: list[dict]) -> LLMResponse:
        catalog = json.loads(system.split("AGENT_CATALOG_JSON:", 1)[1].split("\nEND_CATALOG", 1)[0])
        user_text = messages[-1]["content"].split("\n\nConversation history:", 1)[0]
        by_agent: dict[str, dict] = {}
        tied: list[str] = []
        for clause in _split_clauses(user_text):
            low = clause.lower()
            scores = {a["id"]: sum(1 for k in a["keywords"] if re.search(rf"\b{re.escape(k)}\b", low))
                      for a in catalog}
            best = max(scores.values(), default=0)
            if best == 0:
                continue
            winners = [a for a, s in scores.items() if s == best]
            if len(winners) > 1:
                tied += [w for w in winners if w not in tied]
                continue
            aid = winners[0]
            conf = min(0.95, 0.5 + 0.2 * best)
            if aid in by_agent:
                by_agent[aid]["sub_query"] += f"; {clause}"
                by_agent[aid]["confidence"] = max(by_agent[aid]["confidence"], conf)
            else:
                by_agent[aid] = {"agent_id": aid, "sub_query": clause, "confidence": conf,
                                 "depends_on": [], "rationale": f"{best} keyword hit(s)"}
        if "A11" in by_agent and "A14" in by_agent:   # card advice benefits from spending analysis
            by_agent["A11"]["depends_on"] = ["A14"]
        payload = {"intents": list(by_agent.values()), "ambiguous": False, "clarifying_question": ""}
        if tied and not by_agent:
            names = {a["id"]: a["name"] for a in catalog}
            payload = {
                "intents": [{"agent_id": t, "sub_query": user_text, "confidence": 0.45, "depends_on": [],
                             "rationale": "tied with another agent"} for t in tied],
                "ambiguous": True,
                "clarifying_question": "Happy to help. Do you mean " + ", ".join(names[t] for t in tied[:-1])
                                       + f" or {names[tied[-1]]}?",
            }
        return LLMResponse(tool_calls=[ToolCall(_tid(), "route_intents", payload)])

    # ---- agent tool loop ----
    @staticmethod
    def _args(tool: dict, query: str, seen_text: str) -> dict:
        props = tool["input_schema"].get("properties", {})
        args: dict = {}
        low = query.lower()
        for name, schema in props.items():
            if name == "query":
                args["query"] = query
            elif "enum" in schema:
                hit = next((v for v in schema["enum"] if re.search(rf"\b{re.escape(v)}\b", low)), None)
                if hit:
                    args[name] = hit
            elif name == "transaction_id":
                m = re.search(r"\bT\d{3,}\b", query) or re.search(r'"id": "(T\d+)"[^}]*"status": "disputed"', seen_text)
                if m:
                    args[name] = m.group(1) if m.groups() else m.group(0)
        return args

    def _agent(self, messages: list[dict], tools: list[dict]) -> LLMResponse:
        if not tools:
            return LLMResponse(text="No tools available for this request.")
        query = messages[0]["content"].split("\n\n")[0]
        q = _stems(query)
        used = [b["name"] for m in messages if m["role"] == "assistant" for b in m["content"]
                if b.get("type") == "tool_use"]
        results = [b for m in messages if m["role"] == "user" and isinstance(m["content"], list)
                   for b in m["content"] if b.get("type") == "tool_result"]
        seen_text = " ".join(str(b.get("content")) for b in results)

        if not used:
            tool = max(tools, key=lambda t: len(q & _stems(t["name"].replace("_", " ") + " " + t["description"])))
            return LLMResponse(tool_calls=[ToolCall(_tid(), tool["name"], self._args(tool, query, seen_text))])

        # chain another tool only when the question names what it does (e.g. "dispute" -> get_dispute_status)
        for t in tools:
            name_stems = _stems(" ".join(w for w in t["name"].split("_") if w not in _GENERIC_NAME))
            if t["name"] not in used and name_stems & q:
                return LLMResponse(tool_calls=[ToolCall(_tid(), t["name"], self._args(t, query, seen_text))])

        summaries = []
        for b in results:
            try:
                payload = json.loads(b["content"])
                summaries.append(payload.get("summary") or payload.get("error") or json.dumps(payload)[:300])
            except (ValueError, TypeError):
                summaries.append(str(b.get("content"))[:300])
        return LLMResponse(text=" ".join(summaries))

    # ---- response collation ----
    def _collate(self, messages: list[dict]) -> LLMResponse:
        payload = json.loads(messages[-1]["content"].split("AGENT_RESULTS_JSON:", 1)[1])
        lines = [f"- {r['agent_name']}: {r['output']}" for r in payload if r["status"] == "ok"]
        missing = [r["agent_name"] for r in payload if r["status"] != "ok"]
        text = "\n".join(lines) if lines else "I couldn't complete that request."
        if missing:
            text += f"\n(Couldn't complete: {', '.join(missing)}. Please try again shortly.)"
        return LLMResponse(text=text)
