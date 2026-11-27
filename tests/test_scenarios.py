"""Acceptance tests: the interview scenarios, run offline on the mock LLM."""
import asyncio

import pytest

from app.config import Settings
from app.platform import AgentPlatform


@pytest.fixture
def p():
    return AgentPlatform(Settings(llm_provider="mock"), audit_path=None)


def ask(p, q, customer="C1001"):
    r = asyncio.run(p.handle(q, customer))
    tools = [(e["data"]["agent"], e["data"]["tool"]) for e in p.audit.trace(r.trace_id) if e["stage"] == "tool"]
    return r, tools


def test_ambiguous_asks_and_runs_no_agent(p):
    r, tools = ask(p, "What about my card?")
    assert r.status == "clarify" and r.path == "intent" and tools == []
    for option in ("Credit Card Recommendation", "Transaction Queries", "Policy Knowledge Base"):
        assert option in r.response
    assert r.context["llm_calls"] == 1


def test_single_agent_two_tools(p):
    r, tools = ask(p, "What's happening with my $89.99 Online Store 24 charge, and where is the dispute?")
    assert r.status == "ok"
    assert tools == [("A12", "search_transactions"), ("A12", "get_dispute_status")]
    for fact in ("89.99", "D-55120", "under review", "provisional credit"):
        assert fact in r.response
    assert r.context["llm_calls"] == 4


def test_multi_agent_one_tool_each(p):
    r, tools = ask(p, "What is the late payment fee, and how much did I spend on dining this month?")
    assert r.status == "ok"
    assert sorted(tools) == [("A13", "search_policies"), ("A14", "spending_summary")]
    for fact in ("$35", "POL-LATE-03", "188.70"):
        assert fact in r.response
    assert r.context["orchestrator_loops"] == 1          # both agents ran in the same parallel wave
    assert r.context["llm_calls"] == 6


def test_dependent_agents(p):
    r, tools = ask(p, "How much did I spend this month and which credit card suits my spending?")
    assert [t[0] for t in tools] == ["A14", "A11"]
    assert "1,525.84" in r.response and "Flat 2% Cash Back Card" in r.response
    assert r.context["orchestrator_loops"] == 2


def test_customer_isolation(p):
    r, _ = ask(p, "What's happening with my $89.99 Online Store 24 charge, and where is the dispute?", customer="C2002")
    assert "T9004" not in r.response and "D-55120" not in r.response


def test_second_customer_sees_only_own_data(p):
    r, _ = ask(p, "How much did I spend this month?", customer="C2002")
    assert "45.00" in r.response and "1,525.84" not in r.response


@pytest.mark.parametrize("agents,question", [
    (["A12"], "What's happening with my $89.99 Online Store 24 charge, and where is the dispute?"),
    (["A14", "A13"], "What is the late payment fee, and how much did I spend on dining this month?"),
    (["A11", "A14"], "How much did I spend this month and which credit card suits my spending?"),
])
def test_manual_selection_matches_auto_routing(p, agents, question):
    """Picking the right agents by hand and letting the platform pick must give the same agents, tools and answer."""
    manual, manual_tools = ask_with(p, question, agents)
    auto, auto_tools = ask(p, question)
    assert sorted(manual_tools) == sorted(auto_tools)
    assert manual.response == auto.response
    assert manual.path == "manual" and auto.path == "full"


def ask_with(p, q, agents, customer="C1001"):
    r = asyncio.run(p.handle(q, customer, agent_ids=agents))
    tools = [(e["data"]["agent"], e["data"]["tool"]) for e in p.audit.trace(r.trace_id) if e["stage"] == "tool"]
    return r, tools
