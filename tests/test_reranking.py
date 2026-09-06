"""Reranker tests (Phase 3C).

DeterministicReranker is a lexical, deterministic test implementation - the
tests assert ordering, signal breakdown, validation, and determinism, not
semantic quality. Real semantic rerankers swap in behind the same interface.
"""
import pytest

from app.retrieval.rerank import DeterministicReranker, RerankWeights, RerankedCandidate
from app.retrieval.types import EmptyQueryError
from retrieval_factories import make_candidate


def rerank(query, candidates, top_k=None):
    return DeterministicReranker().rerank(query, candidates, top_k=top_k)


def chunk_ids(entries):
    return [entry.candidate.chunk_id for entry in entries]


# --- basic ordering / top-k -----------------------------------------------


def test_rerank_basic_relevance_ordering():
    strong = make_candidate(
        chunk_id=1, title="政策说明", section=None,
        content="退款金额以订单系统权威数据为准",
        retrieval_methods=("dense",), rank=1,
    )
    weak = make_candidate(
        chunk_id=2, title="政策说明", section=None,
        content="商品均支持七天无理由退货",
        retrieval_methods=("dense",), rank=2,
    )
    result = rerank("退款金额怎么算", [weak, strong])
    assert isinstance(result[0], RerankedCandidate)
    assert result[0].candidate.chunk_id == 1
    assert result[0].rerank_score > result[1].rerank_score
    assert result[0].signals.lexical_overlap == 1.0
    assert result[1].signals.lexical_overlap == 0.0
    assert result[0].candidate.retrieval_methods == ("dense",)


def test_rerank_fewer_candidates_than_requested():
    candidates = [
        make_candidate(chunk_id=1, content="退款资格判定"),
        make_candidate(chunk_id=2, content="换货政策说明"),
    ]
    result = rerank("退款资格判定", candidates, top_k=5)
    assert len(result) == 2
    assert [entry.rank for entry in result] == [1, 2]


def test_rerank_top_k_caps_results():
    candidates = [make_candidate(chunk_id=i) for i in range(1, 7)]
    limited = rerank("退款", candidates, top_k=3)
    assert len(limited) == 3
    assert [entry.rank for entry in limited] == [1, 2, 3]
    all_results = rerank("退款", candidates, top_k=None)
    assert len(all_results) == 6


# --- scoring signals -------------------------------------------------------


def test_rerank_title_match_bonus():
    content = "相关规则以系统实时状态为准。"
    matching = make_candidate(
        chunk_id=1, title="退款资格判定", section=None, content=content,
        retrieval_methods=("dense",), rank=1,
    )
    unrelated = make_candidate(
        chunk_id=2, title="优惠券核销说明", section=None, content=content,
        retrieval_methods=("dense",), rank=1,
    )
    result = rerank("退款资格判定", [unrelated, matching])
    assert result[0].candidate.chunk_id == 1
    assert result[0].signals.title_overlap > result[1].signals.title_overlap
    assert result[0].signals.title_overlap == 1.0


def test_rerank_exact_business_term_affects_ordering():
    verbatim = make_candidate(
        chunk_id=1, title="示例", section=None,
        content="客服完成人工交接流程后登记工单",
        retrieval_methods=("dense",), rank=1,
    )
    separated = make_candidate(
        chunk_id=2, title="示例", section=None,
        content="人工客服负责交接登记,流程另见说明",
        retrieval_methods=("dense",), rank=1,
    )
    result = rerank("人工交接流程", [separated, verbatim])
    assert result[0].candidate.chunk_id == 1
    assert result[0].signals.exact_term_overlap == 1.0
    assert result[0].signals.exact_term_overlap > result[1].signals.exact_term_overlap


def test_rerank_section_bonus():
    content = "以下为政策说明性文字,以系统为准。"
    section_matched = make_candidate(
        chunk_id=1, title="示例政策", section="退款金额", content=content,
        retrieval_methods=("dense",), rank=1,
    )
    section_unrelated = make_candidate(
        chunk_id=2, title="示例政策", section="配送时效", content=content,
        retrieval_methods=("dense",), rank=1,
    )
    result = rerank("退款金额计算", [section_unrelated, section_matched])
    assert result[0].candidate.chunk_id == 1
    assert result[0].signals.section_overlap > 0.0
    assert result[1].signals.section_overlap == 0.0


def test_rerank_retrieval_rank_contributes():
    content = "退款金额以订单系统权威数据为准。"
    best_rank = make_candidate(
        chunk_id=1, document_id=5, title="示例", section=None, content=content,
        retrieval_methods=("dense",), rank=1,
    )
    later_rank = make_candidate(
        chunk_id=2, document_id=6, title="示例", section=None, content=content,
        retrieval_methods=("dense",), rank=2,
    )
    result = rerank("退款", [best_rank, later_rank])
    assert result[0].candidate.chunk_id == 1
    assert result[0].signals.retrieval_component == 1.0
    assert result[1].signals.retrieval_component == 0.5
    assert result[0].rerank_score > result[1].rerank_score


def test_rerank_method_diversity_bonus():
    single = make_candidate(
        chunk_id=1, document_id=5, title="示例", section=None,
        content="退款处理时效政策", retrieval_methods=("dense",), rank=1,
    )
    hybrid = make_candidate(
        chunk_id=2, document_id=6, title="示例", section=None,
        content="退款流程时效说明", retrieval_methods=("dense", "bm25"), rank=2,
    )
    result = rerank("退款时效", [single, hybrid])
    assert result[0].candidate.chunk_id == 2
    assert result[0].signals.method_diversity == 1.0
    assert result[1].signals.method_diversity == 0.0


# --- duplicates ------------------------------------------------------------


def test_rerank_duplicate_penalty_demotes_second_occurrence():
    content = "退款金额由系统计算。"
    original = make_candidate(
        chunk_id=1, document_id=5, title="示例", section=None, content=content,
        retrieval_methods=("dense",), rank=1,
    )
    duplicate = make_candidate(
        chunk_id=2, document_id=5, title="示例", section=None, content=content,
        retrieval_methods=("dense",), rank=1,
    )
    result = rerank("退款", [duplicate, original])
    assert result[0].candidate.chunk_id == 1
    assert result[0].signals.duplicate_penalty == 0.0
    assert result[1].signals.duplicate_penalty == 1.0
    assert result[0].rerank_score - result[1].rerank_score == pytest.approx(1.0)
    assert [entry.rank for entry in result] == [1, 2]


# --- validation / empty inputs / determinism ------------------------------


def test_rerank_empty_candidates():
    assert rerank("退款", []) == []


@pytest.mark.parametrize("bad_query", ["", "   \n\t ", "！ ？"])
def test_rerank_empty_query_is_rejected(bad_query):
    candidate = make_candidate(chunk_id=1)
    with pytest.raises(EmptyQueryError):
        rerank(bad_query, [candidate])


@pytest.mark.parametrize("bad_top_k", [0, -1, True, 1.5])
def test_rerank_top_k_validation(bad_top_k):
    candidate = make_candidate(chunk_id=1)
    with pytest.raises(ValueError):
        rerank("退款", [candidate], top_k=bad_top_k)


def test_rerank_deterministic_output():
    candidates = [
        make_candidate(chunk_id=3, content="退款金额以系统为准"),
        make_candidate(chunk_id=1, content="人工交接流程说明"),
        make_candidate(chunk_id=2, content="七天无理由退货政策"),
    ]
    first = rerank("退款金额", candidates, top_k=3)
    second = rerank("退款金额", candidates, top_k=3)
    assert [(e.candidate.chunk_id, e.rerank_score, e.rank) for e in first] == [
        (e.candidate.chunk_id, e.rerank_score, e.rank) for e in second
    ]


@pytest.mark.parametrize(
    "bad_kwargs",
    [{"lexical": -0.1}, {"duplicate": "x"}, {"title": True}],
)
def test_rerank_weights_validation(bad_kwargs):
    with pytest.raises(ValueError):
        RerankWeights(**bad_kwargs)