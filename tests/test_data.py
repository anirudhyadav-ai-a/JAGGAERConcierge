"""The data/ folder: each source loads exactly, and each agent can read only its own sources."""
import asyncio
from datetime import timedelta

import pytest

from app.config import Settings
from app.data import SOURCES, DataCatalog, SourceDenied
from app.platform import AgentPlatform
from app.tools import ToolContext

CFG = Settings(llm_provider="mock")


@pytest.fixture
def catalog():
    return DataCatalog(CFG.data_dir, CFG.today)


def test_every_source_file_exists(catalog):
    for name in SOURCES:
        assert catalog.repo(name) is not None


def test_dataset_totals_load_exactly(catalog):
    since = (CFG.today - timedelta(days=30)).isoformat()
    rows = catalog.repo("transactions").for_customer("C1001", since=since)
    by_cat = {}
    for t in rows:
        by_cat[t["category"]] = round(by_cat.get(t["category"], 0) + t["amount"], 2)
    assert by_cat == {"dining": 188.70, "groceries": 392.65, "travel": 644.50, "shopping": 299.99}
    assert round(sum(by_cat.values()), 2) == 1525.84
    assert [t["id"] for t in catalog.repo("transactions").for_customer("C2002")] == ["T9101"]


def test_other_sources(catalog):
    assert catalog.repo("customers").get("C1001")["monthly_budget"]["travel"] == 400
    assert catalog.repo("customers").get("C9999") is None
    assert catalog.repo("disputes").get("T9004")["case_id"] == "D-55120"
    assert {c["id"] for c in catalog.repo("card_products").all()} == {
        "CARD_TRAVEL_PLUS", "CARD_DINING_REWARDS", "CARD_CASHBACK_FLAT"}
    docs = catalog.repo("policies").documents()
    assert {d["id"] for d in docs} == {"POL-DISPUTE-01", "POL-FEES-02", "POL-LATE-03", "POL-LIMIT-04"}
    assert all(2 <= len(d["paragraphs"]) <= 3 for d in docs)


def test_each_agent_declares_its_sources():
    p = AgentPlatform(CFG, audit_path=None)
    assert {a.id: a.sources for a in p.registry.enabled()} == {
        "A11": ["customers", "card_products", "transactions"],
        "A12": ["transactions", "disputes"],
        "A13": ["policies"],
        "A14": ["transactions", "customers"],
    }


def test_agent_cannot_read_undeclared_source():
    p = AgentPlatform(CFG, audit_path=None)
    a13 = p.registry.get("A13").data
    assert a13.policies.chunks()
    with pytest.raises(SourceDenied):
        a13.transactions
    with pytest.raises(SourceDenied):
        p.registry.get("A12").data.customers


def test_tool_reading_an_undeclared_source_fails_cleanly():
    """A tool run under another agent's view gets an error result, not someone else's data."""
    p = AgentPlatform(CFG, audit_path=None)
    spending = next(t for t in p.registry.get("A14").tools if t.name == "spending_summary")
    ctx = ToolContext("C1001", "s", "t", data=p.registry.get("A13").data)
    inv = asyncio.run(spending.run({}, ctx))
    assert not inv.ok and "no access" in inv.output["error"]


def test_unknown_source_is_rejected(catalog):
    with pytest.raises(KeyError):
        catalog.view("A15", ["ledger"])


def test_data_dir_is_configurable(tmp_path):
    with pytest.raises(FileNotFoundError):
        DataCatalog(tmp_path, CFG.today).repo("customers")


@pytest.mark.parametrize("query,expected", [
    ("late payment fee", "POL-LATE-03"),
    ("how long does the dispute policy say it takes", "POL-DISPUTE-01"),
    ("foreign transaction fee", "POL-FEES-02"),
    ("credit limit increase", "POL-LIMIT-04"),
])
def test_policy_retrieval_ranks_the_right_policy_first(query, expected):
    p = AgentPlatform(CFG, audit_path=None)
    a13 = p.registry.get("A13")
    inv = asyncio.run(a13.tools[0].run({"query": query}, ToolContext("C1001", "s", "t", data=a13.data)))
    assert inv.output["passages"][0]["id"] == expected
    assert f"[{expected}]" in inv.output["summary"]


def test_transaction_search_sends_a_bounded_number_of_rows_to_the_llm(tmp_path):
    """Count and total cover every match, but only MAX_ROWS rows go into the prompt, however big the data."""
    from app.agents import transactions as a12
    rows = ["id,customer_id,date,merchant,category,amount,status"]
    rows += [f"T{i},C1,2026-11-{1 + i % 26:02d},Shop {i},shopping,10.00,posted" for i in range(5000)]
    (tmp_path / "transactions.csv").write_text("\n".join(rows) + "\n")
    view = DataCatalog(tmp_path, CFG.today).view("A12", ["transactions", "disputes"])
    out = asyncio.run(a12.search_transactions.run({"query": "shopping"}, ToolContext("C1", "s", "t", data=view))).output
    assert out["count"] == 5000 and out["total"] == 50000.00
    assert len(out["transactions"]) == a12.MAX_ROWS == out["shown"]
    assert out["transactions"][0]["date"] >= out["transactions"][-1]["date"]   # newest first
    assert "newest 20 of 5000 shown" in out["summary"]
