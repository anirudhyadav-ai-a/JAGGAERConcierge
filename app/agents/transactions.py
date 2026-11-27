"""A12 — Transaction Query agent.

Data sources: transactions.csv (charges, payments, statuses), disputes.json (dispute cases)."""
from __future__ import annotations

from ..tools import ToolError, tool
from .base import Agent

_CATS = ["dining", "groceries", "travel", "shopping"]
# Count and total cover every match; only this many rows go back to the LLM, newest first, so the prompt
# (and its cost) stays the same size whether a customer has 10 transactions or 10 million.
MAX_ROWS = 20


@tool("search_transactions",
      "Search the customer's recent transactions by merchant, category or status (pending, disputed). "
      "Use for questions about specific charges, payments or purchases.",
      {"query": {"type": "string"},
       "category": {"type": "string", "enum": _CATS},
       "days": {"type": "integer", "default": 30}})
def search_transactions(ctx, query: str = "", category: str | None = None, days: int = 30):
    q = query.lower()
    rows = ctx.data.transactions.for_customer(ctx.customer_id, since=ctx.data.since(days))
    if category:
        rows = [t for t in rows if t["category"] == category]
    else:
        cat_hit = [c for c in _CATS if c in q or (c == "dining" and "restaurant" in q)]
        status_hit = [s for s in ("pending", "disputed") if s in q]
        merchant_hit = [t for t in rows if t["merchant"].lower() in q]
        if merchant_hit:
            rows = merchant_hit
        elif status_hit:
            rows = [t for t in rows if t["status"] in status_hit]
        elif cat_hit:
            rows = [t for t in rows if t["category"] in cat_hit]
    total = round(sum(t["amount"] for t in rows), 2)
    rows.sort(key=lambda t: (t["date"], t["id"]), reverse=True)
    shown = rows[:MAX_ROWS]
    top = ", ".join(f"{t['merchant']} ${t['amount']:,.2f} on {t['date']} ({t['status']})" for t in shown[:5])
    more = f" (newest {len(shown)} of {len(rows)} shown)" if len(rows) > len(shown) else ""
    return {"transactions": shown, "count": len(rows), "total": total, "shown": len(shown),
            "summary": f"Found {len(rows)} transaction(s) totalling ${total:,.2f}{more}: {top}."}


@tool("get_dispute_status", "Get the status of a disputed transaction / dispute case.",
      {"transaction_id": {"type": "string"}, "query": {"type": "string"}})
def get_dispute_status(ctx, transaction_id: str | None = None, query: str = ""):
    own = {t["id"] for t in ctx.data.transactions.for_customer(ctx.customer_id)}
    ids = [transaction_id] if transaction_id else [i for i in ctx.data.disputes.transaction_ids() if i in own]
    if not ids or ids[0] not in own:
        raise ToolError("no dispute found for this customer")
    d = ctx.data.disputes.get(ids[0])
    if not d:
        raise ToolError("transaction is not disputed")
    return {**d, "transaction_id": ids[0],
            "summary": f"Dispute {d['case_id']} on {ids[0]} is {d['stage'].replace('_', ' ')}; "
                       f"provisional credit {'issued' if d['provisional_credit'] else 'not issued'}, "
                       f"~{d['expected_resolution_days']} days to resolve."}


def build() -> Agent:
    return Agent(
        id="A12", name="Transaction Queries",
        description="Answers questions about the customer's transactions, charges, pending items and disputes.",
        keywords=["card", "transaction", "charge", "charged", "payment", "purchase", "pending", "dispute", "disputed",
                  "spent at", "merchant", "refund"],
        system_prompt="You answer transaction questions using only tool data. Quote amounts and dates exactly.",
        tools=[search_transactions, get_dispute_status],
        sources=["transactions", "disputes"],
    )
