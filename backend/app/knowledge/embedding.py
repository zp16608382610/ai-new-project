"""Embedding provider abstraction (Phase 3A preparation / Phase 3B local path).

Principles:
- No external LLM/embedding API is called in this phase, and no single vendor
  is hard-coded into retrieval logic (EmbeddingProvider stays the swap point).
- DeterministicEmbeddingProvider is a LOCAL, NON-SEMANTIC pseudo-embedding:
    * Phase 3A: interface/dimension-stability contract only.
    * Phase 3B: local dense path. Vector = L2-normalized bag of character
      bigrams hashed into `dimensions` slots, so cosine similarity is
      deterministic and responds ONLY to surface-form overlap.
  It is NOT a semantic-quality embedding: paraphrases that share no surface
  forms will not match, exactly like a lexical hash-bag. Production retrieval
  requires a real embedding model + database-level vectors (pgvector), which
  belongs to a later phase and is NOT available/verified in this environment.
"""
from __future__ import annotations

import hashlib
import math
import unicodedata
from typing import Protocol, Sequence


class EmbeddingProvider(Protocol):
    """Provider contract. Swap point for a real embedding model later."""

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one dense vector per input text."""
        ...


class DeterministicEmbeddingProvider:
    """Local deterministic pseudo-embedding (NOT for semantic retrieval)."""

    dimensions: int = 128

    def __init__(self, dimensions: int = 128) -> None:
        self.dimensions = dimensions

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text) for text in texts]

    # Keep in sync with app/retrieval/text.py CJK_STOP_CHARS.
    _STOP = frozenset("的了么吗呢啊吧呀哦嘛喔在是有可也这那和与就都会能把请向问什么怎让别不还又")

    def _gram(self, text: str) -> list[str]:
        compact = "".join(
            ch for ch in unicodedata.normalize("NFKC", text) if ch.isalnum()
        )
        chars = list(compact)
        if not chars:
            return []
        if len(chars) == 1:
            return [chars[0]] if chars[0] not in self._STOP else []
        grams = []
        for left, right in zip(chars, chars[1:]):
            if left not in self._STOP and right not in self._STOP:
                grams.append(left + right)
        return grams

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        for gram in self._gram(text):
            digest = hashlib.sha256(gram.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            vector[index] += 1.0
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [round(value / norm, 6) for value in vector]


__all__ = ["DeterministicEmbeddingProvider", "EmbeddingProvider"]