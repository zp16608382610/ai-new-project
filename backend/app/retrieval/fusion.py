"""Rank-based fusion (Phase 3B): Reciprocal Rank Fusion (RRF).

Why RRF instead of weighted score sums: dense cosine similarity and BM25 scores
live on different scales; adding them requires normalization that is hard to
justify. RRF only uses ranks, needs no score calibration, is deterministic, and
gives documents found by both retrievers a natural boost.

score(chunk) = sum over methods of 1 / (k + rank_in_method)
"""
from __future__ import annotations

from collections import defaultdict

from app.retrieval.types import RetrievalCandidate


def reciprocal_rank_fusion(
    ranked_lists: list[list[RetrievalCandidate]],
    *,
    k: int = 60,
    top_k: int | None = None,
) -> list[RetrievalCandidate]:
    """Fuse ranked candidate lists into a deterministic RRF ranking."""
    accumulated: dict[int, float] = defaultdict(float)
    methods: dict[int, set[str]] = defaultdict(set)
    originals: dict[int, RetrievalCandidate] = {}

    for candidates in ranked_lists:
        for rank, candidate in enumerate(candidates, start=1):
            accumulated[candidate.chunk_id] += 1.0 / (k + rank)
            methods[candidate.chunk_id].update(candidate.retrieval_methods)
            originals[candidate.chunk_id] = candidate

    fused: list[RetrievalCandidate] = []
    for chunk_id, score in accumulated.items():
        original = originals[chunk_id]
        fused.append(
            RetrievalCandidate(
                chunk_id=original.chunk_id,
                document_id=original.document_id,
                title=original.title,
                category=original.category,
                version=original.version,
                section=original.section,
                content=original.content,
                score=round(score, 6),
                rank=0,
                retrieval_methods=tuple(sorted(methods[chunk_id])),
                metadata=original.metadata,
            )
        )

    fused.sort(key=lambda candidate: (-candidate.score, candidate.chunk_id))
    for rank, candidate in enumerate(fused, start=1):
        candidate = RetrievalCandidate(
            chunk_id=candidate.chunk_id,
            document_id=candidate.document_id,
            title=candidate.title,
            category=candidate.category,
            version=candidate.version,
            section=candidate.section,
            content=candidate.content,
            score=candidate.score,
            rank=rank,
            retrieval_methods=candidate.retrieval_methods,
            metadata=candidate.metadata,
        )
        fused[rank - 1] = candidate
    return fused[:top_k] if top_k is not None else fused