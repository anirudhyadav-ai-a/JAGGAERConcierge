"""A13 — Knowledge Base (Policies) agent.

Data source: policies/*.md, split into paragraph chunks. Retrieval is keyword overlap for now;
swap `search_policies` for an embedding + vector-index retriever without changing the agent."""
from __future__ import annotations

import re

from ..tools import tool
from .base import Agent


_STOP = {"the", "and", "for", "are", "was", "what", "when", "how", "does", "did", "can", "may", "say", "says",
         "about", "this", "that", "with", "your", "you", "they", "their", "there", "which", "from", "have",
         "has", "will", "would", "should", "much", "long", "take", "takes", "policy", "policies", "tell"}


def _tokens(s: str) -> set[str]:
    """Content-word stems: drop stop words, compare the first 5 letters so dispute ~ disputes."""
    return {w[:5] for w in re.findall(r"[a-z]+", s.lower()) if len(w) > 2 and w not in _STOP}


def _score(q: set[str], chunk: dict) -> int:
    return 2 * len(q & _tokens(chunk["title"])) + len(q & _tokens(chunk["text"]))


@tool("search_policies",
      "Search bank policy documents (fees, disputes, late payment, credit limits). "
      "Returns passages with policy ids to cite.",
      {"query": {"type": "string"}, "top_k": {"type": "integer", "default": 2}}, required=["query"])
def search_policies(ctx, query: str, top_k: int = 2):
    q = _tokens(query)
    scored = sorted(((_score(q, c), c) for c in ctx.data.policies.chunks()),
                    key=lambda x: x[0], reverse=True)
    hits, seen = [], set()
    for s, c in scored:          # best chunk per policy, top_k policies
        if s > 0 and c["id"] not in seen:
            hits.append(c)
            seen.add(c["id"])
        if len(hits) == top_k:
            break
    if not hits:
        return {"passages": [], "summary": "No matching policy found."}
    return {"passages": hits,
            "summary": " ".join(f"{p['text']} [{p['id']}]" for p in hits[:1])}


def build() -> Agent:
    return Agent(
        id="A13", name="Policy Knowledge Base",
        description="Answers questions about bank policies, fees, terms and procedures with citations.",
        keywords=["card", "policy", "policies", "fee", "fees", "rule", "terms", "how long", "allowed",
                  "late", "limit increase", "foreign"],
        system_prompt="Answer only from retrieved policy passages and cite the policy id in brackets. "
                      "If nothing relevant is retrieved, say so.",
        tools=[search_policies],
        sources=["policies"],
    )
