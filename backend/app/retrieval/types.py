"""Typed retrieval boundary objects (Phase 3B).

Pure dataclasses + exceptions; no ORM / FastAPI imports here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.db.enums import KnowledgeCategory, KnowledgeStatus


class RetrievalError(Exception):
    """Base class for retrieval-layer errors (FastAPI-agnostic)."""


class EmptyQueryError(RetrievalError):
    """Query is empty after deterministic preprocessing."""


@dataclass(frozen=True)
class RetrievalFilter:
    """Simple typed metadata filter (no DSL).

    Default lifecycle rule: only ACTIVE documents are retrieval candidates.
    Pass status=None to include every lifecycle state (internal/testing use only).
    """

    status: KnowledgeStatus | None = KnowledgeStatus.ACTIVE
    category: KnowledgeCategory | None = None
    language: str | None = None


@dataclass(frozen=True)
class RetrievalCandidate:
    """One ranked candidate chunk, traceable back to its source document."""

    chunk_id: int
    document_id: int
    title: str
    category: KnowledgeCategory
    version: str
    section: str | None
    content: str
    score: float
    rank: int
    retrieval_methods: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def traceable_key(self) -> tuple[str, str, str, str]:
        """Document -> Version -> Chunk -> Source identity for assertions."""
        source = str((self.metadata or {}).get("source", ""))
        return (self.title, self.version, str(self.chunk_id), source)


@dataclass(frozen=True)
class RetrievalResult:
    """Typed retrieval outcome. Candidates == () means 'no useful candidates'."""

    query: str
    candidates: tuple[RetrievalCandidate, ...] = ()
    methods: tuple[str, ...] = ("dense", "sparse")
    latency_ms: float | None = None
    filters: RetrievalFilter = field(default_factory=RetrievalFilter)

    @property
    def empty(self) -> bool:
        return len(self.candidates) == 0