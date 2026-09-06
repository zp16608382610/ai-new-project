"""Retrieval -> Reranking -> Context Assembly orchestration (Phase 3C).

Internal, FastAPI-independent entry point used by tests now and by the Agent
layer later. Keeps the two-stage Top-K contract and the context budget in one
typed config so the numbers are not scattered across call sites:

    RetrievalService.retrieve(top_k=retrieval_top_k)     # default 20
      -> DeterministicReranker.rerank(top_k=rerank_top_k) # default 5
      -> ContextAssembler.assemble(...)                   # default budget 2000

No public customer-facing RAG endpoint is created in this phase.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.retrieval.context import ContextAssembler, ContextBudget, ContextPackage
from app.retrieval.rerank import DeterministicReranker
from app.retrieval.service import RetrievalService
from app.retrieval.types import RetrievalFilter

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


@dataclass(frozen=True)
class RetrievalPipelineConfig:
    """Typed two-stage Top-K + context budget configuration."""

    retrieval_top_k: int = 20
    rerank_top_k: int = 5
    max_context_tokens: int = 2000
    reserve_tokens: int = 0

    def __post_init__(self) -> None:
        for name in (
            "retrieval_top_k",
            "rerank_top_k",
            "max_context_tokens",
            "reserve_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
        if self.retrieval_top_k < 1:
            raise ValueError("retrieval_top_k must be >= 1")
        if self.rerank_top_k < 1:
            raise ValueError("rerank_top_k must be >= 1")
        if self.rerank_top_k > self.retrieval_top_k:
            raise ValueError("rerank_top_k must be <= retrieval_top_k")
        if self.max_context_tokens < 1:
            raise ValueError("max_context_tokens must be >= 1")
        if self.reserve_tokens < 0:
            raise ValueError("reserve_tokens must be >= 0")
        if self.reserve_tokens >= self.max_context_tokens:
            raise ValueError("reserve_tokens must be < max_context_tokens")


class RetrievalPipeline:
    """Hybrid Retrieval (top 20) -> Reranker (top 5) -> Context Assembly."""

    def __init__(
        self,
        session: "Session",
        *,
        config: RetrievalPipelineConfig | None = None,
        service: RetrievalService | None = None,
        reranker: DeterministicReranker | None = None,
        assembler: ContextAssembler | None = None,
    ) -> None:
        self._config = config or RetrievalPipelineConfig()
        self._service = service or RetrievalService(session)
        self._reranker = reranker or DeterministicReranker()
        self._assembler = assembler or ContextAssembler(
            ContextBudget(
                max_tokens=self._config.max_context_tokens,
                reserve_tokens=self._config.reserve_tokens,
            )
        )

    @property
    def config(self) -> RetrievalPipelineConfig:
        return self._config

    @property
    def service(self) -> RetrievalService:
        return self._service

    @property
    def reranker(self) -> DeterministicReranker:
        return self._reranker

    @property
    def assembler(self) -> ContextAssembler:
        return self._assembler

    def run(
        self,
        query: str,
        *,
        filters: RetrievalFilter | None = None,
        methods: tuple[str, ...] = ("dense", "sparse"),
    ) -> ContextPackage:
        """Run the full internal pipeline and return the final context."""
        result = self._service.retrieve(
            query,
            top_k=self._config.retrieval_top_k,
            filters=filters,
            methods=methods,
        )
        available = self._assembler.budget.available_tokens
        if result.empty:
            return ContextPackage(
                query=result.query,
                items=(),
                total_items=0,
                truncated=False,
                token_budget=available,
                estimated_tokens=0,
            )
        reranked = self._reranker.rerank(
            result.query, result.candidates, top_k=self._config.rerank_top_k
        )
        if not reranked:
            return ContextPackage(
                query=result.query,
                items=(),
                total_items=0,
                truncated=False,
                token_budget=available,
                estimated_tokens=0,
            )
        return self._assembler.assemble(result.query, reranked)