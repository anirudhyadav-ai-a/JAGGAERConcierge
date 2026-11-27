import asyncio

import pytest
from fastapi.testclient import TestClient

from app.audit import redact
from app.config import Settings
from app.models import TaskStatus
from app.platform import AgentPlatform


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def platform():
    return AgentPlatform(Settings(llm_provider="mock"), audit_path=None)


def stages(p, trace_id):
    return [f"{e['stage']}.{e['action']}" for e in p.audit.trace(trace_id)]


def test_single_intent_routes_to_one_agent(platform):
    r = run(platform.handle("Why is there a pending charge at Sushi Bar?", "C1001"))
    assert r.status == "ok"
    assert [a["agent_id"] for a in r.context["agents"]] == ["A12"]
    assert "Sushi Bar" in r.response
    assert "search_transactions" in r.context["tools_used"]


def test_multi_intent_runs_dependency_after_upstream(platform):
    r = run(platform.handle("How much did I spend this month and which credit card should I get?", "C1001"))
    assert r.status == "ok"
    assert {a["agent_id"] for a in r.context["agents"]} == {"A11", "A14"}
    order = [e["data"]["agent"] for e in platform.audit.trace(r.trace_id) if e["action"] == "dispatch"]
    assert order.index("A14") < order.index("A11")          # A11 depends on A14
    assert "collator.loop_back" in stages(platform, r.trace_id)  # collator -> orchestrator loop
    assert r.context["orchestrator_loops"] == 2


def test_parallel_independent_agents(platform):
    r = run(platform.handle("Status of my disputed transaction, and how long does the dispute policy say it takes?", "C1001"))
    assert {a["agent_id"] for a in r.context["agents"]} == {"A12", "A13"}
    assert "POL-DISPUTE-01" in r.response
    assert r.context["orchestrator_loops"] == 1


def test_out_of_scope_short_circuits_before_orchestrator(platform):
    r = run(platform.handle("hello there", "C1001"))
    assert r.status == "out_of_scope"
    assert not any(s.startswith("orchestrator") for s in stages(platform, r.trace_id))


def test_low_score_asks_for_clarification():
    p = AgentPlatform(Settings(llm_provider="mock", intent_threshold=0.99), audit_path=None)
    r = run(p.handle("any fees?", "C1001"))
    assert r.status == "clarify"


def test_failing_agent_is_retried_then_reported_partial(platform):
    calls = {"n": 0}
    agent = platform.registry.get("A13")
    original = agent.run

    async def flaky(task, ctx, audit):
        calls["n"] += 1
        raise RuntimeError("KB down")
    agent.run = flaky

    r = run(platform.handle("Status of my disputed transaction, and how long does the dispute policy say it takes?", "C1001"))
    agent.run = original
    assert calls["n"] == platform.cfg.max_task_attempts
    assert r.status == "partial"
    a13 = next(a for a in r.context["agents"] if a["agent_id"] == "A13")
    assert a13["status"] == TaskStatus.FAILED.value


def test_dependent_task_skipped_when_upstream_fails(platform):
    async def broken(task, ctx, audit):
        raise RuntimeError("PFM down")
    platform.registry.get("A14").run = broken
    r = run(platform.handle("How much did I spend this month and which credit card should I get?", "C1001"))
    statuses = {a["agent_id"]: a["status"] for a in r.context["agents"]}
    assert statuses == {"A14": "failed", "A11": "skipped"}
    assert r.status == "error"


def test_agent_timeout():
    p = AgentPlatform(Settings(llm_provider="mock", agent_timeout_s=0.05, max_task_attempts=1), audit_path=None)

    async def slow(task, ctx, audit):
        await asyncio.sleep(1)
    p.registry.get("A12").run = slow
    r = run(p.handle("Why is there a pending charge at Sushi Bar?", "C1001"))
    assert r.status == "error"
    assert any("timeout" in str(e["data"].get("error")) for e in p.audit.trace(r.trace_id))


def test_session_keeps_history_and_rejects_other_customer(platform):
    r1 = run(platform.handle("Why is there a pending charge at Sushi Bar?", "C1001"))
    run(platform.handle("what are the late fees policy?", "C1001", r1.session_id))
    assert len(platform.sessions.get(r1.session_id).history) == 4
    with pytest.raises(PermissionError):
        run(platform.handle("hi", "C9999", r1.session_id))


def test_audit_redacts_sensitive_values(platform):
    assert redact("card 4111111111111111 mail a@b.com") == "card [REDACTED_PAN] mail [REDACTED_EMAIL]"
    r = run(platform.handle("Dispute charge on card 4111111111111111", "C1001"))
    assert "4111111111111111" not in str(platform.audit.trace(r.trace_id))


def test_tool_rejects_unknown_arguments(platform):
    from app.tools import ToolContext
    t = platform.registry.get("A12").tools[0]
    inv = run(t.run({"customer_id": "C2"}, ToolContext("C1001", "s", "t")))
    assert not inv.ok and "unknown arguments" in inv.output["error"]


def test_http_api():
    from app import main
    main.platform = AgentPlatform(Settings(llm_provider="mock"), audit_path=None)
    c = TestClient(main.app)
    assert c.get("/health").json()["agents"] == ["A11", "A12", "A13", "A14"]
    body = c.post("/v1/chat", json={"customer_id": "C1001", "message": "What is the foreign transaction fee policy?"}).json()
    assert body["status"] == "ok" and "POL-FEES-02" in body["response"]
    assert c.get(f"/v1/audit/{body['trace_id']}").status_code == 200
    assert len(c.get(f"/v1/sessions/{body['session_id']}").json()["history"]) == 2


def test_manual_mode_runs_only_selected_agents():
    p = AgentPlatform(Settings(llm_provider="mock"), audit_path=None)
    r = run(p.handle_manual("What is the late payment fee, and how much did I spend on dining this month?",
                            "C1001", ["A14"]))
    assert r.path == "manual" and [a["agent_id"] for a in r.context["agents"]] == ["A14"]
    assert "188.70" in r.response and "POL-LATE-03" not in r.response   # the unselected agent's part is missing


def test_manual_mode_requires_a_selection():
    p = AgentPlatform(Settings(llm_provider="mock"), audit_path=None)
    r = run(p.handle_manual("What about my card?", "C1001", []))
    assert r.status == "error" and r.context["llm_calls"] == 0


def test_ui_and_manual_api():
    from app import main
    main.platform = AgentPlatform(Settings(llm_provider="mock"), audit_path=None)
    c = TestClient(main.app)
    page = c.get("/")
    assert page.status_code == 200 and "Ask once, compare" in page.text
    body = c.post("/v1/chat/manual", json={"customer_id": "C1001", "message": "Any pending charges?",
                                           "agent_ids": ["A12"]}).json()
    assert body["path"] == "manual" and "latency_ms" in body["context"]
