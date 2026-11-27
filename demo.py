"""Run the interview scenarios end to end and show route, agents, tools, LLM calls, cost and answer.

    python demo.py                 # real Claude calls; needs ANTHROPIC_API_KEY (in the environment or .env)
    python demo.py "your question" # ask your own question
    LLM_PROVIDER=mock python demo.py   # offline mock, for CI only
"""
import asyncio
import logging
import sys

from app.platform import AgentPlatform

logging.getLogger("agent_platform").setLevel(logging.WARNING)

SCENARIOS = [
    ("Ambiguous", "What about my card?"),
    ("Single agent, 2 tools", "What's happening with my $89.99 Online Store 24 charge, and where is the dispute?"),
    ("Multi-agent, 1 tool each", "What is the late payment fee, and how much did I spend on dining this month?"),
    ("Dependent agents", "How much did I spend this month and which credit card suits my spending?"),
]


async def main(questions):
    try:
        p = AgentPlatform(audit_path=None)
    except RuntimeError as exc:
        raise SystemExit(f"Cannot start: {exc}")
    print(f"LLM provider: {p.llm.name}"
          + (f" | budget remaining ${p.budget.remaining:.4f}" if p.budget is not None else " (offline mock: test mode, not Claude)"))
    for i, (label, q) in enumerate(questions, 1):
        r = await p.handle(q, customer_id="C1001")
        tools = [f"{e['data']['agent']}.{e['data']['tool']}" for e in p.audit.trace(r.trace_id) if e["stage"] == "tool"]
        agents = ",".join(a["agent_id"] for a in r.context.get("agents", [])) or "-"
        print("\n" + "=" * 100)
        print(f"[{i}] {label}")
        print(f"USER    {q}")
        print(f"ROUTE   status={r.status}  path={r.path}  agents={agents}  llm_calls={r.context['llm_calls']}"
              f"  cost=${r.context['cost_usd']:.4f}")
        print(f"TOOLS   {'  '.join(tools) or '-'}")
        print("ANSWER  " + r.response.replace("\n", "\n        "))
        print(f"TRACE   {r.trace_id}  ({len(p.audit.trace(r.trace_id))} audit events)")
    if p.budget is not None:
        print(f"\nTotal spent so far: ${p.budget.spent:.4f} of ${p.budget.limit_usd:.2f}")


if __name__ == "__main__":
    qs = [("Your question", " ".join(sys.argv[1:]))] if len(sys.argv) > 1 else SCENARIOS
    asyncio.run(main(qs))
