"""Spend protection.

BudgetGuard keeps a local USD ledger from the `usage` block of every Claude response and refuses any
call whose worst-case cost (input estimate + max_tokens of output) could push spend past BUDGET_USD.
The ledger is persisted so restarts don't reset it. It is per machine, so the Console workspace spend
limit stays the authoritative cap; this guard is the second line of defence.

Prices are USD per million tokens, from https://platform.claude.com/docs/en/about-claude/pricing.
Unknown models are charged at a deliberately high rate so the guard errs on the safe side.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path

PRICES_PER_MTOK = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),    # prompts up to 100k tokens (ours are far smaller)
    "claude-opus-5-5": (4.00, 20.00),
}
UNKNOWN_MODEL_PRICE = (10.00, 50.00)


class BudgetExceeded(RuntimeError):
    pass


class CallLimitExceeded(RuntimeError):
    pass


def price_for(model: str) -> tuple[float, float]:
    for prefix, price in PRICES_PER_MTOK.items():
        if model == prefix or model.startswith(prefix + "-"):
            return price
    return UNKNOWN_MODEL_PRICE


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pin, pout = price_for(model)
    return (input_tokens * pin + output_tokens * pout) / 1_000_000


class BudgetGuard:
    def __init__(self, limit_usd: float, ledger_path: str | None):
        self.limit_usd = limit_usd
        self.path = Path(ledger_path) if ledger_path else None
        self._lock = threading.Lock()
        self._reserved = 0.0
        self.state = {"spent_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "by_model": {}}
        if self.path and self.path.exists():
            self.state.update(json.loads(self.path.read_text()))

    @property
    def spent(self) -> float:
        return self.state["spent_usd"]

    @property
    def remaining(self) -> float:
        return max(0.0, self.limit_usd - self.spent)

    def reserve(self, model: str, est_input_tokens: int, max_output_tokens: int) -> float:
        """Reserve the worst-case cost of a call before making it. Raises if it could exceed the budget."""
        worst = cost_usd(model, est_input_tokens, max_output_tokens)
        with self._lock:
            if self.spent + self._reserved + worst > self.limit_usd:
                raise BudgetExceeded(
                    f"LLM budget reached: spent ${self.spent:.4f} of ${self.limit_usd:.2f}; "
                    f"next call could cost up to ${worst:.4f}")
            self._reserved += worst
        return worst

    def settle(self, reserved: float, model: str, input_tokens: int, output_tokens: int) -> float:
        actual = cost_usd(model, input_tokens, output_tokens)
        with self._lock:
            self._reserved = max(0.0, self._reserved - reserved)
            s = self.state
            s["spent_usd"] = round(s["spent_usd"] + actual, 6)
            s["calls"] += 1
            s["input_tokens"] += input_tokens
            s["output_tokens"] += output_tokens
            m = s["by_model"].setdefault(model, {"calls": 0, "usd": 0.0})
            m["calls"] += 1
            m["usd"] = round(m["usd"] + actual, 6)
            s["updated_at"] = datetime.now(timezone.utc).isoformat()
            if self.path:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(s, indent=2))
        return actual

    def release(self, reserved: float) -> None:
        with self._lock:
            self._reserved = max(0.0, self._reserved - reserved)

    def snapshot(self) -> dict:
        return {**self.state, "limit_usd": self.limit_usd, "remaining_usd": round(self.remaining, 6)}
