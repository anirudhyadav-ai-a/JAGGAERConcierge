"""Agent registry. A15 is the open slot on the whiteboard: add a module with build() and register it here."""
from __future__ import annotations

from ..data import DataCatalog
from ..llm import LLM
from . import cc_recommend, kb_policies, pfm, transactions
from .base import Agent

AGENT_BUILDERS = [cc_recommend.build, transactions.build, kb_policies.build, pfm.build]


class AgentRegistry:
    def __init__(self, llm: LLM, data: DataCatalog, max_steps: int = 4):
        self._agents: dict[str, Agent] = {}
        self.data = data
        for build in AGENT_BUILDERS:
            agent = build()
            agent.data = data.view(agent.id, agent.sources)
            agent.llm = agent.llm or llm
            agent.max_steps = max_steps
            self.register(agent)

    def register(self, agent: Agent) -> None:
        if agent.id in self._agents:
            raise ValueError(f"duplicate agent id {agent.id}")
        self._agents[agent.id] = agent

    def get(self, agent_id: str) -> Agent:
        return self._agents[agent_id]

    def enabled(self) -> list[Agent]:
        return [a for a in self._agents.values() if a.enabled]

    def catalog(self) -> list[dict]:
        return [a.card() for a in self.enabled()]

    def describe(self) -> list[dict]:
        """Catalog plus the data files each agent reads (for /v1/agents and the UI)."""
        return [{**a.card(), "sources": a.sources, "files": a.data.files} for a in self.enabled()]
