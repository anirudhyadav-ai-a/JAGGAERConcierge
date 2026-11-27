"""A11 — Credit Card Recommendation agent.

Data sources: customers.json (credit band, existing cards), card_products.json (catalog),
transactions.csv (last 30 days of spend to estimate rewards)."""
from __future__ import annotations

from collections import defaultdict

from ..tools import ToolError, tool
from .base import Agent

_BANDS = ["poor", "fair", "good", "excellent"]


@tool("get_customer_profile", "Get the customer's credit score band, income band and existing cards.")
def get_customer_profile(ctx):
    p = ctx.data.customers.get(ctx.customer_id)
    if not p:
        raise ToolError("customer not found")
    return {**p, "summary": f"Credit band {p['credit_score_band']}, holds {', '.join(p['existing_cards'])}."}


@tool("recommend_cards",
      "Rank eligible credit card products for this customer by estimated annual rewards, "
      "using their last 30 days of spending by category. Use for card recommendation questions.",
      {"query": {"type": "string", "description": "What the customer wants from a card"},
       "top_n": {"type": "integer", "default": 2}})
def recommend_cards(ctx, query: str = "", top_n: int = 2):
    profile = ctx.data.customers.get(ctx.customer_id)
    if not profile:
        raise ToolError("customer not found")
    spend = defaultdict(float)
    for t in ctx.data.transactions.for_customer(ctx.customer_id, since=ctx.data.since(30)):
        spend[t["category"]] += t["amount"]
    band = _BANDS.index(profile["credit_score_band"])
    ranked = []
    for c in ctx.data.card_products.all():
        if _BANDS.index(c["min_score_band"]) > band or c["id"] in profile["existing_cards"]:
            continue
        monthly = sum(amt * c["rewards"].get(cat, c["rewards"]["default"]) / 100 for cat, amt in spend.items())
        ranked.append({"card_id": c["id"], "name": c["name"], "annual_fee": c["annual_fee"],
                       "est_net_annual_value": round(monthly * 12 - c["annual_fee"], 2), "intro": c["intro"]})
    ranked.sort(key=lambda r: r["est_net_annual_value"], reverse=True)
    top = ranked[:top_n]
    summary = "; ".join(f"{r['name']} (~${r['est_net_annual_value']}/yr net, fee ${r['annual_fee']})" for r in top)
    return {"recommendations": top, "spend_basis": dict(spend),
            "summary": f"Best fits for your spending: {summary}."}


def build() -> Agent:
    return Agent(
        id="A11", name="Credit Card Recommendation",
        description="Recommends credit card products that fit the customer's spending and eligibility.",
        keywords=["card", "credit card", "recommend", "rewards", "cashback", "cash back", "which card", "apply"],
        system_prompt="You recommend credit cards. Always ground recommendations in the recommend_cards tool "
                      "output. State the estimated value and annual fee. Never promise approval. Mention only cards "
                      "and figures that appear in tool output: the customer's existing cards have no reward data, "
                      "so don't compare against them or estimate what they earn.",
        tools=[get_customer_profile, recommend_cards],
        sources=["customers", "card_products", "transactions"],
    )
