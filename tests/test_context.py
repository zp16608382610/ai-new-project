"""Context assembly tests (Phase 3C).

Covers ordering, metadata preservation, token budget, truncation, dedupe
(version-safe), source priority, determinism, typed schema, and an end-to-end
retrieval -> rerank -> context integration against the seeded SQLite corpus.
"""
import pytest

from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from app.knowledge.seed import seed_knowledge
from app.retrieval.context import (
    ContextAssembler,
    ContextBudget,
    ContextItem,
    ContextPackage,
    estimate_tokens,
)
from app.retrieval.pipeline import RetrievalPipeline, RetrievalPipelineConfig
from retrieval_factories import make_candidate, make_reranked


def assemble(query, reranked, budget=None):
    return ContextAssembler(budget).assemble(query, reranked)


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


# --- ordering / schema -----------------------------------------------------


def test_context_ordering_and_package_schema():
    entries = [
        make_reranked(
            make_candidate(chunk_id=1, document_id=1, content="第一个片段"), 9.0, rank=1
        ),
        make_reranked(
            make_candidate(chunk_id=2, document_id=1, content="第二个片段"), 5.0, rank=2
        ),
        make_reranked(
            make_candidate(chunk_id=3, document_id=1, content="第三个片段"), 2.0, rank=3
        ),
    ]
    package = assemble("退款", entries)
    assert isinstance(package, ContextPackage)
    assert package.query == "退款"
    assert isinstance(package.estimated_tokens, int)
    assert isinstance(package.token_budget, int)
    assert isinstance(package.total_items, int)
    assert isinstance(package.truncated, bool)
    assert len(package.items) == 3
    assert package.total_items == 3
    assert package.truncated is False
    assert [item.content for item in package.items] == [
        "第一个片段",
        "第二个片段",
        "第三个片段",
    ]
    assert [item.relevance_score for item in package.items] == [9.0, 5.0, 2.0]
    assert all(isinstance(item, ContextItem) for item in package.items)


def test_context_metadata_preservation_and_traceability():
    candidate = make_candidate(
        chunk_id=42,
        document_id=7,
        title="退货政策",
        section="退货资格",
        version="1.0.0",
        content="七天无理由退货条件说明。",
        source="internal/mock/policy",
        status="ACTIVE",
        language="zh-CN",
        retrieval_methods=("dense", "bm25"),
    )
    package = assemble("退货条件", [make_reranked(candidate, 4.5)])
    item = package.items[0]
    assert item.chunk_id == 42
    assert item.document_id == 7
    assert item.source_id == "internal/mock/policy"
    assert item.title == "退货政策"
    assert item.version == "1.0.0"
    assert item.section == "退货资格"
    assert item.status == "ACTIVE"
    assert item.language == "zh-CN"
    assert item.content == "七天无理由退货条件说明。"
    assert item.relevance_score == 4.5
    assert item.retrieval_methods == ("dense", "bm25")
    assert item.citation == "退货政策 / v1.0.0 / 退货资格"


# --- token budget / truncation --------------------------------------------


def test_context_token_budget_is_respected():
    budget = ContextBudget(max_tokens=100, reserve_tokens=0)
    entries = [
        make_reranked(
            make_candidate(chunk_id=i, document_id=i, content="a" * 400),
            float(10 - i),
        )
        for i in range(1, 4)
    ]
    assert estimate_tokens("a" * 400) == 100
    package = assemble("退款", entries, budget=budget)
    assert len(package.items) == 1
    assert package.total_items == 3
    assert package.truncated is True
    assert package.estimated_tokens <= package.token_budget


def test_context_truncation_respects_reserve_tokens():
    budget = ContextBudget(max_tokens=120, reserve_tokens=20)
    entries = [
        make_reranked(
            make_candidate(chunk_id=i, document_id=i, content=ch * 400),
            float(10 - i),
        )
        for i, ch in enumerate(("a", "b", "c"), start=1)
    ]
    package = assemble("退款", entries, budget=budget)
    assert package.token_budget == 100
    assert package.estimated_tokens == 100
    assert len(package.items) == 1
    assert package.total_items == 3
    assert package.truncated is True


def test_context_never_splits_oversized_item():
    budget = ContextBudget(max_tokens=100)
    entry = make_reranked(
        make_candidate(chunk_id=1, document_id=1, content="a" * 500), 5.0
    )
    assert estimate_tokens("a" * 500) == 125
    package = assemble("退款", [entry], budget=budget)
    assert package.empty is True
    assert package.total_items == 1
    assert package.truncated is True
    assert package.estimated_tokens == 0


def test_context_empty_reranked_input():
    package = assemble("退款", [])
    assert package.empty is True
    assert package.total_items == 0
    assert package.truncated is False
    assert package.estimated_tokens == 0


# --- dedupe (version-safe) ------------------------------------------------


def test_context_dedupe_same_chunk_id_keeps_first():
    entries = [
        make_reranked(
            make_candidate(chunk_id=9, document_id=3, content="首次内容"), 6.0
        ),
        make_reranked(
            make_candidate(chunk_id=9, document_id=3, content="重复的 chunk"), 1.0
        ),
    ]
    package = assemble("退款", entries)
    assert len(package.items) == 1
    assert package.items[0].content == "首次内容"
    assert package.items[0].relevance_score == 6.0


def test_context_dedupe_same_document_section_content_only():
    entries = [
        make_reranked(
            make_candidate(
                chunk_id=1, document_id=5, section="规则", content="完全一致甲"
            ),
            3.0,
        ),
        make_reranked(
            make_candidate(
                chunk_id=2, document_id=5, section="规则", content="完全一致甲"
            ),
            2.0,
        ),
        make_reranked(
            make_candidate(
                chunk_id=3, document_id=5, section="规则", content="不同内容乙"
            ),
            1.0,
        ),
    ]
    package = assemble("退款", entries)
    assert [item.content for item in package.items] == ["完全一致甲", "不同内容乙"]
    assert package.total_items == 2


def test_context_active_version_preference():
    draft = make_candidate(
        chunk_id=11,
        document_id=20,
        title="退款政策",
        version="1.0.0",
        section="退款资格",
        content="旧版资格条款。",
        status="DRAFT",
    )
    active = make_candidate(
        chunk_id=12,
        document_id=21,
        title="退款政策",
        version="2.0.0",
        section="退款资格",
        content="新版资格条款。",
        status="ACTIVE",
    )
    package = assemble(
        "退款资格", [make_reranked(draft, 9.0), make_reranked(active, 8.0)]
    )
    assert len(package.items) == 1
    assert package.items[0].version == "2.0.0"
    assert package.items[0].status == "ACTIVE"
    assert package.items[0].content == "新版资格条款。"


def test_context_different_active_versions_are_not_merged():
    identical = "两个版本共用的相同条款文本。"
    v2 = make_candidate(
        chunk_id=21,
        document_id=30,
        title="退款政策",
        version="2.0.0",
        section="退款资格",
        content=identical,
        status="ACTIVE",
    )
    v3 = make_candidate(
        chunk_id=22,
        document_id=31,
        title="退款政策",
        version="3.0.0",
        section="退款资格",
        content=identical,
        status="ACTIVE",
    )
    package = assemble(
        "退款资格", [make_reranked(v2, 9.0), make_reranked(v3, 7.0)]
    )
    assert [item.version for item in package.items] == ["2.0.0", "3.0.0"]
    assert [item.content for item in package.items] == [identical, identical]


def test_context_draft_unique_section_fallback_kept():
    active = make_candidate(
        chunk_id=31,
        document_id=40,
        title="退款政策",
        version="2.0.0",
        section="退款金额",
        content="现行退款金额规则。",
        status="ACTIVE",
    )
    draft = make_candidate(
        chunk_id=32,
        document_id=41,
        title="退款政策",
        version="1.0.0",
        section="历史特殊说明",
        content="旧版特有说明,未被现行版覆盖。",
        status="DRAFT",
    )
    package = assemble(
        "退款", [make_reranked(active, 8.0), make_reranked(draft, 5.0)]
    )
    assert package.total_items == 2
    assert {item.version for item in package.items} == {"1.0.0", "2.0.0"}
    assert "旧版特有说明,未被现行版覆盖。" in {
        item.content for item in package.items
    }


def test_context_deterministic_output():
    entries = [
        make_reranked(make_candidate(chunk_id=3, content="第三条内容"), 4.0),
        make_reranked(make_candidate(chunk_id=1, content="第一条内容"), 9.0),
        make_reranked(make_candidate(chunk_id=2, content="第二条内容"), 6.0),
    ]
    first = assemble("退款", entries)
    second = assemble("退款", entries)
    assert first == second


# --- budget / config validation -------------------------------------------


def test_context_budget_validation():
    with pytest.raises(ValueError):
        ContextBudget(max_tokens=0)
    with pytest.raises(ValueError):
        ContextBudget(max_tokens=100, reserve_tokens=100)
    with pytest.raises(ValueError):
        ContextBudget(max_tokens=100, reserve_tokens=-1)
    assert estimate_tokens("") == 0
    assert estimate_tokens("退款") >= 1


def test_pipeline_config_defaults_and_validation():
    config = RetrievalPipelineConfig()
    assert config.retrieval_top_k == 20
    assert config.rerank_top_k == 5
    assert config.max_context_tokens == 2000
    assert config.reserve_tokens == 0
    with pytest.raises(ValueError):
        RetrievalPipelineConfig(retrieval_top_k=20, rerank_top_k=21)
    with pytest.raises(ValueError):
        RetrievalPipelineConfig(retrieval_top_k=0)
    with pytest.raises(ValueError):
        RetrievalPipelineConfig(rerank_top_k=0)
    with pytest.raises(ValueError):
        RetrievalPipelineConfig(max_context_tokens=0)
    with pytest.raises(ValueError):
        RetrievalPipelineConfig(max_context_tokens=100, reserve_tokens=100)


# --- integration -----------------------------------------------------------


def test_integration_retrieval_rerank_context_end_to_end(seeded):
    pipeline = RetrievalPipeline(seeded)
    package = pipeline.run("退款金额怎么算")
    assert package.empty is False
    assert len(package.items) <= pipeline.config.rerank_top_k
    assert package.total_items <= pipeline.config.retrieval_top_k
    assert package.estimated_tokens <= package.token_budget

    top = package.items[0]
    assert top.title == "退款政策"
    assert top.version == "2.0.0"
    assert top.status == "ACTIVE"
    assert top.section == "退款金额"
    assert top.source_id == "internal/mock/policy"
    assert top.document_id > 0
    assert top.chunk_id > 0
    assert set(top.retrieval_methods) == {"bm25", "dense"}

    scores = [item.relevance_score for item in package.items]
    assert scores == sorted(scores, reverse=True)
    assert all(item.status == "ACTIVE" for item in package.items)


def test_integration_zero_result_retrieval_is_typed_empty(seeded):
    pipeline = RetrievalPipeline(seeded)
    package = pipeline.run("股票行情怎么买", methods=("sparse",))
    assert package.empty is True
    assert package.items == ()
    assert package.total_items == 0
    assert package.truncated is False
    assert package.estimated_tokens == 0