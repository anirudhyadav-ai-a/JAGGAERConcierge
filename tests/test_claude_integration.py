"""The real Claude code path (AnthropicLLM + MeteredLLM + BudgetGuard), driven by a fake SDK client."""
import asyncio
import json

import pytest

from app.config import Settings
from app.llm import build_llm
from app.llm.budget import BudgetGuard, cost_usd
from app.platform import AgentPlatform
from tests.fake_anthropic import FakeAsyncAnthropic


def run(coro):
    return asyncio.run(coro)


def claude_platform(tmp_path, client=None, **overrides):
    cfg = Settings(llm_provider="anthropic", usage_ledger_path=str(tmp_path / "ledger.json"), **overrides)
    client = client or FakeAsyncAnthropic()
    return AgentPlatform(cfg, llm=build_llm(cfg, client=client), audit_path=None), client


def test_routes_by_model_rules(tmp_path):
    p, client = claude_platform(tmp_path)
    r = run(p.handle("What is the late payment fee, and how much did I spend on dining this month?", "C1001"))
    assert r.status == "ok"
    intent = [k for k in client.requests if k["model"] == "claude-haiku-5-5"]
    agents = [k for k in client.requests if k["model"] == "claude-sonnet-5-5" and k.get("tools")]
    assert len(intent) == 1
    assert intent[0]["tool_choice"] == {"type": "tool", "name": "route_intents"}
    assert intent[0]["thinking"] == {"type": "disabled"}
    assert agents and all(k["thinking"] == {"type": "between_tools"} and "tool_choice" not in k for k in agents)


def test_three_interview_scenarios_on_claude_path(tmp_path):
    p, _ = claude_platform(tmp_path)
    amb = run(p.handle("What about my card?", "C1001"))
    assert amb.status == "clarify" and amb.context["llm_calls"] == 1

    single = run(p.handle("What's happening with my $89.99 Online Store 24 charge, and where is the dispute?", "C1001"))
    tools = [e["data"]["tool"] for e in p.audit.trace(single.trace_id) if e["stage"] == "tool"]
    assert single.status == "ok" and tools == ["search_transactions", "get_dispute_status"]
    assert "D-55120" in single.response

    multi = run(p.handle("What is the late payment fee, and how much did I spend on dining this month?", "C1001"))
    assert multi.status == "ok"
    assert {a["agent_id"] for a in multi.context["agents"]} == {"A13", "A14"}
    assert "188.70" in multi.response and "POL-LATE-03" in multi.response
    assert multi.context["cost_usd"] > 0


def test_cost_is_tracked_and_persisted(tmp_path):
    p, client = claude_platform(tmp_path)
    r = run(p.handle("Why is there a pending charge at Sushi Bar?", "C1001"))
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["calls"] == len(client.requests) == r.context["llm_calls"]
    assert ledger["spent_usd"] == pytest.approx(r.context["cost_usd"], abs=1e-6)
    # a restart picks the ledger up again
    assert BudgetGuard(5.0, str(tmp_path / "ledger.json")).spent == pytest.approx(ledger["spent_usd"])


def test_budget_stops_calls_before_overspending(tmp_path):
    p, client = claude_platform(tmp_path, budget_usd=0.02)
    statuses = [run(p.handle("How much did I spend this month and which credit card suits my spending?", "C1001")).status
                for _ in range(10)]
    assert "budget_exceeded" in statuses
    assert p.budget.spent <= 0.02           # never goes over, because worst-case cost is reserved up front
    # cheaper Haiku routing calls may still fit; keep going until even those are refused
    for _ in range(100):
        if run(p.handle("What about my card?", "C1001")).status == "budget_exceeded":
            break
    calls_at_limit = len(client.requests)
    r = run(p.handle("What about my card?", "C1001"))
    assert r.status == "budget_exceeded" and len(client.requests) == calls_at_limit   # no further API calls
    assert p.budget.spent <= 0.02


def test_per_request_call_cap(tmp_path):
    p, client = claude_platform(tmp_path, max_llm_calls_per_request=2)
    r = run(p.handle("What is the late payment fee, and how much did I spend on dining this month?", "C1001"))
    assert r.status != "ok"
    assert r.context["llm_calls"] == 2 and len(client.requests) == 2


def test_missing_key_gives_clear_error(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        build_llm(Settings(llm_provider="anthropic", usage_ledger_path=str(tmp_path / "l.json")))


def test_cost_formula():
    assert cost_usd("claude-sonnet-5-5", 1_000_000, 0) == pytest.approx(2.0)
    assert cost_usd("claude-sonnet-5-5", 0, 1_000_000) == pytest.approx(10.0)
    assert cost_usd("claude-haiku-5-5", 1_000_000, 1_000_000) == pytest.approx(0.60)
    assert cost_usd("some-future-model", 1_000_000, 0) == pytest.approx(10.0)   # unknown -> priced high


def test_router_on_sonnet_does_not_send_a_rejected_tool_choice(tmp_path):
    """Sonnet/Opus 5.5 reject forced tool_choice of type "tool" and "any"; the router must use "auto"."""
    p, client = claude_platform(tmp_path, intent_model="claude-sonnet-5-5")
    r = run(p.handle("What is the late payment fee, and how much did I spend on dining this month?", "C1001"))
    assert r.status == "ok"
    intent = next(k for k in client.requests if any(t["name"] == "route_intents" for t in k.get("tools", [])))
    assert intent["tool_choice"] == {"type": "auto"}


def test_refusal_from_the_router_is_reported_plainly(tmp_path):
    p, _ = claude_platform(tmp_path, client=FakeAsyncAnthropic(refuse_purpose="intent"))
    r = run(p.handle("What about my card?", "C1001"))
    assert r.status == "error" and "declined" in r.response
    # a refused request still bills its input tokens, so it must be in the ledger
    ledger = json.loads((tmp_path / "ledger.json").read_text())
    assert ledger["calls"] == 1 and ledger["spent_usd"] > 0 and r.context["cost_usd"] > 0


def test_refusal_inside_an_agent_fails_that_agent_only(tmp_path):
    p, _ = claude_platform(tmp_path, client=FakeAsyncAnthropic(refuse_purpose="agent"))
    r = run(p.handle("What is the late payment fee, and how much did I spend on dining this month?", "C1001"))
    assert r.status == "error"
    assert all(a["status"] != "ok" for a in r.context["agents"])
    assert "collate" not in r.context["llm_calls_by_step"]   # nothing to combine: no paid collate call
    assert "couldn't complete" in r.response
