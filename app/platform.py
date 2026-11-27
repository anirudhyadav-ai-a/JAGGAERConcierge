"""The Client box: wires Session -> Intent layer -> Orchestration graph -> Audit, end to end."""
from __future__ import annotations

from .agents import AgentRegistry
from .audit import AuditLogger
from .data import DataCatalog
from .config import Settings, settings as default_settings
from .intent import IntentEvaluator, IntentPreprocessor
from .llm import BudgetExceeded, CallLimitExceeded, MeteredLLM, build_llm, start_request
from .models import FinalResponse, new_id
from .orchestrator import OrchestrationState, build_graph
from .session import SessionStore, Turn


class AgentPlatform:
    def __init__(self, cfg: Settings | None = None, llm: MeteredLLM | None = None, audit_path: str | None = "default"):
        self.cfg = cfg or default_settings
        self.llm = llm or build_llm(self.cfg)
        self.audit = AuditLogger(self.cfg.audit_path if audit_path == "default" else audit_path)
        self.sessions = SessionStore()
        self.data = DataCatalog(self.cfg.data_dir, self.cfg.today)
        self.registry = AgentRegistry(self.llm, self.data, self.cfg.agent_max_tool_steps)
        names = {a.id: a.name for a in self.registry.enabled()}
        self.preprocessor = IntentPreprocessor(self.llm, self.registry.catalog(), self.audit, self.cfg.history_turns)
        self.evaluator = IntentEvaluator(set(names), self.cfg.intent_threshold, self.audit, names)
        self.graph = build_graph(self.llm, self.registry, self.audit, self.cfg)

    @property
    def budget(self):
        return getattr(self.llm, "budget", None)

    async def handle(self, message: str, customer_id: str, session_id: str | None = None,
                     agent_ids: list[str] | None = None) -> FinalResponse:
        """agent_ids=None: the platform picks the agents (after).
        agent_ids=[...]: the user picked them (before). Everything downstream is the SAME pipeline, so with the
        same agents the two modes run the same tools and produce the same answer."""
        manual = agent_ids is not None
        session = self.sessions.get_or_create(session_id, customer_id)
        trace_id = new_id("trace")
        meter = start_request(self.cfg.max_llm_calls_per_request)
        enabled = [a.id for a in self.registry.enabled()]
        chosen = [a for a in dict.fromkeys(agent_ids or []) if a in enabled]
        self.audit.event(trace_id, session.id, "client", "request", customer_id=customer_id, message=message,
                         mode="manual" if manual else "auto", selected_agents=chosen if manual else None)
        if manual and not chosen:
            return FinalResponse(session.id, trace_id, "error", "Select at least one agent before asking.",
                                 path="manual", context={"agents": [], "tools_used": [], "llm_calls": 0, "cost_usd": 0.0})

        try:
            # Box 1: Intent pre-processing -> Intent Eval / Score
            routed = await self.preprocessor.run(message, session, trace_id, allowed=chosen if manual else None)
            if manual:
                decision = self.evaluator.forced(routed.intents, chosen, message, trace_id, session.id)
            else:
                decision = self.evaluator.evaluate(routed.intents, trace_id, session.id,
                                                   routed.ambiguous, routed.clarifying_question)

            if decision.route != "orchestrate":
                final = FinalResponse(session.id, trace_id, decision.route, decision.message, path="intent",
                                      context={"intents": [{"agent_id": s.intent.agent_id, "score": s.score,
                                                            "accepted": s.accepted} for s in decision.scored]})
            else:
                # Box 2/3: Orchestrator graph -> Executor -> Agents -> Response Collator (loop)
                state = OrchestrationState(
                    trace_id, session.id, customer_id, message, decision.accepted,
                    intent_scores={s.intent.agent_id: s.score for s in decision.scored},
                )
                await self.graph.run(state, on_step=lambda n: self.audit.event(
                    trace_id, session.id, "orchestrator", "node", node=n))
                final = state.final
                if manual:
                    final.path = "manual"
        except BudgetExceeded as exc:
            self.audit.event(trace_id, session.id, "client", "budget_exceeded", error=str(exc))
            final = FinalResponse(session.id, trace_id, "budget_exceeded",
                                  "The demo's LLM spending limit has been reached, so no further calls will be made. "
                                  "Raise BUDGET_USD or switch to LLM_PROVIDER=mock.", path="intent")
        except CallLimitExceeded as exc:
            self.audit.event(trace_id, session.id, "client", "call_limit", error=str(exc))
            final = FinalResponse(session.id, trace_id, "error",
                                  "This request needed more AI steps than allowed. Please ask a narrower question.")
        except Exception as exc:
            self.audit.event(trace_id, session.id, "client", "error", error=f"{type(exc).__name__}: {exc}")
            hints = {
                "AuthenticationError": "The Claude API key was rejected. Check ANTHROPIC_API_KEY in your .env file.",
                "PermissionDeniedError": "This API key isn't allowed to use the configured model or workspace.",
                "RateLimitError": "Claude API rate limit or spend limit reached. Wait a moment, or check the "
                                  "workspace spend limit in the Console.",
                "APIConnectionError": "Couldn't reach the Claude API. Check your internet connection.",
            }
            final = FinalResponse(session.id, trace_id, "error", hints.get(
                type(exc).__name__, "Sorry, something went wrong on our side. Please try again."), path="none")

        # Agent-level budget/limit errors surface as failed tasks; report them clearly too.
        if final.status in ("error", "partial") and self._budget_hit(trace_id):
            final.status = "budget_exceeded"
            final.response += "\n(The demo's LLM spending limit was reached during this request.)"

        final.context["llm_calls"] = meter.calls
        final.context["llm_calls_by_step"] = dict(meter.by_purpose)
        final.context["cost_usd"] = round(meter.cost_usd, 6)
        if self.budget is not None:
            final.context["budget_remaining_usd"] = round(self.budget.remaining, 4)

        session.history += [Turn("user", message, trace_id), Turn("assistant", final.response, trace_id)]
        self.audit.event(trace_id, session.id, "client", "response", status=final.status,
                         llm_calls=meter.calls, cost_usd=round(meter.cost_usd, 6))
        return final

    async def handle_manual(self, message: str, customer_id: str, agent_ids: list[str],
                            session_id: str | None = None) -> FinalResponse:
        """The 'before' experience: the user picks the agents; the rest of the pipeline is identical."""
        return await self.handle(message, customer_id, session_id, agent_ids=agent_ids)

    def _budget_hit(self, trace_id: str) -> bool:
        return any("BudgetExceeded" in str(e["data"].get("error", "")) or "budget reached" in str(e["data"].get("error", ""))
                   for e in self.audit.trace(trace_id))
