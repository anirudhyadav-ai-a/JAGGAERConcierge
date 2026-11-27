"""Central settings. Everything is overridable through environment variables or a local .env file."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

try:  # .env is optional; never committed (see .gitignore)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover
    pass


def _provider() -> str:
    p = os.getenv("LLM_PROVIDER", "auto").lower()
    if p == "auto":
        return "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "mock"
    return p


@dataclass(frozen=True)
class Settings:
    # "auto" -> Claude when ANTHROPIC_API_KEY is set, otherwise the offline mock.
    llm_provider: str = field(default_factory=_provider)

    # Model per job: a small fast model routes, a stronger model reasons over tools and writes.
    intent_model: str = os.getenv("INTENT_MODEL", "claude-haiku-5-5")
    agent_model: str = os.getenv("AGENT_MODEL", "claude-sonnet-5-5")
    collate_model: str = os.getenv("COLLATE_MODEL", "claude-sonnet-5-5")
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "2048"))
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "60"))

    # Spend protection (second line of defence behind the Console workspace limit)
    budget_usd: float = float(os.getenv("BUDGET_USD", "5.0"))
    max_llm_calls_per_request: int = int(os.getenv("MAX_LLM_CALLS_PER_REQUEST", "12"))
    usage_ledger_path: str = os.getenv("USAGE_LEDGER_PATH", ".usage/ledger.json")

    # Intent layer
    intent_threshold: float = float(os.getenv("INTENT_THRESHOLD", "0.55"))
    history_turns: int = int(os.getenv("HISTORY_TURNS", "4"))

    # Orchestration layer
    max_orchestrator_loops: int = int(os.getenv("MAX_ORCHESTRATOR_LOOPS", "3"))
    max_task_attempts: int = int(os.getenv("MAX_TASK_ATTEMPTS", "2"))
    agent_timeout_s: float = float(os.getenv("AGENT_TIMEOUT_S", "90"))
    agent_max_tool_steps: int = int(os.getenv("AGENT_MAX_TOOL_STEPS", "4"))

    # Data sources: the folder each agent's repositories read from, and the pinned "today"
    data_dir: str = os.getenv("DATA_DIR", str(Path(__file__).resolve().parent.parent / "data"))
    today: date = date.fromisoformat(os.getenv("TODAY", "2026-11-27"))

    # Audit / Log
    audit_path: str = os.getenv("AUDIT_PATH", "audit.jsonl")


settings = Settings()
