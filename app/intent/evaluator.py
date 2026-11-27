"""Intent Eval / Intent Score (box 1, bottom — the ✓ / ✗ gate).

Turns raw LLM intents into a scored, validated decision:
  - drop intents for unknown/disabled agents
  - penalise empty or very short sub-queries
  - penalise intents depending on agents that were not selected
  - accept if score >= threshold
Routes: "orchestrate" (>=1 accepted) | "clarify" (something matched but too weak)
        | "out_of_scope" (nothing matched at all). Non-orchestrate routes go straight back to the client.
"""
from __future__ import annotations

from ..audit import AuditLogger
from ..models import Intent, IntentDecision, ScoredIntent


class IntentEvaluator:
    def __init__(self, agent_ids: set[str], threshold: float, audit: AuditLogger, agent_names: dict[str, str]):
        self.agent_ids = agent_ids
        self.threshold = threshold
        self.audit = audit
        self.agent_names = agent_names

    def score(self, intent: Intent, selected: set[str]) -> ScoredIntent:
        reasons: list[str] = []
        if intent.agent_id not in self.agent_ids:
            return ScoredIntent(intent, 0.0, False, [f"unknown agent {intent.agent_id}"])
        s = max(0.0, min(1.0, intent.confidence))
        if len(intent.sub_query.split()) < 2:
            s -= 0.25
            reasons.append("sub_query too short")
        missing = [d for d in intent.depends_on if d not in selected]
        if missing:
            intent.depends_on = [d for d in intent.depends_on if d in selected]
            s -= 0.05
            reasons.append(f"dropped missing dependencies {missing}")
        s = round(max(0.0, s), 3)
        accepted = s >= self.threshold
        reasons.append("accepted" if accepted else f"below threshold {self.threshold}")
        return ScoredIntent(intent, s, accepted, reasons)

    def forced(self, intents: list[Intent], chosen: list[str], message: str,
               trace_id: str, session_id: str) -> IntentDecision:
        """Manual selection: the user's chosen agents are accepted as-is (no score gate, no clarify).
        A chosen agent the router found no part of the question for gets the whole question."""
        by_agent = {i.agent_id: i for i in intents if i.agent_id in chosen}
        scored = []
        # same order the router would produce, so identical selections give identical answers
        ordered = [i.agent_id for i in intents if i.agent_id in chosen] + [a for a in chosen if a not in by_agent]
        for aid in dict.fromkeys(ordered):
            i = by_agent.get(aid) or Intent(aid, message, 1.0, rationale="selected by user")
            i.confidence = 1.0
            i.depends_on = [d for d in i.depends_on if d in chosen and d != aid]
            scored.append(ScoredIntent(i, 1.0, True, ["selected by user"]))
        self.audit.event(trace_id, session_id, "intent_score", "orchestrate", reason="user selected agents",
                         agents=chosen)
        return IntentDecision("orchestrate", scored)

    def evaluate(self, intents: list[Intent], trace_id: str, session_id: str,
                 ambiguous: bool = False, clarifying_question: str = "") -> IntentDecision:
        if ambiguous:
            # The router itself flagged the message as ambiguous: never guess, ask.
            scored = [ScoredIntent(i, round(min(i.confidence, self.threshold - 0.01), 3), False,
                                   ["router flagged ambiguous"]) for i in intents if i.agent_id in self.agent_ids]
            options = [self.agent_names[s.intent.agent_id] for s in scored]
            question = clarifying_question or (
                "Could you tell me a bit more? I can help with " + ", ".join(options or self.agent_names.values()) + ".")
            decision = IntentDecision("clarify", scored, question)
            self.audit.event(trace_id, session_id, "intent_score", "clarify", reason="ambiguous",
                             options=options)
            return decision

        selected = {i.agent_id for i in intents if i.agent_id in self.agent_ids}
        scored = [self.score(i, selected) for i in intents]
        # dependencies on rejected intents are removed so the planner never waits forever
        accepted_ids = {s.intent.agent_id for s in scored if s.accepted}
        for s in scored:
            s.intent.depends_on = [d for d in s.intent.depends_on if d in accepted_ids]

        if any(s.accepted for s in scored):
            decision = IntentDecision("orchestrate", scored)
        elif scored:
            options = ", ".join(self.agent_names[s.intent.agent_id] for s in scored if s.intent.agent_id in self.agent_names)
            decision = IntentDecision("clarify", scored,
                                      f"I want to make sure I get this right — are you asking about {options}? "
                                      "Could you add a bit more detail?")
        else:
            options = "; ".join(self.agent_names.values())
            decision = IntentDecision("out_of_scope", scored,
                                      f"I can help with: {options}. What would you like to do?")

        self.audit.event(trace_id, session_id, "intent_score", decision.route,
                         threshold=self.threshold,
                         scored=[{"agent_id": s.intent.agent_id, "score": s.score,
                                  "accepted": s.accepted, "reasons": s.reasons} for s in scored])
        return decision
