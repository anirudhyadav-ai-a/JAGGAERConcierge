"""Minimal async state graph (nodes + edges + conditional edges), dependency-free.
Same mental model as LangGraph's StateGraph; swap it in later if you prefer."""
from __future__ import annotations

from typing import Any, Awaitable, Callable

END = "__end__"
Node = Callable[[Any], Awaitable[None]]
Router = Callable[[Any], str]


class GraphRecursionError(RuntimeError):
    pass


class StateGraph:
    def __init__(self):
        self.nodes: dict[str, Node] = {}
        self.edges: dict[str, str] = {}
        self.routers: dict[str, Router] = {}
        self.entry: str | None = None

    def add_node(self, name: str, fn: Node) -> "StateGraph":
        self.nodes[name] = fn
        return self

    def add_edge(self, src: str, dst: str) -> "StateGraph":
        self.edges[src] = dst
        return self

    def add_conditional_edges(self, src: str, router: Router) -> "StateGraph":
        self.routers[src] = router
        return self

    def set_entry_point(self, name: str) -> "StateGraph":
        self.entry = name
        return self

    async def run(self, state: Any, max_steps: int = 50, on_step: Callable[[str], None] | None = None) -> Any:
        current = self.entry
        for _ in range(max_steps):
            if current == END:
                return state
            if on_step:
                on_step(current)
            await self.nodes[current](state)
            current = self.routers[current](state) if current in self.routers else self.edges.get(current, END)
        raise GraphRecursionError(f"graph exceeded {max_steps} steps")
