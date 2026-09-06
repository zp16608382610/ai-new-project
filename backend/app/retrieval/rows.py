"""Row -> candidate seed conversion (internal helper)."""
from __future__ import annotations

from app.db.models import KnowledgeChunk
from app.retrieval.types import RetrievalCandidate


def seed_from_chunk(chunk: KnowledgeChunk) -> RetrievalCandidate:
    """Build an unscored candidate from a chunk row (document loaded)."""
    document = chunk.document
    return RetrievalCandidate(
        chunk_id=chunk.id,
        document_id=document.id,
        title=document.title,
        category=document.category,
        version=document.version,
        section=chunk.section,
        content=chunk.content,
        score=0.0,
        rank=0,
        retrieval_methods=(),
        metadata=dict(chunk.metadata_json or {}),
    )