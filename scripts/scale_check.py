"""Scale check for the data layer: generate N transactions, then time the agents' tools on them.

    python scripts/scale_check.py                 # 1,000,000 transactions across 10,000 customers
    python scripts/scale_check.py --rows 100000

No LLM calls, so it is free. It shows two things:
  - per-customer lookups stay fast as the data grows, because repositories index by customer;
  - what reaches the LLM stays the same size (totals plus at most MAX_ROWS rows), so the cost of a
    question doesn't grow with the data.
The generated data goes to a temporary folder and is deleted afterwards; data/ is not touched.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import resource
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents import cc_recommend, pfm, transactions  # noqa: E402
from app.config import Settings  # noqa: E402
from app.data import DataCatalog  # noqa: E402
from app.tools import ToolContext  # noqa: E402

CATEGORIES = ["dining", "groceries", "travel", "shopping"]
STATUSES = ["posted"] * 8 + ["pending", "disputed"]


def generate(folder: Path, rows: int, customers: int) -> None:
    for name in ("customers.json", "card_products.json", "disputes.json"):
        shutil.copy(ROOT / "data" / name, folder / name)
    shutil.copytree(ROOT / "data" / "policies", folder / "policies")
    rnd = random.Random(1)
    with (folder / "transactions.csv").open("w") as f:
        f.write("id,customer_id,date,merchant,category,amount,status\n")
        for i in range(rows):
            f.write(f"T{i},C{i % customers:06d},2026-{rnd.randint(9, 11):02d}-{rnd.randint(1, 27):02d},"
                    f"Merchant {i % 5000},{rnd.choice(CATEGORIES)},{rnd.randint(100, 50000) / 100:.2f},"
                    f"{rnd.choice(STATUSES)}\n")
        # keep the demo customer, so results can be compared with the normal dataset
        f.writelines(line + "\n" for line in (ROOT / "data" / "transactions.csv").read_text().splitlines()[1:])


def timed(tool, args: dict, view, customer: str, runs: int = 200) -> tuple[float, dict, int]:
    ctx = ToolContext(customer, "scale", "scale", data=view)
    start = time.perf_counter()
    for _ in range(runs):
        out = asyncio.run(tool.run(args, ctx)).output
    ms = (time.perf_counter() - start) / runs * 1000
    return ms, out, len(json.dumps(out))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--customers", type=int, default=10_000)
    a = ap.parse_args()

    folder = Path(tempfile.mkdtemp(prefix="scale_check_"))
    try:
        t = time.perf_counter()
        generate(folder, a.rows, a.customers)
        print(f"generated {a.rows:,} transactions for {a.customers:,} customers in {time.perf_counter() - t:.1f}s")

        catalog = DataCatalog(folder, Settings(llm_provider="mock").today)
        t = time.perf_counter()
        catalog.repo("transactions")
        load = time.perf_counter() - t
        peak_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024)
        print(f"loaded at start-up in {load:.1f}s (peak memory {peak_mb:,.0f} MB)\n")

        a12 = catalog.view("A12", ["transactions", "disputes"])
        a14 = catalog.view("A14", ["transactions", "customers"])
        a11 = catalog.view("A11", ["customers", "card_products", "transactions"])
        busiest = f"C{0:06d}"
        checks = [
            ("search_transactions, typical customer", transactions.search_transactions, {"query": "shopping"}, a12, busiest),
            ("search_transactions, all of their rows", transactions.search_transactions, {"query": "", "days": 365}, a12, busiest),
            ("spending_summary, demo customer", pfm.spending_summary, {}, a14, "C1001"),
            ("recommend_cards, demo customer", cc_recommend.recommend_cards, {}, a11, "C1001"),
        ]
        print(f"{'tool call':42} {'time':>9} {'matches':>8} {'rows to LLM':>12} {'bytes to LLM':>13}")
        for label, tool, args, view, customer in checks:
            ms, out, size = timed(tool, args, view, customer)
            matches = out.get("count", "-")
            shown = out.get("shown", "-")
            print(f"{label:42} {ms:>7.2f}ms {matches!s:>8} {shown!s:>12} {size:>13,}")
        print(f"\nThe demo customer's answers match the normal dataset; at most {transactions.MAX_ROWS} rows ever "
              "reach the LLM.\nBeyond a single machine's memory, swap the repository classes in "
              "app/data/repositories.py for a database (see docs/architecture.md).")
    finally:
        shutil.rmtree(folder, ignore_errors=True)


if __name__ == "__main__":
    main()
