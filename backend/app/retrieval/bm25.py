"""BM25 sparse retrieval - pure standard-library implementation (Phase 3B).

Why no dependency: BM25 is ~60 lines over a term-document index; adding a
library (especially LangChain) only for BM25 would violate the dependency
discipline. Tokenization is deterministic character-bigram based (see text.py)
and works for the fixed Chinese development corpus.

Design notes:
- Each retrieve() call re-indexes the current ACTIVE candidate set from the
  repository, so ingestion changes are always reflected and the layer stays
  deterministic (no external service, no mutable shared index).
- Candidates with score == 0 (no shared query terms) are dropped.
"""
from __future__ import annotations

import math
from collections import Counter

from app.db.repository import KnowledgeChunkRepository
from app.retrieval.rows import seed_from_chunk
from app.retrieval.text import tokenize
from app.retrieval.types import RetrievalCandidate, RetrievalFilter


class BM25Index:
    """Okapi BM25 (k1=1.5, b=0.75) over pre-tokenized documents."""

    def __init__(
        self,
        tokenized_docs: list[list[str]],
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self.documents = tokenized_docs
        self.k1 = k1
        self.b = b
        self.n_docs = len(tokenized_docs)
        self.doc_lengths = [len(tokens) for tokens in tokenized_docs]
        self.average_length = (
            sum(self.doc_lengths) / self.n_docs if self.n_docs else 0.0
        )
        document_frequency: Counter[str] = Counter()
        for tokens in tokenized_docs:
            document_frequency.update(set(tokens))
        self.document_frequency = document_frequency

    def _idf(self, term: str) -> float:
        df = self.document_frequency.get(term, 0)
        # log1p variant keeps idf positive and smooths df == n_docs.
        return math.log1p((self.n_docs - df + 0.5) / (df + 0.5))

    def score(self, query_tokens: list[str], doc_index: int) -> float:
        if not query_tokens or not self.documents:
            return 0.0
        terms = Counter(query_tokens)
        doc_tokens = self.documents[doc_index]
        term_frequencies = Counter(doc_tokens)
        doc_length = self.doc_lengths[doc_index]
        denominator = (
            1 - self.b + self.b * (doc_length / self.average_length)
            if self.average_length
            else 1.0
        )
        total = 0.0
        for term, query_count in terms.items():
            tf = term_frequencies.get(term, 0)
            if tf == 0:
                continue
            weight = (tf * (self.k1 + 1)) / (tf + self.k1 * denominator)
            total += self._idf(term) * weight * query_count
        return total

    def score_all(self, query_tokens: list[str]) -> list[float]:
        return [self.score(query_tokens, i) for i in range(self.n_docs)]


class BM25Retriever:
    """Sparse (BM25) retriever over knowledge chunks."""

    method = "bm25"

    def __init__(self, chunk_repository: KnowledgeChunkRepository) -> None:
        self._chunk_repository = chunk_repository

    def retrieve(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: RetrievalFilter | None = None,
    ) -> list[RetrievalCandidate]:
        filter_obj = filters or RetrievalFilter()
        rows = self._chunk_repository.list_retrieval_candidates(
            status=filter_obj.status,
            category=filter_obj.category,
            language=filter_obj.language,
        )
        if not rows:
            return []
        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scored_texts: list[str] = []
        seeds: list[RetrievalCandidate] = []
        for row in rows:
            seeds.append(seed_from_chunk(row))
            scored_texts.append(
                (row.section + "\n" if row.section else "") + row.content
            )

        tokenized_corpus = [tokenize(t) for t in scored_texts]
        index = BM25Index(tokenized_corpus)
        # Minimum-overlap gate: multi-token queries need >= 2 distinct shared
        # tokens, which suppresses single-coincidence false positives (e.g. a
        # shared common verb like 计算). Single-token queries keep threshold 1.
        query_set = set(query_tokens)
        min_shared = 2 if len(query_set) > 1 else 1
        scored: list[tuple[float, int]] = []
        for doc_index, doc_tokens in enumerate(tokenized_corpus):
            shared = len(query_set & set(doc_tokens))
            if shared < min_shared:
                continue
            score = index.score(query_tokens, doc_index)
            if score > 0.0:
                scored.append((score, doc_index))
        # deterministic order: higher BM25 score first, then lower chunk id
        scored.sort(key=lambda pair: (-pair[0], seeds[pair[1]].chunk_id))
        results: list[RetrievalCandidate] = []
        for rank, (score, doc_index) in enumerate(scored[:top_k], start=1):
            seed = seeds[doc_index]
            results.append(
                RetrievalCandidate(
                    chunk_id=seed.chunk_id,
                    document_id=seed.document_id,
                    title=seed.title,
                    category=seed.category,
                    version=seed.version,
                    section=seed.section,
                    content=seed.content,
                    score=score,
                    rank=rank,
                    retrieval_methods=(self.method,),
                    metadata=seed.metadata,
                )
            )
        return results