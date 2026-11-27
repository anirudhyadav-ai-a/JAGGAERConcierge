"""Load benchmark: N concurrent questions through the platform, with and without the plan cache.

    python bench.py                    # 200 requests, mock LLM with 400 ms per call
    python bench.py --requests 500 --latency-ms 250

Free: it uses the deterministic mock with a simulated latency per LLM call, so the effect of the plan cache and
of parallel agent waves is visible without spending money. It runs the mix of interview questions (rows 2-5)
twice, cache off and cache on, and prints p50/p95 latency, requests per second, cache hit rate and average LLM calls.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import time

os.environ.setdefault("LLM_PROVIDER", "mock")

from app.config import Settings  # noqa: E402
from app.platform import AgentPlatform  # noqa: E402

MIX = [
    "What's happening with my $89.99 Online Store 24 charge, and where is the dispute?",
    "What is the late payment fee, and how much did I spend on dining this month?",
    "How much did I spend this month and which credit card suits my spending?",
    "How much have I spent on dining this month and what's the late fee?",
    "How much did I spend on travel this month, and what's the late fee?",
]


async def run(n: int, latency_ms: int, cache: bool) -> dict:
    p = AgentPlatform(Settings(llm_provider="mock", mock_llm_latency_ms=latency_ms, cache_enabled=cache,
                               cache_path="", max_llm_calls_per_request=50), audit_path=None)
    # Warm-up: one full run per question shape, as a real deployment would have built up over time.
    for q in MIX:
        await p.handle(q, "C1001")

    async def one(i: int):
        t = time.perf_counter()
        r = await p.handle(MIX[i % len(MIX)], "C1001" if i % 2 else "C2002")
        return (time.perf_counter() - t) * 1000, r

    start = time.perf_counter()
    results = await asyncio.gather(*(one(i) for i in range(n)))
    wall = time.perf_counter() - start
    lat = sorted(ms for ms, _ in results)
    return {
        "p50": statistics.median(lat),
        "p95": lat[int(0.95 * (len(lat) - 1))],
        "rps": n / wall,
        "hit_rate": sum(r.path == "cache" for _, r in results) / n,
        "calls": statistics.mean(r.context["llm_calls"] for _, r in results),
        "ok": sum(r.status == "ok" for _, r in results),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--latency-ms", type=int, default=400, help="simulated latency per LLM call")
    a = ap.parse_args()
    print(f"{a.requests} concurrent requests, mix of rows 2-5, {a.latency_ms} ms per simulated LLM call\n")
    print(f"{'':12} {'p50':>8} {'p95':>8} {'req/s':>8} {'cache hits':>11} {'LLM calls/req':>14} {'ok':>6}")
    for label, cache in (("cache off", False), ("cache on", True)):
        r = asyncio.run(run(a.requests, a.latency_ms, cache))
        print(f"{label:12} {r['p50']:>6.0f}ms {r['p95']:>6.0f}ms {r['rps']:>8.1f} {r['hit_rate']:>10.0%} "
              f"{r['calls']:>14.2f} {r['ok']:>6}")
    print("\nOn Claude the same ratio of LLM calls applies to cost: a cache hit makes 2 calls (route + write) "
          "instead of 4-6.")


if __name__ == "__main__":
    main()
