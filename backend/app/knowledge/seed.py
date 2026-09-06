"""Deterministic knowledge seed (Phase 3A).

Idempotent and offline: ingests the synthetic policy set through
IngestionService; repeated calls create nothing new.

Usage (dev / smoke / tests):

    from app.db.session import SessionLocal
    from app.knowledge.seed import seed_knowledge

    session = SessionLocal()
    try:
        inserted = seed_knowledge(session)
    finally:
        session.close()
"""
from sqlalchemy.orm import Session

from app.knowledge.documents import knowledge_specs
from app.knowledge.ingestion import IngestionService


def seed_knowledge(session: Session) -> bool:
    """Ingest the deterministic knowledge set. True when anything new was stored."""
    service = IngestionService(session)
    created_any = False
    for spec in knowledge_specs():
        _, created = service.ingest_spec(spec)
        created_any = created_any or created
    return created_any