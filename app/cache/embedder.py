"""Question embeddings for the plan cache.

HashingEmbedder is dependency-free and deterministic: word stems (weighted double) and character trigrams,
hashed into a 1,024-dimension vector and L2-normalised. It runs on the slot-masked question, so "spend on
dining" and "spent on travel" compare as the same question. Swap in a sentence-embedding model behind the
Embedder interface for production.
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_STOP = {"what", "whats", "what's", "is", "the", "a", "an", "and", "how", "much", "did", "i", "have", "my", "me",
         "on", "in", "of", "to", "for", "this", "that", "do", "does", "can", "you", "please", "about", "it", "its",
         "there", "be", "are", "was", "were", "so", "at", "with", "tell"}


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    def __init__(self, dim: int = 1024, stem: int = 4, word_weight: int = 2):
        self.dim, self.stem, self.word_weight = dim, stem, word_weight

    def _bucket(self, feature: str) -> int:
        return int(hashlib.blake2b(feature.encode(), digest_size=8).hexdigest(), 16) % self.dim

    def embed(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for w in re.findall(r"\{[a-z_]+\}|[a-z0-9']+", text.lower()):
            if w in _STOP:
                continue
            if w.startswith("{"):                      # slot names count as words
                v[self._bucket(w)] += self.word_weight
                continue
            v[self._bucket("w:" + w[: self.stem])] += self.word_weight
            for i in range(max(1, len(w) - 2)):
                v[self._bucket("c:" + w[i:i + 3])] += 1
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))
