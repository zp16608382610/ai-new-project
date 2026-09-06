"""Reranking layer (Phase 3C) - replaceable reranker architecture.

Boundary:
    Retrieval finds candidates, the Reranker owns relevance ordering, and
    Context Assembly decides what finally reaches the model. None of these
    layers may live inside FastAPI routes, and none may depend on the future
    Agent layer.

Swap point:
    DeterministicReranker is a deterministic, local TEST implementation. It is
    NOT a semantic reranker. A real Cross-Encoder, an LLM-based reranker, or a
    provider-specific reranking API can later replace it behind the same
    Reranker interface without touching retrieval or context assembly.

Scoring model (explicit weights, see RerankWeights):

    rerank_score =
        lexical_overlap        (query vs chunk content tokens,   [0, 1])
      + title_overlap          (query vs document title tokens,  [0, 1])
      + section_overlap        (query vs chunk section tokens,   [0, 1])
      + exact_term_overlap     (verbatim query term in content,  [0, 1])
      + retrieval_component    (positional component,            [0, 1])
      + method_diversity       (found by dense AND bm25 -> 1.0)
      - duplicate_penalty      (identical content already ranked higher)

Duplicate handling is scoped per (document_id, content key): two versions of
the same policy that happen to share identical section wording are never
treated as duplicates of each other, so versions stay separately traceable.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from app.retrieval.text import preprocess_query, tokenize
from app.retrieval.types import RetrievalCandidate


class Reranker(Protocol):
    """Interface implemented by the deterministic local and future real rerankers."""

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalCandidate],
        *,
        top_k: int | None = None,
    ) -> list["RerankedCandidate"]:
        """Return up to top_k candidates ordered by descending relevance."""
        ...


@dataclass(frozen=True)
class RerankWeights:
    """Explicit weights of the deterministic scoring model."""

    lexical: float = 1.5
    title: float = 1.2
    section: float = 0.8
    exact_term: float = 1.0
    retrieval: float = 0.5
    diversity: float = 0.4
    duplicate: float = 1.0

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            value = getattr(self, field_name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"RerankWeights.{field_name} must be a number")
            if value < 0:
                raise ValueError(f"RerankWeights.{field_name} must be >= 0")


@dataclass(frozen=True)
class RerankSignals:
    """Per-candidate signal breakdown (audit + future model swap)."""

    lexical_overlap: float
    title_overlap: float
    section_overlap: float
    exact_term_overlap: float
    retrieval_component: float
    method_diversity: float
    duplicate_penalty: float
    total: float


@dataclass(frozen=True)
class RerankedCandidate:
    """Reranker output: original candidate + rerank score / rank / signals."""

    candidate: RetrievalCandidate
    rerank_score: float
    rank: int
    signals: RerankSignals


def _surface(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def _component_fraction(query_tokens: list[str], text_tokens: list[str]) -> float:
    """Shared query tokens / all query tokens (bounded to [0, 1])."""
    if not query_tokens:
        return 0.0
    return len(set(query_tokens) & set(text_tokens)) / len(query_tokens)


def _exact_term_fraction(query_tokens: list[str], content_surface: str) -> float:
    """Verbatim query terms (len >= 2) present inside the chunk content."""
    terms = sorted({term for term in query_tokens if len(term) >= 2})
    if not terms:
        return 0.0
    matched = sum(1 for term in terms if term in content_surface)
    return matched / len(terms)


def _retrieval_component(rank: int, total: int) -> float:
    """Normalize the original retrieval rank to [0, 1] (best rank -> 1.0)."""
    if total <= 0:
        return 0.0
    position = rank if rank and rank > 0 else 1
    value = 1.0 - (position - 1) / total
    if value < 0.0:
        return 0.0
    return min(value, 1.0)


class DeterministicReranker:
    """Deterministic lexical reranker (architecture + test implementation).

    Not a semantic reranker: every signal derives from surface-form overlap
    between query and candidate. Given the same input it always returns the
    same ordering, which keeps tests and later evaluations stable.
    """

    name = "deterministic_lexical"

    def __init__(self, weights: RerankWeights | None = None) -> None:
        self._weights = weights or RerankWeights()

    @property
    def weights(self) -> RerankWeights:
        return self._weights

    def rerank(
        self,
        query: str,
        candidates: Sequence[RetrievalCandidate],
        *,
        top_k: int | None = None,
    ) -> list[RerankedCandidate]:
        """Score candidates; return the top_k by descending rerank score."""
        if top_k is not None and (
            isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1
        ):
            raise ValueError("top_k must be a positive integer")
        normalized_query = preprocess_query(query)  # raises EmptyQueryError
        if not candidates:
            return []
        query_tokens = tokenize(normalized_query)
        weights = self._weights
        total = len(candidates)

        computed: list[tuple[float, RerankSignals, RetrievalCandidate]] = []
        for index, candidate in enumerate(candidates, start=1):
            content_surface = _surface(candidate.content)
            signals = RerankSignals(
                lexical_overlap=_component_fraction(
                    query_tokens, tokenize(content_surface)
                ),
                title_overlap=_component_fraction(
                    query_tokens, tokenize(_surface(candidate.title))
                ),
                section_overlap=_component_fraction(
                    query_tokens, tokenize(_surface(candidate.section or ""))
                ),
                exact_term_overlap=_exact_term_fraction(query_tokens, content_surface),
                retrieval_component=_retrieval_component(
                    candidate.rank if candidate.rank and candidate.rank > 0 else index,
                    total,
                ),
                method_diversity=1.0 if len(candidate.retrieval_methods) > 1 else 0.0,
                duplicate_penalty=0.0,
                total=0.0,
            )
            primary = (
                weights.lexical * signals.lexical_overlap
                + weights.title * signals.title_overlap
                + weights.section * signals.section_overlap
                + weights.exact_term * signals.exact_term_overlap
                + weights.retrieval * signals.retrieval_component
                + weights.diversity * signals.method_diversity
            )
            computed.append((round(primary, 6), signals, candidate))

        # primary order decides which occurrence counts as the original
        computed.sort(key=lambda entry: (-entry[0], entry[2].chunk_id))

        seen: set[tuple[int, str]] = set()
        finalized: list[RerankedCandidate] = []
        for primary, base, candidate in computed:
            content_key = " ".join(tokenize(_surface(candidate.content)))
            duplicate_key = (candidate.document_id, content_key)
            is_duplicate = duplicate_key in seen
            seen.add(duplicate_key)
            penalty = 1.0 if is_duplicate else 0.0
            final_score = max(0.0, round(primary - weights.duplicate * penalty, 6))
            signals = replace(base, duplicate_penalty=penalty, total=final_score)
            finalized.append(
                RerankedCandidate(
                    candidate=candidate,
                    rerank_score=final_score,
                    rank=0,
                    signals=signals,
                )
            )

        finalized.sort(key=lambda entry: (-entry.rerank_score, entry.candidate.chunk_id))
        for rank, entry in enumerate(finalized, start=1):
            finalized[rank - 1] = replace(entry, rank=rank)
        if top_k is not None:
            finalized = finalized[:top_k]
        return finalized