"""Plan cache (spec step 9): rows 5-7, lookup rules, persistence, isolation, fallback, API and the Claude path."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from app.cache import HashingEmbedder, JsonFilePlanStore, cosine, extract_entities, mask
from app.config import Settings
from app.platform import AgentPlatform

ROW3 = "What is the late payment fee, and how much did I spend on dining this month?"
ROW5 = "How much have I spent on dining this month and what's the late fee?"
ROW6 = "How much did I spend on travel this month, and what's the late fee?"
ROW2 = "What's happening with my $89.99 Online Store 24 charge, and where is the dispute?"


def platform(**cfg):
    return AgentPlatform(Settings(llm_provider="mock", cache_path="", **cfg), audit_path=None)


def ask(p, q, session=None, customer="C1001", **kw):
    r = asyncio.run(p.handle(q, customer, session, **kw))
    tools = [(e["data"]["agent"], e["data"]["tool"], e["data"]["arguments"])
             for e in p.audit.trace(r.trace_id) if e["stage"] == "tool"]
    return r, tools


# ---- acceptance rows 5-7 ----

def test_row5_cache_hit_runs_the_same_tools_live_with_two_llm_calls():
    p = platform()
    first, _ = ask(p, ROW3)
    assert first.path == "full" and first.context["llm_calls"] == 6 and first.context["cache"]["saved"]
    r, tools = ask(p, ROW5)
    assert r.status == "ok" and r.path == "cache"
    assert r.context["llm_calls"] == 2 and r.context["llm_calls_by_step"] == {"intent": 1, "collate": 1}
    assert {(a, t) for a, t, _ in tools} == {("A13", "search_policies"), ("A14", "spending_summary")}
    for fact in ("$35", "POL-LATE-03", "188.70"):
        assert fact in r.response
    c = r.context["cache"]
    assert c["hit"] and c["similarity"] >= 0.80 and c["first_run"]["llm_calls"] == 6


def test_row6_new_slot_value_reuses_the_plan_template():
    p = platform()
    ask(p, ROW3)
    r, tools = ask(p, ROW6)
    assert r.path == "cache" and r.context["llm_calls"] == 2
    assert ("A14", "spending_summary") in [(a, t) for a, t, _ in tools]
    assert next(args for a, t, args in tools if t == "spending_summary")["category"] == "travel"
    assert "644.50" in r.response and "POL-LATE-03" in r.response


def test_row7_more_details_reruns_the_previous_question_in_full_and_counts_against_the_plan():
    p = platform()
    ask(p, ROW3)
    r5, _ = ask(p, ROW5)
    plan_id = r5.context["cache"]["plan_id"]
    r7, _ = ask(p, "more details please", r5.session_id)
    assert r7.path == "full" and r7.context["detail"] is True and r7.context["llm_calls"] == 6
    assert r7.context["answered_question"] == ROW5
    for fact in ("$35", "POL-LATE-03", "188.70"):
        assert fact in r7.response
    plans = p.plan_cache.store.all()
    assert len(plans) == 1 and plans[0].negative == 1          # replaced, and the negative count carried over
    assert plans[0].id != plan_id                              # the full run's plan took the old one's place


def test_more_details_flag_works_like_the_phrase():
    p = platform()
    r, _ = ask(p, ROW3)
    again, _ = ask(p, "anything", r.session_id, more_details=True)
    assert again.path == "full" and again.context["detail"] and again.context["answered_question"] == ROW3


def test_a_plan_with_too_many_more_details_requests_is_no_longer_used():
    p = platform(cache_max_negative=2)
    ask(p, ROW3)
    for _ in range(2):
        r, _ = ask(p, ROW5)
        assert r.path == "cache"
        ask(p, "tell me more", r.session_id)
    r, _ = ask(p, ROW5)
    assert r.path == "full" and "more details" in r.context["cache"]["reason"]


def test_a_longer_question_mentioning_detail_is_new_and_skips_the_cache():
    p = platform()
    ask(p, ROW3)
    r, _ = ask(p, "In detail please: how much have I spent on dining this month and what's the late fee?")
    assert r.path == "full" and "detail" not in r.context


# ---- lookup rules ----

def test_missing_slot_is_a_miss_not_a_guess():
    p = platform()
    ask(p, ROW3)
    r, _ = ask(p, "What's the late fee and how much did I spend this month?")
    assert r.path == "full" and r.context["cache"]["reason"] == "question has no category"


def test_a_different_set_of_agents_never_reads_the_plan():
    p = platform()
    ask(p, ROW3)
    r, _ = ask(p, "How much did I spend on dining this month?")       # A14 only
    assert r.path == "full" and r.context["cache"]["reason"] == "no plan for this set of agents"


def test_cache_expires_after_ttl():
    p = platform(cache_ttl_s=60)
    now = [1_000_000.0]
    p.plan_cache.clock = lambda: now[0]
    ask(p, ROW3)
    assert ask(p, ROW5)[0].path == "cache"
    now[0] += 61
    r, _ = ask(p, ROW5)
    assert r.path == "full" and r.context["cache"]["reason"] == "plan expired"


def test_cache_is_invalidated_when_a_tool_schema_changes():
    p = platform()
    ask(p, ROW3)
    p.plan_cache.version = "tools-changed"         # what a new catalog hash looks like after a schema change
    r, _ = ask(p, ROW5)
    assert r.path == "full" and r.context["cache"]["reason"] == "tools changed since the plan was built"


def test_a_plan_that_reuses_an_id_from_a_tool_result_is_not_cached():
    """The mock passes T9004 (read from search_transactions' output) to get_dispute_status. A plan holding that
    literal would point every later question at one customer's transaction, so it isn't saved."""
    p = platform()
    r, _ = ask(p, ROW2)
    assert r.status == "ok" and r.context["cache"]["saved"] is False
    assert "transaction_id" in r.context["cache"]["save_note"]
    assert p.plan_cache.store.all() == []


def test_a_cached_plan_serves_another_customer_with_their_own_data():
    p = platform()
    ask(p, ROW3, customer="C1001")
    r, tools = ask(p, "How much have I spent on shopping this month and what's the late fee?", customer="C2002")
    assert r.path == "cache"
    assert "45.00" in r.response and "299.99" not in r.response        # C2002's shopping, not C1001's
    plan = p.plan_cache.store.all()[0]
    stored = str(plan.summary())
    for leaked in ("C1001", "188.70", "dining"):
        assert leaked not in stored                                   # no customer data or slot values stored


def test_a_failing_cached_tool_falls_back_to_the_full_flow():
    p = platform()
    ask(p, ROW3)
    plan = p.plan_cache.store.all()[0]
    step = next(s for s in plan.steps if s.tool == "spending_summary")
    step.arguments["category"] = "not-a-category"                    # the replayed call will fail validation
    p.plan_cache.store.put(plan)
    r, _ = ask(p, ROW5)
    assert r.path == "full" and r.status == "ok" and "188.70" in r.response
    assert r.context["cache"]["reason"].startswith("a cached tool failed")
    assert any(e["stage"] == "cache" and e["action"] == "fallback" for e in p.audit.trace(r.trace_id))


def test_manual_mode_never_uses_or_writes_the_cache():
    p = platform()
    ask(p, ROW3)
    r, _ = ask(p, ROW5, agent_ids=["A13", "A14"])
    assert r.path == "manual" and r.context["llm_calls"] == 6 and "cache" not in r.context
    assert len(p.plan_cache.store.all()) == 1


def test_cache_can_be_switched_off():
    p = platform(cache_enabled=False)
    ask(p, ROW3)
    r, _ = ask(p, ROW5)
    assert r.path == "full" and p.plan_cache.store.all() == []


# ---- persistence ----

def test_plans_persist_across_restarts(tmp_path):
    path = tmp_path / "plans.json"
    first = AgentPlatform(Settings(llm_provider="mock", cache_path=str(path)), audit_path=None)
    ask(first, ROW3)
    assert path.exists() and len(JsonFilePlanStore(path).all()) == 1
    restarted = AgentPlatform(Settings(llm_provider="mock", cache_path=str(path)), audit_path=None)
    r, _ = ask(restarted, ROW5)
    assert r.path == "cache" and r.context["llm_calls"] == 2


def test_an_unreadable_cache_file_starts_empty(tmp_path):
    path = tmp_path / "plans.json"
    path.write_text("{not json")
    p = AgentPlatform(Settings(llm_provider="mock", cache_path=str(path)), audit_path=None)
    assert ask(p, ROW3)[0].path == "full"


# ---- building blocks ----

def test_entities_and_masking():
    e = extract_entities("Did I pay $89.99 at Online Store 24 (T9004) on dining in the last 2 weeks?", ["Online Store 24"])
    assert e == {"category": "dining", "merchant": "Online Store 24", "transaction_id": "T9004", "days": 14,
                 "amount": 89.99}
    assert mask(ROW3, extract_entities(ROW3)) == "what is the late payment fee, and how much did i spend on {category} {days} ?"


def test_embedder_separates_same_plan_from_different_plans():
    emb = HashingEmbedder()
    q = lambda t: emb.embed(mask(t, extract_entities(t, ["Online Store 24"])))   # noqa: E731
    assert cosine(q(ROW3), q(ROW5)) >= 0.80 and cosine(q(ROW3), q(ROW6)) >= 0.80
    assert cosine(q("Where is my dispute for the Online Store 24 charge?"),
                  q("Do I have any pending charges at Online Store 24?")) < 0.80


# ---- API ----

def test_cache_api_lists_and_clears_plans():
    from app import main
    main.platform = platform()
    c = TestClient(main.app)
    c.post("/v1/chat", json={"customer_id": "C1001", "message": ROW3})
    body = c.get("/v1/cache").json()
    assert body["count"] == 1 and body["plans"][0]["slots"] == ["category"]
    assert "embedding" not in body["plans"][0]
    hit = c.post("/v1/chat", json={"customer_id": "C1001", "message": ROW5}).json()
    assert hit["path"] == "cache" and hit["context"]["cache"]["hit"]
    more = c.post("/v1/chat", json={"customer_id": "C1001", "message": "x", "session_id": hit["session_id"],
                                    "more_details": True}).json()
    assert more["path"] == "full" and more["context"]["detail"]
    assert c.delete("/v1/cache").json() == {"cleared": 1}
    assert c.get("/v1/cache").json()["count"] == 0


# ---- Claude path (fake SDK client) ----

def test_cache_hit_on_the_claude_path_makes_one_haiku_and_one_sonnet_call(tmp_path):
    from app.llm import build_llm
    from tests.fake_anthropic import FakeAsyncAnthropic
    cfg = Settings(llm_provider="anthropic", cache_path="", usage_ledger_path=str(tmp_path / "ledger.json"))
    client = FakeAsyncAnthropic()
    p = AgentPlatform(cfg, llm=build_llm(cfg, client=client), audit_path=None)
    full, _ = ask(p, ROW3)
    n = len(client.requests)
    hit, _ = ask(p, ROW5)
    models = [k["model"] for k in client.requests[n:]]
    assert hit.path == "cache" and models == ["claude-haiku-5-5", "claude-sonnet-5-5"]
    assert hit.context["cost_usd"] < full.context["cost_usd"]
