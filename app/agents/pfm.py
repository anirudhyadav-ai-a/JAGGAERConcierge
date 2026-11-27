"""A14 — Personal Finance Management (PFM) agent.

Data sources: transactions.csv (spend by category), customers.json (monthly budget per category)."""
from __future__ import annotations

from collections import defaultdict

from ..tools import tool
from .base import Agent


def _spend(ctx, days: int = 30) -> dict[str, float]:
    s = defaultdict(float)
    for t in ctx.data.transactions.for_customer(ctx.customer_id, since=ctx.data.since(days)):
        s[t["category"]] += t["amount"]
    return {k: round(v, 2) for k, v in s.items()}


@tool("spending_summary", "Total spend this month (last 30 days) by category, highlighting the "
      "biggest category. Use for 'how much did I spend' and saving questions. Pass category to get one "
      "category's total.",
      {"query": {"type": "string"},
       "category": {"type": "string", "enum": ["dining", "groceries", "travel", "shopping"]}})
def spending_summary(ctx, query: str = "", category: str | None = None):
    s = _spend(ctx)
    if category:
        amt = s.get(category, 0.0)
        return {"category": category, "total": amt, "days": 30,
                "summary": f"You spent ${amt:,.2f} on {category} in the last 30 days."}
    total = round(sum(s.values()), 2)
    top = max(s, key=s.get) if s else None
    return {"by_category": s, "total": total, "top_category": top,
            "summary": f"You spent ${total:,.2f} in the last 30 days; biggest category is {top} (${s.get(top, 0):,.2f})."}


@tool("budget_status", "Compare this month's spending against the customer's budget per category "
      "and list categories that are over budget.", {"query": {"type": "string"}})
def budget_status(ctx, query: str = ""):
    s = _spend(ctx)
    budget = ctx.data.customers.get(ctx.customer_id)["monthly_budget"]
    rows = {c: {"spent": s.get(c, 0), "budget": b, "remaining": round(b - s.get(c, 0), 2)} for c, b in budget.items()}
    over = [c for c, r in rows.items() if r["remaining"] < 0]
    msg = f"Over budget in: {', '.join(over)}." if over else "You're within budget in every category."
    return {"categories": rows, "over_budget": over, "summary": msg}


def build() -> Agent:
    return Agent(
        id="A14", name="Personal Finance (PFM)",
        description="Answers personal finance questions: spending analysis, budgets, saving.",
        keywords=["spend", "spending", "budget", "save", "saving", "savings", "where does my money",
                  "expenses", "afford"],
        system_prompt="You are a personal finance coach. Use tool numbers. You may add one short practical "
                      "suggestion, but don't invent prices, savings or other amounts that aren't in tool output. "
                      "Do not give investment advice.",
        tools=[spending_summary, budget_status],
        sources=["transactions", "customers"],
    )
