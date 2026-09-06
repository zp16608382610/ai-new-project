"""Knowledge ingestion service (Phase 3A).

Pipeline:

    KnowledgeSpec (source)
        -> normalize (deterministic text)
        -> checksum (sha256 of normalized text)
        -> section-aware chunking
        -> KnowledgeDocument + KnowledgeChunk rows
        -> Database

Design notes:
- FastAPI-independent: no request/response objects and no route wiring here.
- Immutable versioned documents: natural key (category, title, version)
  identifies one document. Re-ingesting identical content is a no-op
  (idempotent). Re-ingesting different content under the same key is
  rejected - policies evolve by creating a new version instead.
- Commit happens at the service layer, matching Phase 2 conventions.
- Embedding / vectors are intentionally absent (Phase 3B).
"""
from __future__ import annotations

import hashlib
from datetime import date

from sqlalchemy.orm import Session

from app.db.enums import KnowledgeStatus
from app.db.models import KnowledgeDocument
from app.db.repository import KnowledgeChunkRepository, KnowledgeDocumentRepository
from app.knowledge.chunker import chunk_document, normalize_text
from app.knowledge.documents import KnowledgeSpec


class KnowledgeIngestionError(RuntimeError):
    """Raised when ingestion cannot proceed (e.g. immutable version conflict)."""


def checksum_of(text: str) -> str:
    """sha256 of the normalized UTF-8 text - stable idempotency key."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class IngestionService:
    """Knowledge Source -> Normalize/Chunk -> Repository -> Database."""

    def __init__(
        self,
        session: Session,
        *,
        document_repo: KnowledgeDocumentRepository | None = None,
        chunk_repo: KnowledgeChunkRepository | None = None,
    ) -> None:
        self._session = session
        self._document_repo = document_repo or KnowledgeDocumentRepository(session)
        self._chunk_repo = chunk_repo or KnowledgeChunkRepository(session)

    @property
    def session(self) -> Session:
        """Expose the bound session (repository/service convention)."""
        return self._session

    def ingest_spec(self, spec: KnowledgeSpec) -> tuple[KnowledgeDocument, bool]:
        """Ingest one versioned document.

        Returns (document, created). A second ingestion of identical content
        under the same natural key returns (existing_document, False) without
        touching the stored chunks.
        """
        normalized = normalize_text(spec.content)
        checksum = checksum_of(normalized)

        existing = self._document_repo.get_by_natural_key(
            spec.category, spec.title, spec.version
        )
        if existing is not None:
            if existing.checksum != checksum:
                raise KnowledgeIngestionError(
                    f"knowledge version conflict: ({spec.category}, {spec.title!r}, "
                    f"{spec.version!r}) already exists with different content; "
                    "create a new version instead"
                )
            return existing, False

        document = KnowledgeDocument(
            title=spec.title,
            category=spec.category,
            version=spec.version,
            status=KnowledgeStatus.ACTIVE if spec.activate else KnowledgeStatus.DRAFT,
            source=spec.source,
            effective_date=date.fromisoformat(spec.effective_date),
            language=spec.language,
            checksum=checksum,
        )
        self._session.add(document)
        try:
            self._session.flush()  # assign document.id for chunk FK rows
            snapshot = {
                "title": spec.title,
                "category": spec.category.value,
                "version": spec.version,
                "status": document.status.value,
                "effective_date": document.effective_date.isoformat(),
                "source": spec.source,
                "language": spec.language,
            }
            for index, chunk in enumerate(chunk_document(normalized)):
                self._chunk_repo.create(
                    document_id=document.id,
                    chunk_index=index,
                    section=chunk.section,
                    content=chunk.content,
                    metadata_json={**snapshot, "section": chunk.section},
                )
            self._session.commit()
        except Exception:
            self._session.rollback()
            raise
        return document, True