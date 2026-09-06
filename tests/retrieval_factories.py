"""Deterministic candidate factories for Phase 3C unit tests.

Keeps reranking / context tests readable: hand-built RetrievalCandidate
objects (mirroring the repository snapshot metadata) without a database.
"""
from app.db.enums import KnowledgeCategory
from app.retrieval.rerank import RerankSignals, RerankedCandidate
from app.retrieval.types import RetrievalCandidate


def make_candidate(
    *,
    chunk_id: int,
    document_id: int = 1,
    title: str = "退款政策",
    category: KnowledgeCategory = KnowledgeCategory.REFUND,
    version: str = "2.0.0",
    section: str | None = "退款金额",
    content: str = "退款金额由订单权威数据推导。",
    score: float = 0.02,
    rank: int = 1,
    retrieval_methods: tuple[str, ...] = ("dense", "bm25"),
    status: str = "ACTIVE",
    source: str = "internal/mock/policy",
    language: str = "zh-CN",
) -> RetrievalCandidate:
    """Build one candidate with the same metadata shape the repository stores."""
    return RetrievalCandidate(
        chunk_id=chunk_id,
        document_id=document_id,
        title=title,
        category=category,
        version=version,
        section=section,
        content=content,
        score=score,
        rank=rank,
        retrieval_methods=tuple(retrieval_methods),
        metadata={
            "source": source,
            "status": status,
            "language": language,
            "title": title,
            "version": version,
            "category": category.value,
        },
    )


def make_reranked(
    candidate: RetrievalCandidate,
    rerank_score: float = 0.0,
    *,
    rank: int = 1,
    signals: RerankSignals | None = None,
) -> RerankedCandidate:
    """Wrap a candidate as reranker output (zero signals when not relevant)."""
    resolved = signals or RerankSignals(
        lexical_overlap=0.0,
        title_overlap=0.0,
        section_overlap=0.0,
        exact_term_overlap=0.0,
        retrieval_component=0.0,
        method_diversity=0.0,
        duplicate_penalty=0.0,
        total=rerank_score,
    )
    return RerankedCandidate(
        candidate=candidate,
        rerank_score=rerank_score,
        rank=rank,
        signals=resolved,
    )