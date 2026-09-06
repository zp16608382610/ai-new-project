"""Dense retrieval (Phase 3B) - provider/database agnostic boundary.

DenseRetriever is an interface so a future PostgreSQL+pgvector implementation
can be dropped in without changing the retrieval service or the fusion logic.

LocalDenseRetriever is the deterministic local/dev path:
- loads the ACTIVE candidate set from the repository,
- embeds query + chunk texts through the configured EmbeddingProvider
  (default: DeterministicEmbeddingProvider - a lexical hash-bag stub),
- ranks by cosine similarity.

IMPORTANT distinction (documented, not to be blurred):
DeterministicEmbeddingProvider is NOT a semantic embedding. It only responds to
surface-form overlap, which makes local tests deterministic. Real semantic
quality requires a proper embedding model (Phase 3C+, production path), which
pgvector would store/query at the database level. pgvector is NOT available in
this environment, so database-level vector performance is unmeasured.
"""
from __future__ import annotations

import math
from abc import ABC, abstractmethod

from app.db.repository import KnowledgeChunkRepository
from app.knowledge.embedding import DeterministicEmbeddingProvider, EmbeddingProvider
from app.retrieval.rows import seed_from_chunk
from app.retrieval.types import RetrievalCandidate, RetrievalFilter


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    dot = sum(a * b for a, b in zip(left, right))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0.0 or norm_right == 0.0:
        return 0.0
    return dot / (norm_left * norm_right)


class DenseRetriever(ABC):
    """Interface implemented by any future dense/vector retriever."""

    method = "dense"

    @abstractmethod
    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: RetrievalFilter | None = None,
    ) -> list[RetrievalCandidate]:
        """Return up to top_k ranked candidates."""


class LocalDenseRetriever(DenseRetriever):
    """Deterministic in-process dense path over the current ACTIVE corpus."""

    def __init__(
        self,
        chunk_repository: KnowledgeChunkRepository,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._chunk_repository = chunk_repository
        self._embedding_provider: EmbeddingProvider = (
            embedding_provider or DeterministicEmbeddingProvider()
        )

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: RetrievalFilter | None = None,
    ) -> list[RetrievalCandidate]:
        filter_obj = filters or RetrievalFilter()
        rows = self._chunk_repository.list_retrieval_candidates(
            status=filter_obj.status,
            category=filter_obj.category,
            language=filter_obj.language,
        )
        if not rows:
            return []
        seeds = [seed_from_chunk(row) for row in rows]
        texts = [
            (row.section + "\n" if row.section else "") + row.content for row in rows
        ]

        vectors = self._embedding_provider.embed([*texts, query])
        query_vector = vectors[-1]
        scored: list[tuple[float, int]] = []
        for doc_index, vector in enumerate(vectors[:-1]):
            similarity = cosine_similarity(vector, query_vector)
            if similarity > 0.0:
                scored.append((similarity, doc_index))
        scored.sort(key=lambda pair: (-pair[0], seeds[pair[1]].chunk_id))

        results: list[RetrievalCandidate] = []
        for rank, (score, doc_index) in enumerate(scored[:top_k], start=1):
            seed = seeds[doc_index]
            results.append(
                RetrievalCandidate(
                    chunk_id=seed.chunk_id,
                    document_id=seed.document_id,
                    title=seed.title,
                    category=seed.category,
                    version=seed.version,
                    section=seed.section,
                    content=seed.content,
                    score=score,
                    rank=rank,
                    retrieval_methods=(self.method,),
                    metadata=seed.metadata,
                )
            )
        return results