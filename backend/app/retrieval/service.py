"""Internal retrieval service (Phase 3B).

RetrievalService.retrieve(query, top_k, filters) is the internal entry point
used by tests (and later by the Agent/Tool layer). It is FastAPI-independent;
no public customer-facing RAG chat endpoint is created in this phase.
"""
from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.db.repository import KnowledgeChunkRepository
from app.knowledge.embedding import DeterministicEmbeddingProvider, EmbeddingProvider
from app.retrieval.bm25 import BM25Retriever
from app.retrieval.dense import DenseRetriever, LocalDenseRetriever
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.text import preprocess_query
from app.retrieval.types import RetrievalFilter, RetrievalResult


class RetrievalService:
    """Query -> QueryProcessor -> (Dense + Sparse) -> RRF -> Ranked Candidates."""

    def __init__(
        self,
        session: Session,
        *,
        embedding_provider: EmbeddingProvider | None = None,
        chunk_repository: KnowledgeChunkRepository | None = None,
        dense_retriever: DenseRetriever | None = None,
        sparse_retriever: BM25Retriever | None = None,
    ) -> None:
        repository = chunk_repository or KnowledgeChunkRepository(session)
        self._embedding_provider: EmbeddingProvider = (
            embedding_provider or DeterministicEmbeddingProvider()
        )
        self._dense = dense_retriever or LocalDenseRetriever(
            repository, self._embedding_provider
        )
        self._sparse = sparse_retriever or BM25Retriever(repository)
        self._chunk_repository = repository

    @property
    def dense_retriever(self) -> DenseRetriever:
        return self._dense

    @property
    def sparse_retriever(self) -> BM25Retriever:
        return self._sparse

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: RetrievalFilter | None = None,
        methods: tuple[str, ...] = ("dense", "sparse"),
    ) -> RetrievalResult:
        """Run hybrid retrieval. Returns [] (empty candidates) on no results."""
        if not isinstance(top_k, int) or top_k < 1:
            raise ValueError("top_k must be a positive integer")
        normalized_query = preprocess_query(query)
        filter_obj = filters or RetrievalFilter()

        method_map = {
            "dense": lambda: self._dense.retrieve(
                normalized_query, top_k=top_k, filters=filter_obj
            ),
            "sparse": lambda: self._sparse.retrieve(
                normalized_query, top_k=top_k, filters=filter_obj
            ),
        }
        unknown = [m for m in methods if m not in method_map]
        if unknown:
            raise ValueError(f"unknown retrieval methods: {unknown}")
        if not methods:
            raise ValueError("methods must not be empty")

        started = time.perf_counter()
        ranked_lists = [method_map[m]() for m in methods]
        fused = reciprocal_rank_fusion(ranked_lists, top_k=top_k)
        latency_ms = round((time.perf_counter() - started) * 1000, 3)

        return RetrievalResult(
            query=normalized_query,
            candidates=tuple(fused),
            methods=tuple(methods),
            latency_ms=latency_ms,
            filters=filter_obj,
        )