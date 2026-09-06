"""Knowledge base tests (Phase 3A): documents, versioning, lifecycle, chunks, ingestion, seed.

策略:与 Phase 2 数据层测试一致,使用 SQLite 内存库(Base.metadata.create_all)
验证数据模型与 ingestion 行为;PostgreSQL / pgvector 验证留待具备 Docker 的环境。
本阶段不写 retrieval / RAG 测试。
"""
import pytest
from sqlalchemy import func, select

from app.db.base import Base
from app.db.enums import KnowledgeCategory, KnowledgeStatus
from app.db.models import KnowledgeChunk, KnowledgeDocument
from app.db.repository import KnowledgeDocumentRepository
from app.db.session import create_db_engine, create_session_factory
from app.knowledge.chunker import chunk_document, normalize_text
from app.knowledge.documents import KnowledgeSpec, knowledge_specs
from app.knowledge.embedding import DeterministicEmbeddingProvider
from app.knowledge.ingestion import IngestionService, KnowledgeIngestionError, checksum_of
from app.knowledge.seed import seed_knowledge


@pytest.fixture()
def db_session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _policy_content_v1() -> str:
    return """# 退款政策
## 适用范围
本政策适用于已签收订单的退款申请。

## 退款资格
订单必须已签收,且订单内全部商品均可退。

## 退款时效
自商品签收之日起十五个自然日内可以申请无理由退款。
"""


def _policy_content_v2() -> str:
    return """# 退款政策
## 适用范围
本政策适用于已签收订单的退款申请。

## 退款资格
订单必须已签收,且订单内全部商品均可退。

## 退款时效
自商品签收之日起十四个自然日内可以申请无理由退款。

## 不可退款情形
定制商品与已使用的贴身商品不可退款。
"""


def _refund_spec(
    version: str = "1.0.0", activate: bool = True, content: str | None = None
) -> KnowledgeSpec:
    return KnowledgeSpec(
        title="退款政策",
        category=KnowledgeCategory.REFUND,
        version=version,
        content=content
        if content is not None
        else (_policy_content_v1() if version == "1.0.0" else _policy_content_v2()),
        effective_date="2026-08-01" if version == "1.0.0" else "2026-09-01",
        activate=activate,
    )


# --- deterministic text handling -------------------------------------------


def test_normalization_is_deterministic_and_normalizes_newlines():
    raw = "\r\n\r\nA  \r\nB\r\n\r\n\r\nC  \n\nD \r\n"
    first = normalize_text(raw)
    assert normalize_text(raw) == first
    assert "\r" not in first
    assert first == "A\nB\n\nC\n\nD"
    assert normalize_text(first) == first


def test_chunking_is_section_aware_and_deterministic():
    text = _policy_content_v1()
    chunks_a = chunk_document(text)
    chunks_b = chunk_document(text)
    assert [(c.section, c.content) for c in chunks_a] == [
        (c.section, c.content) for c in chunks_b
    ]
    sections = [c.section for c in chunks_a]
    for expected in ("适用范围", "退款资格", "退款时效"):
        assert expected in sections
    body = chunks_a[sections.index("适用范围")]
    assert body.section == "适用范围"
    assert body.content.startswith("本政策适用于已签收订单")
    assert "十五个自然日" in "".join(c.content for c in chunks_a)


def test_chunking_splits_long_sections_deterministically():
    long_body = "## 长章节\n" + "规则描述。" * 300  # far beyond max_chars
    chunks = chunk_document(long_body, max_chars=300)
    first = [(c.section, c.content) for c in chunks]
    again = [(c.section, c.content) for c in chunk_document(long_body, max_chars=300)]
    assert first == again
    assert len(chunks) > 1
    assert all(len(c.content) <= 300 for c in chunks)
    assert all(c.section == "长章节" for c in chunks)
    assert "".join(c.content for c in chunks) == "规则描述。" * 300


# --- ingestion: documents + chunks ------------------------------------------


def test_ingest_spec_creates_document_and_chunks(db_session):
    service = IngestionService(db_session)
    spec = _refund_spec(version="1.0.0", activate=True)
    document, created = service.ingest_spec(spec)
    assert created is True
    assert document.status == KnowledgeStatus.ACTIVE
    assert document.checksum == checksum_of(normalize_text(spec.content))
    assert document.category == KnowledgeCategory.REFUND
    assert document.version == "1.0.0"
    assert document.source == spec.source

    chunks = document.chunks
    assert len(chunks) > 0
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    expected = chunk_document(normalize_text(spec.content))
    assert [c.content for c in chunks] == [e.content for e in expected]

    # persistence + metadata survive a fresh read from the same database
    fresh = db_session.scalar(
        select(KnowledgeDocument).where(KnowledgeDocument.id == document.id)
    )
    assert fresh is not None and len(fresh.chunks) == len(chunks)
    meta = fresh.chunks[0].metadata_json or {}
    assert meta["title"] == "退款政策"
    assert meta["category"] == "REFUND"
    assert meta["version"] == "1.0.0"
    assert meta["status"] == "ACTIVE"
    assert meta["source"] == "internal/mock/policy"
    assert meta["language"] == "zh-CN"
    assert meta["effective_date"] == "2026-08-01"


def test_reingest_identical_content_is_idempotent(db_session):
    service = IngestionService(db_session)
    spec = _refund_spec(version="1.0.0", activate=True)
    document, first_created = service.ingest_spec(spec)
    chunk_count = db_session.scalar(select(func.count()).select_from(KnowledgeChunk))
    document_again, second_created = service.ingest_spec(spec)
    assert first_created is True and second_created is False
    assert document_again.id == document.id
    assert db_session.scalar(select(func.count()).select_from(KnowledgeDocument)) == 1
    assert (
        db_session.scalar(select(func.count()).select_from(KnowledgeChunk)) == chunk_count
    )


def test_same_version_different_content_is_rejected(db_session):
    service = IngestionService(db_session)
    service.ingest_spec(_refund_spec(version="1.0.0", content=_policy_content_v1()))
    with pytest.raises(KnowledgeIngestionError):
        service.ingest_spec(_refund_spec(version="1.0.0", content=_policy_content_v2()))
    db_session.rollback()
    docs = db_session.scalars(select(KnowledgeDocument)).all()
    assert len(docs) == 1
    joined = "".join(
        c.content
        for c in db_session.scalars(select(KnowledgeChunk)).all()
    )
    assert joined and "十五个自然日" in joined


# --- versioning + lifecycle ---------------------------------------------------


def test_versioning_preserves_history_and_tracks_active(db_session):
    service = IngestionService(db_session)
    repo = KnowledgeDocumentRepository(db_session)
    v1, v1_created = service.ingest_spec(
        _refund_spec(version="1.0.0", activate=False)
    )
    v2, v2_created = service.ingest_spec(_refund_spec(version="2.0.0", activate=True))
    assert v1_created and v2_created

    assert v1.status == KnowledgeStatus.DRAFT
    assert v2.status == KnowledgeStatus.ACTIVE
    assert len(db_session.scalars(select(KnowledgeDocument)).all()) == 2

    active = repo.list_active()
    assert [d.version for d in active] == ["2.0.0"]
    assert all(d.status == KnowledgeStatus.ACTIVE for d in active)

    # lifecycle: DRAFT/ACTIVE documents can move to ARCHIVED without deleting history
    v1.status = KnowledgeStatus.ARCHIVED
    v2.status = KnowledgeStatus.ARCHIVED
    db_session.commit()
    assert repo.list_active() == []
    versions = [d.version for d in repo.list_by_category(KnowledgeCategory.REFUND)]
    assert versions == ["1.0.0", "2.0.0"]
    statuses = {d.status for d in repo.list_by_category(KnowledgeCategory.REFUND)}
    assert statuses == {KnowledgeStatus.ARCHIVED}
    archived_v1 = repo.get_by_natural_key(
        KnowledgeCategory.REFUND, "退款政策", "1.0.0"
    )
    assert archived_v1 is not None
    assert len(archived_v1.chunks) == len(v1.chunks)


def test_archived_vs_active_documents(db_session):
    service = IngestionService(db_session)
    repo = KnowledgeDocumentRepository(db_session)
    service.ingest_spec(_refund_spec(version="1.0.0", activate=True))
    service.ingest_spec(_refund_spec(version="2.0.0", activate=True))
    service.ingest_spec(
        KnowledgeSpec(
            title="退货政策",
            category=KnowledgeCategory.RETURN,
            version="1.0.0",
            content="# 退货政策\n## 退货条件\n全新未使用商品可申请退货。",
            effective_date="2026-08-15",
        )
    )
    refunds = repo.list_by_category(KnowledgeCategory.REFUND)
    archive_target = refunds[0]
    archive_target.status = KnowledgeStatus.ARCHIVED
    db_session.commit()

    active_versions = {d.version for d in repo.list_active(KnowledgeCategory.REFUND)}
    assert active_versions == {"2.0.0"}
    kept = {d.version for d in repo.list_by_category(KnowledgeCategory.REFUND)}
    assert kept == {"1.0.0", "2.0.0"}
    # ARCHIVED != ACTIVE: archiving removes a doc from retrieval candidates only
    assert all(d.status != KnowledgeStatus.ARCHIVED for d in repo.list_active())


# --- source traceability -----------------------------------------------------


def test_every_chunk_is_traceable_to_source_document(db_session):
    service = IngestionService(db_session)
    spec = _refund_spec(version="2.0.0", activate=True)
    document, created = service.ingest_spec(spec)
    assert created is True
    db_session.expire_all()

    rows = db_session.scalars(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == document.id)
        .order_by(KnowledgeChunk.chunk_index)
    ).all()
    assert len(rows) > 0
    for chunk in rows:
        meta = chunk.metadata_json or {}
        # Document -> Version -> Chunk -> Source chain is fully recoverable
        assert chunk.document.id == document.id
        assert chunk.document.title == meta["title"] == "退款政策"
        assert chunk.document.version == meta["version"] == "2.0.0"
        assert chunk.document.category.value == meta["category"] == "REFUND"
        assert meta["source"] == "internal/mock/policy"
        assert meta["language"] == "zh-CN"
        assert meta["effective_date"] == "2026-09-01"
        assert meta["section"] == chunk.section


# --- seed knowledge -----------------------------------------------------------


def test_seed_knowledge_is_deterministic_and_idempotent(db_session):
    assert seed_knowledge(db_session) is True
    doc_count = db_session.scalar(select(func.count()).select_from(KnowledgeDocument))
    chunk_count = db_session.scalar(select(func.count()).select_from(KnowledgeChunk))
    assert doc_count == len(knowledge_specs()) == 7

    assert seed_knowledge(db_session) is False
    assert (
        db_session.scalar(select(func.count()).select_from(KnowledgeDocument))
        == doc_count
    )
    assert (
        db_session.scalar(select(func.count()).select_from(KnowledgeChunk))
        == chunk_count
    )


def test_seed_preserves_refund_versions_and_keeps_one_active(db_session):
    seed_knowledge(db_session)
    repo = KnowledgeDocumentRepository(db_session)
    refund_docs = repo.list_by_category(KnowledgeCategory.REFUND)
    assert [d.version for d in refund_docs] == ["1.0.0", "2.0.0"]
    assert refund_docs[0].status == KnowledgeStatus.DRAFT
    assert refund_docs[1].status == KnowledgeStatus.ACTIVE

    active = repo.list_active()
    active_refunds = [d for d in active if d.category == KnowledgeCategory.REFUND]
    assert [d.version for d in active_refunds] == ["2.0.0"]
    # one ACTIVE document per remaining category (refund keeps only v2 active)
    assert len({d.category for d in active}) == 6


# --- embedding abstraction (no external calls) --------------------------------


def test_embedding_provider_is_deterministic_and_dimension_stable():
    provider = DeterministicEmbeddingProvider(dimensions=8)
    vectors = provider.embed(["same text", "same text"])
    assert vectors[0] == vectors[1]
    assert all(len(v) == 8 for v in vectors)
    other = provider.embed(["different text"])[0]
    assert vectors[0] != other