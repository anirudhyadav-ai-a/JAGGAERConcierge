"""The Client box: wires Session -> Intent layer -> (plan cache | Orchestration graph) -> Audit, end to end."""
from __future__ import annotations

import re
import time

from .agents import AgentRegistry
from .audit import AuditLogger, redact
from .cache import FastPath, PlanCache, build_store, catalog_version, extract_entities, mask
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
        self.plan_cache = PlanCache(build_store(self.cfg.cache_path or None), catalog_version(self.registry.enabled()),
                                    self.cfg.cache_similarity, self.cfg.cache_ttl_s, self.cfg.cache_max_negative)
        self.fast_path = FastPath(self.llm, self.registry, self.audit)
        self._tools = {(a.id, t.name): t for a in self.registry.enabled() for t in a.tools}

    @property
    def budget(self):
        return getattr(self.llm, "budget", None)

    async def handle(self, message: str, customer_id: str, session_id: str | None = None,
                     agent_ids: list[str] | None = None, more_details: bool = False) -> FinalResponse:
        """agent_ids=None: the platform picks the agents (after).
        agent_ids=[...]: the user picked them (before). Everything downstream is the SAME pipeline, so with the
        same agents the two modes run the same tools and produce the same answer.
        more_details=True, or a short follow-up like "more details please": re-run the previous question through
        the full graph for a fuller answer, bypassing the plan cache."""
        manual = agent_ids is not None
        session = self.sessions.get_or_create(session_id, customer_id)
        trace_id = new_id("trace")
        started = time.perf_counter()
        meter = start_request(self.cfg.max_llm_calls_per_request)
        enabled = [a.id for a in self.registry.enabled()]
        chosen = [a for a in dict.fromkeys(agent_ids or []) if a in enabled]
        self.audit.event(trace_id, session.id, "client", "request", customer_id=customer_id, message=message,
                         mode="manual" if manual else "auto", selected_agents=chosen if manual else None)
        if manual and not chosen:
            return FinalResponse(session.id, trace_id, "error", "Select at least one agent before asking.",
                                 path="manual", context={"agents": [], "tools_used": [], "llm_calls": 0, "cost_usd": 0.0})

        # "More details": answer the previous question again, through the full graph, in detail.
        question, detail, use_cache, replaces = message, False, self.cfg.cache_enabled and not manual, None
        last = session.memory.get("last") if not manual else None
        if not manual and (more_details or _is_detail_request(message)) and last:
            question, detail, use_cache = last["question"], True, False
            if last.get("path") == "cache" and last.get("plan_id"):
                self.plan_cache.add_negative(last["plan_id"])     # the cached answer wasn't enough
                replaces = last["plan_id"]
            self.audit.event(trace_id, session.id, "client", "more_details", question=question,
                             previous_path=last.get("path"), plan_id=last.get("plan_id"))
        elif _mentions_detail(message):
            use_cache = False          # a longer message asking for detail is a new question: skip the cache

        cache_info: dict | None = None
        pending_plan = None
        final = None
        try:
            # Box 1: Intent pre-processing -> Intent Eval / Score
            routed = await self.preprocessor.run(question, session, trace_id, allowed=chosen if manual else None)
            if manual:
                decision = self.evaluator.forced(routed.intents, chosen, question, trace_id, session.id)
            else:
                decision = self.evaluator.evaluate(routed.intents, trace_id, session.id,
                                                   routed.ambiguous, routed.clarifying_question)

            if decision.route != "orchestrate":
                final = FinalResponse(session.id, trace_id, decision.route, decision.message, path="intent",
                                      context={"intents": [{"agent_id": s.intent.agent_id, "score": s.score,
                                                            "accepted": s.accepted} for s in decision.scored]})
            else:
                entities = extract_entities(question, self._merchants())
                masked = mask(redact(question), entities)
                agents = [i.agent_id for i in decision.accepted]
                if use_cache:
                    # Plan cache: same agents + slots present + similar question -> run the cached tools live
                    lookup = self.plan_cache.lookup(agents, entities, masked)
                    self.audit.event(trace_id, session.id, "cache", "lookup", result=lookup.reason,
                                     similarity=lookup.best_similarity, agents=agents, slots=sorted(entities))
                    cache_info = {"hit": False, "reason": lookup.reason}
                    if lookup.hit:
                        final = await self.fast_path.run(lookup.hit, decision.accepted, entities, question,
                                                         customer_id, session.id, trace_id)
                        if final is not None:
                            entry = lookup.hit.entry
                            self.plan_cache.record_hit(entry)
                            cache_info = {"hit": True, "plan_id": entry.id, "similarity": lookup.hit.similarity,
                                          "plan_question": entry.question, "hits": entry.hits,
                                          "first_run": entry.first_run}
                        else:
                            cache_info = {"hit": False, "reason": "a cached tool failed, so the full flow ran"}
                if final is None:
                    # Box 2/3: Orchestrator graph -> Executor -> Agents -> Response Collator (loop)
                    state = OrchestrationState(
                        trace_id, session.id, customer_id, question, decision.accepted,
                        intent_scores={s.intent.agent_id: s.score for s in decision.scored}, detail=detail,
                    )
                    await self.graph.run(state, on_step=lambda n: self.audit.event(
                        trace_id, session.id, "orchestrator", "node", node=n))
                    final = state.final
                    if manual:
                        final.path = "manual"
                    elif self.cfg.cache_enabled and final.status == "ok":
                        pending_plan = (state, entities, masked)
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
                "LLMRefusal": "Claude declined to answer this question. Please rephrase it.",
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
        final.context["latency_ms"] = int((time.perf_counter() - started) * 1000)
        if self.budget is not None:
            final.context["budget_remaining_usd"] = round(self.budget.remaining, 4)
        if detail:
            final.context["detail"] = True
            final.context["answered_question"] = question

        # Save the plan (which agent ran which tool) after a successful full run, never the answer.
        plan_id = cache_info.get("plan_id") if cache_info else None
        if pending_plan:
            saved_id, reason = self._save_plan(*pending_plan, final, trace_id, replaces)
            plan_id = saved_id or plan_id
            cache_info = {**(cache_info or {"hit": False}), "saved": bool(saved_id), "saved_plan_id": saved_id,
                          "save_note": reason}
            self.audit.event(trace_id, session.id, "cache", "saved" if saved_id else "not_saved",
                             plan_id=saved_id, reason=reason)
        if cache_info is not None:
            final.context["cache"] = cache_info
        if not manual and final.path in ("full", "cache"):
            session.memory["last"] = {"question": question, "path": final.path, "plan_id": plan_id}

        session.history += [Turn("user", message, trace_id), Turn("assistant", final.response, trace_id)]
        self.audit.event(trace_id, session.id, "client", "response", status=final.status,
                         llm_calls=meter.calls, cost_usd=round(meter.cost_usd, 6))
        return final

    async def handle_manual(self, message: str, customer_id: str, agent_ids: list[str],
                            session_id: str | None = None) -> FinalResponse:
        """The 'before' experience: the user picks the agents; the rest of the pipeline is identical."""
        return await self.handle(message, customer_id, session_id, agent_ids=agent_ids)

    def _merchants(self) -> set[str]:
        try:
            return self.data.repo("transactions").merchants()
        except (KeyError, FileNotFoundError):
            return set()

    def _save_plan(self, state, entities, masked, final, trace_id, replaces):
        invocations = []
        waves = sorted({t.wave for t in state.tasks.values()})
        for task in sorted(state.tasks.values(), key=lambda t: (t.wave, t.id)):
            result = state.results.get(task.id)
            for inv in (result.tool_calls if result else []):
                invocations.append((task.agent_id, waves.index(task.wave), inv))
        entry, reason = self.plan_cache.build(
            agents=[i.agent_id for i in state.intents],
            depends_on={i.agent_id: list(i.depends_on) for i in state.intents if i.depends_on},
            invocations=invocations, tools=self._tools, entities=entities, masked_question=masked,
            first_run={"llm_calls": final.context["llm_calls"], "latency_ms": final.context["latency_ms"],
                       "cost_usd": final.context["cost_usd"], "trace_id": trace_id})
        if entry is None:
            return None, reason
        self.plan_cache.save(entry, replaces=replaces)
        return entry.id, "saved"

    def _budget_hit(self, trace_id: str) -> bool:
        return any("BudgetExceeded" in str(e["data"].get("error", "")) or "budget reached" in str(e["data"].get("error", ""))
                   for e in self.audit.trace(trace_id))


_DETAIL_PHRASES = ("more detail", "more details", "elaborate", "tell me more", "not helpful", "go deeper",
                   "explain more", "in detail", "more info")


def _mentions_detail(message: str) -> bool:
    low = message.lower()
    return any(p in low for p in _DETAIL_PHRASES)


def _is_detail_request(message: str) -> bool:
    """A short follow-up (8 words or fewer) asking for more detail on the previous answer."""
    return len(re.findall(r"\w+", message)) <= 8 and _mentions_detail(message)
