"""Live check on real Claude: run the four interview questions and verify each answer's facts.

    python scripts/live_check.py              # needs ANTHROPIC_API_KEY; spends roughly $0.03-0.10
    python scripts/live_check.py --max-usd 0.25

Wording varies between runs, so it checks facts (amounts, case ids, policy citations, agents and tools), not
exact text. It stops before any call that could take this run past --max-usd, on top of BUDGET_USD and the
Console workspace limit. Exit code 0 means every check passed.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import settings  # noqa: E402
from app.platform import AgentPlatform  # noqa: E402

CHECKS = [
    {
        "name": "Ambiguous",
        "q": "What about my card?",
        "status": "clarify", "agents": set(), "tools": set(),
        "facts": [],
    },
    {
        "name": "One agent, two tools",
        "q": "What's happening with my $89.99 Online Store 24 charge, and where is the dispute?",
        "status": "ok", "agents": {"A12"}, "tools": {"search_transactions", "get_dispute_status"},
        "facts": ["89.99", "D-55120"],
    },
    {
        "name": "Two agents, one tool each",
        "q": "What is the late payment fee, and how much did I spend on dining this month?",
        "status": "ok", "agents": {"A13", "A14"}, "tools": {"search_policies", "spending_summary"},
        "facts": ["35", "POL-LATE-03", "188.70"],
    },
    {
        "name": "One agent needs another",
        "q": "How much did I spend this month and which credit card suits my spending?",
        "status": "ok", "agents": {"A11", "A14"}, "tools": {"spending_summary", "recommend_cards"},
        "facts": ["1,525.84", "Flat 2% Cash Back"],
    },
]


async def main(max_usd: float) -> int:
    if not os.getenv("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Put it in .env or the environment first.")
        return 2
    cfg = replace(settings, llm_provider="anthropic")
    p = AgentPlatform(cfg, audit_path=None)
    start_spent = p.budget.spent
    print(f"Models: intent={cfg.intent_model} agent={cfg.agent_model} collate={cfg.collate_model}")
    print(f"Ledger before: ${start_spent:.4f} of ${p.budget.limit_usd:.2f}\n")

    failures = 0
    for c in CHECKS:
        if p.budget.spent - start_spent > max_usd:
            print(f"Stopping: this run has spent more than --max-usd ${max_usd:.2f}")
            return 1
        r = await p.handle(c["q"], customer_id="C1001")
        agents = {a["agent_id"] for a in r.context.get("agents", [])}
        tools = set(r.context.get("tools_used", []))
        problems = []
        if r.status != c["status"]:
            problems.append(f"status {r.status!r}, expected {c['status']!r}")
        if agents != c["agents"]:
            problems.append(f"agents {sorted(agents)}, expected {sorted(c['agents'])}")
        if not c["tools"] <= tools:
            problems.append(f"tools {sorted(tools)}, expected at least {sorted(c['tools'])}")
        problems += [f"answer is missing {f!r}" for f in c["facts"] if f not in r.response]
        failures += bool(problems)
        print(f"[{'PASS' if not problems else 'FAIL'}] {c['name']}  "
              f"({r.context['llm_calls']} calls, ${r.context['cost_usd']:.4f}, {r.trace_id})")
        print("       Q: " + c["q"])
        print("       A: " + r.response.replace("\n", "\n          "))
        for pr in problems:
            print("       ! " + pr)
        print()

    spent = p.budget.spent - start_spent
    print(f"This run: ${spent:.4f}. Ledger total: ${p.budget.spent:.4f} of ${p.budget.limit_usd:.2f}.")
    print("All checks passed." if not failures else f"{failures} check(s) failed.")
    return 0 if not failures else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--max-usd", type=float, default=0.30, help="stop once this run has spent more than this")
    sys.exit(asyncio.run(main(ap.parse_args().max_usd)))
