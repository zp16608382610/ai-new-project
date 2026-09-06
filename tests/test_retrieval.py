"""Retrieval tests (Phase 3B): dense/sparse/hybrid, RRF, filters, lifecycle, traceability.

策略:与 Phase 2/3A 一致,使用 SQLite 内存库 + 确定性 seed(退款 v1 DRAFT / v2 ACTIVE
及其余 ACTIVE 文档)。本地 dense 路径使用 DeterministicEmbeddingProvider(确定性、
词面重叠感知,非语义 embedding)。PostgreSQL / pgvector 未实机验证。
本阶段不写 RAG 生成 / 评测框架测试。
"""
import pytest

from app.db.base import Base
from app.db.enums import KnowledgeCategory, KnowledgeStatus
from app.db.models import KnowledgeChunk
from app.db.repository import KnowledgeChunkRepository, KnowledgeDocumentRepository
from app.db.session import create_db_engine, create_session_factory
from app.knowledge.documents import KnowledgeSpec
from app.knowledge.ingestion import IngestionService
from app.knowledge.seed import seed_knowledge
from app.retrieval.dataset import RETRIEVAL_CASES
from app.retrieval.fusion import reciprocal_rank_fusion
from app.retrieval.service import RetrievalService
from app.retrieval.types import EmptyQueryError, RetrievalFilter


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


@pytest.fixture()
def seeded(db_session):
    seed_knowledge(db_session)
    return db_session


@pytest.fixture()
def service(seeded):
    return RetrievalService(seeded)


def _has_doc(result, title, version=None, category=None) -> bool:
    candidates = result.candidates if hasattr(result, "candidates") else result
    for candidate in candidates:
        if candidate.title != title:
            continue
        if version is not None and candidate.version != version:
            continue
        if category is not None and candidate.category != category:
            continue
        return True
    return False


def _chunk_repo(session) -> KnowledgeChunkRepository:
    return KnowledgeChunkRepository(session)


# --- query validation / preprocessing ---------------------------------------


def test_empty_query_is_rejected(service):
    with pytest.raises(EmptyQueryError):
        service.retrieve("")
    with pytest.raises(EmptyQueryError):
        service.retrieve("   \n\t ")
    with pytest.raises(EmptyQueryError):
        service.retrieve("！ ？")


def test_query_preprocessing_is_deterministic(service):
    a = service.retrieve("  退款\n政策  ")
    b = service.retrieve("退款政策")
    assert a.query == "退款 政策"
    assert b.query == "退款政策"


# --- dense retrieval --------------------------------------------------------


def test_dense_top_k_and_deterministic_path(service):
    retriever = service.dense_retriever
    result = retriever.retrieve("退款金额怎么算", top_k=3)
    assert 0 < len(result) <= 3
    assert [c.rank for c in result] == list(range(1, len(result) + 1))
    again = retriever.retrieve("退款金额怎么算", top_k=3)
    assert [(c.chunk_id, c.score) for c in result] == [
        (c.chunk_id, c.score) for c in again
    ]
    assert _has_doc(result, "退款政策")


def test_dense_known_relevant_query_retrieves_relevant_chunk(service):
    result = service.dense_retriever.retrieve("七天无理由退货怎么申请", top_k=5)
    assert _has_doc(result, "退货政策", category=KnowledgeCategory.RETURN)


# --- sparse / BM25 -----------------------------------------------------------


def test_sparse_exact_keyword_retrieves_relevant_chunk(service):
    result = service.sparse_retriever.retrieve("七天无理由退货条件", top_k=5)
    assert result and result[0].title == "退货政策"
    assert _has_doc(result, "退货政策", category=KnowledgeCategory.RETURN)


def test_sparse_important_terms_affect_ranking(db_session):
    ingestion = IngestionService(db_session)
    ingestion.ingest_spec(
        KnowledgeSpec(
            title="运费规则一",
            category=KnowledgeCategory.RETURN,
            version="1.0.0",
            content="# 运费规则一\n## 跨境运费\n跨境订单运费由平台全额补贴。",
            effective_date="2026-08-01",
        )
    )
    ingestion.ingest_spec(
        KnowledgeSpec(
            title="运费规则二",
            category=KnowledgeCategory.COUPON,
            version="1.0.0",
            content="# 运费规则二\n## 普通运费\n普通订单运费由用户承担。",
            effective_date="2026-08-01",
        )
    )
    retriever = RetrievalService(db_session).sparse_retriever
    result = retriever.retrieve("跨境订单运费谁出", top_k=3)
    assert result and result[0].title == "运费规则一"


def test_sparse_irrelevant_query_returns_no_candidates(service):
    result = service.sparse_retriever.retrieve("股票行情 基金定投 电影推荐", top_k=5)
    assert result == []


# --- hybrid + fusion ---------------------------------------------------------


def test_hybrid_returns_relevant_candidates(service):
    result = service.retrieve("签收后多少天内可以申请退款", top_k=5)
    assert result.candidates
    assert _has_doc(result, "退款政策", version="2.0.0")


def test_rrf_is_deterministic_and_merges_duplicates(service):
    query = "退款政策"
    first = service.retrieve(query, top_k=5)
    second = service.retrieve(query, top_k=5)
    assert [
        (c.chunk_id, c.rank, c.score, c.retrieval_methods) for c in first.candidates
    ] == [(c.chunk_id, c.rank, c.score, c.retrieval_methods) for c in second.candidates]

    dense_list = service.dense_retriever.retrieve(query, top_k=5)
    sparse_list = service.sparse_retriever.retrieve(query, top_k=5)
    fused = reciprocal_rank_fusion([dense_list, sparse_list], top_k=5)
    unique_ids = {c.chunk_id for c in dense_list} | {c.chunk_id for c in sparse_list}
    assert len(fused) <= len(unique_ids)
    seen = [c.chunk_id for c in fused]
    assert len(seen) == len(set(seen))
    dense_ids = {c.chunk_id for c in dense_list}
    overlap = [c for c in fused if c.chunk_id in dense_ids]
    assert any(set(c.retrieval_methods) == {"dense", "bm25"} for c in overlap)


# --- metadata filtering ------------------------------------------------------


def test_category_filter_limits_candidates(service):
    result = service.retrieve(
        "退款政策 退货政策 优惠券",
        top_k=10,
        filters=RetrievalFilter(category=KnowledgeCategory.COUPON),
    )
    assert result.candidates
    assert all(c.category == KnowledgeCategory.COUPON for c in result.candidates)


def test_language_filter_works(service):
    zh = service.retrieve("退款", top_k=5, filters=RetrievalFilter(language="zh-CN"))
    assert zh.candidates
    en = service.retrieve(
        "refund", top_k=5, filters=RetrievalFilter(language="en-US")
    )
    assert en.candidates == ()


# --- lifecycle / versioning --------------------------------------------------


def test_archived_version_is_excluded_by_default(seeded):
    repo = KnowledgeDocumentRepository(seeded)
    v1 = repo.get_by_natural_key(KnowledgeCategory.REFUND, "退款政策", "1.0.0")
    assert v1 is not None
    v1.status = KnowledgeStatus.ARCHIVED
    seeded.commit()

    service = RetrievalService(seeded)
    result = service.retrieve("退款政策 退款资格", top_k=10)
    assert result.candidates
    # normal retrieval must return the ACTIVE version and never silently return v1
    assert any(c.title == "退款政策" and c.version == "2.0.0" for c in result.candidates)
    assert not any(c.title == "退款政策" and c.version == "1.0.0" for c in result.candidates)
    assert all((c.metadata or {}).get("status") == "ACTIVE" for c in result.candidates)

    # explicit internal override (status=None) may include historical versions
    relaxed = service.retrieve(
        "退款政策 退款资格", top_k=10, filters=RetrievalFilter(status=None)
    )
    assert any(
        c.title == "退款政策" and c.version == "1.0.0" for c in relaxed.candidates
    )


def test_active_documents_can_be_retrieved(service):
    result = service.retrieve("优惠券退回规则", top_k=5)
    assert result.candidates
    assert all((c.metadata or {}).get("status") == "ACTIVE" for c in result.candidates)


# --- traceability ------------------------------------------------------------


def test_results_are_traceable_to_source(seeded):
    service = RetrievalService(seeded)
    result = service.retrieve(
        "退款金额 处理时效",
        top_k=10,
        filters=RetrievalFilter(category=KnowledgeCategory.REFUND),
    )
    assert result.candidates
    for candidate in result.candidates:
        meta = candidate.metadata or {}
        # chunk -> document -> version -> source chain is consistent
        assert candidate.document_id > 0
        assert candidate.title == "退款政策"
        assert candidate.version == "2.0.0"
        assert candidate.category == KnowledgeCategory.REFUND
        assert meta.get("source") == "internal/mock/policy"
        assert meta.get("title") == candidate.title
        assert meta.get("version") == candidate.version
        assert meta.get("language") == "zh-CN"
        chunk = seeded.get(KnowledgeChunk, candidate.chunk_id)
        assert chunk is not None and chunk.document_id == candidate.document_id
        assert chunk.content == candidate.content


# --- quality dataset (deterministic dev checks) ------------------------------


@pytest.mark.parametrize(
    "case", RETRIEVAL_CASES, ids=[f"{i}-{c.kind}" for i, c in enumerate(RETRIEVAL_CASES)]
)
def test_retrieval_dataset_case(service, case):
    if case.expected_title is None:
        # irrelevant question: no lexical overlap -> sparse must not invent hits
        sparse = service.retrieve(case.query, top_k=5, methods=("sparse",))
        assert sparse.empty
        return
    result = service.retrieve(case.query, top_k=case.top_k)
    assert result.candidates
    assert _has_doc(
        result,
        case.expected_title,
        case.expected_version,
        case.expected_category,
    )


def test_no_results_is_typed_empty(service):
    result = service.retrieve("股票行情", top_k=5, methods=("sparse",))
    assert result.candidates == ()
    assert result.empty is True
    assert result.latency_ms is not None and result.latency_ms >= 0