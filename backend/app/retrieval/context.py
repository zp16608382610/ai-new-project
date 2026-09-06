"""Context assembly (Phase 3C) - reranked candidates into the final LLM context.

Responsibility:
    decide WHAT knowledge the model is allowed to rely on:
    1. consume reranked candidates (relevance order from the reranker)
    2. dedupe without ever merging different versions
    3. keep relevance ordering
    4. cap the final chunk count
    5. cap the token / character budget (deterministic approximation)
    6. keep source metadata + citation / traceability per item
    7. emit a typed ContextPackage for the later Agent/LLM layer

Grounding boundary (must hold):
    This layer NEVER fabricates answers, auto-fills knowledge, calls business
    APIs, queries orders, or executes refunds / cancels. Static knowledge
    travels through RAG; dynamic business facts are obtained through Tools in
    a later Agent phase. Whatever is not inside the final context is not
    ground for the model to answer from.

Dedupe policy (defensive, version-safe):
    - identical chunk_id: keep the first occurrence
    - identical content inside the same document (same document_id + section):
      keep the first occurrence
    Duplicate detection is document-scoped on purpose: two versions of one
    policy that share identical wording are NEVER merged (document_id differs,
    so both stay available and separately traceable).

Source priority (defensive):
    The repository already defaults retrieval to ACTIVE documents, so this is
    mostly a safety net: when the same (category, title, section) is present
    from an ACTIVE and a DRAFT/ARCHIVED version (explicit relaxed retrieval),
    only the ACTIVE version is kept for that group. Versions are never
    rewritten or deleted - dropping happens only inside the assembled context.

Token estimation:
    No tokenizer dependency exists yet. estimate_tokens() is a deterministic
    approximation (1 CJK char ~ 1 token, ~4 ASCII alnum chars per token) and
    is explicitly documented as an approximation, not a real tokenizer count.
    ContextBudget can later be swapped for a tokenizer-backed implementation.
"""
from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from app.db.enums import KnowledgeCategory
from app.retrieval.rerank import RerankedCandidate
from app.retrieval.text import tokenize

_CJK_START = ord("\u3400")
_CJK_END = ord("\u9fff")
_CJK_COMPAT_START = ord("\uf900")
_CJK_COMPAT_END = ord("\ufaff")


def estimate_tokens(text: str) -> int:
    """Deterministic approximation of the token count (see module docstring)."""
    if not text:
        return 0
    normalized = unicodedata.normalize("NFKC", text)
    cjk_count = 0
    ascii_alnum = 0
    for char in normalized:
        codepoint = ord(char)
        if _CJK_START <= codepoint <= _CJK_END or (
            _CJK_COMPAT_START <= codepoint <= _CJK_COMPAT_END
        ):
            cjk_count += 1
        elif char.isascii() and char.isalnum():
            ascii_alnum += 1
    ascii_tokens = (ascii_alnum + 3) // 4
    return max(1, cjk_count + ascii_tokens)


@dataclass(frozen=True)
class ContextBudget:
    """Token budget owned by the context layer (not scattered in callers)."""

    max_tokens: int = 2000
    reserve_tokens: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.max_tokens, bool) or not isinstance(self.max_tokens, int):
            raise ValueError("max_tokens must be an integer")
        if isinstance(self.reserve_tokens, bool) or not isinstance(
            self.reserve_tokens, int
        ):
            raise ValueError("reserve_tokens must be an integer")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be >= 1")
        if self.reserve_tokens < 0:
            raise ValueError("reserve_tokens must be >= 0")
        if self.reserve_tokens >= self.max_tokens:
            raise ValueError("reserve_tokens must be < max_tokens")

    @property
    def available_tokens(self) -> int:
        return self.max_tokens - self.reserve_tokens


@dataclass(frozen=True)
class ContextItem:
    """One chunk admitted into the final context (full citation/traceability)."""

    chunk_id: int
    document_id: int
    source_id: str
    title: str
    category: KnowledgeCategory
    version: str
    status: str | None
    section: str | None
    language: str | None
    content: str
    relevance_score: float
    retrieval_methods: tuple[str, ...]

    @property
    def citation(self) -> str:
        """Deterministic human-readable source handle (never a fabricated answer)."""
        parts = [self.title, f"v{self.version}"]
        if self.section:
            parts.append(self.section)
        return " / ".join(parts)


@dataclass(frozen=True)
class ContextPackage:
    """Final context handed to the Agent / LLM layer."""

    query: str
    items: tuple[ContextItem, ...]
    total_items: int
    truncated: bool
    token_budget: int
    estimated_tokens: int

    @property
    def empty(self) -> bool:
        return len(self.items) == 0


def _surface(text: str) -> str:
    return unicodedata.normalize("NFKC", text).lower()


def _content_key(content: str) -> str:
    return " ".join(tokenize(_surface(content)))


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


class ContextAssembler:
    """Reranked candidates -> dedupe -> ACTIVE preference -> token budget."""

    def __init__(self, budget: ContextBudget | None = None) -> None:
        self._budget = budget or ContextBudget()

    @property
    def budget(self) -> ContextBudget:
        return self._budget

    def assemble(
        self,
        query: str,
        reranked: Sequence[RerankedCandidate],
        *,
        budget: ContextBudget | None = None,
    ) -> ContextPackage:
        """Build the final context package from reranked candidates."""
        active_budget = budget or self._budget
        provisional = [self._to_item(entry) for entry in reranked]
        prioritized = self._apply_active_preference(provisional)
        deduped = self._dedupe(prioritized)
        included, used_tokens = self._fit_budget(deduped, active_budget)
        total_items = len(deduped)
        truncated = len(included) < total_items
        return ContextPackage(
            query=query,
            items=tuple(included),
            total_items=total_items,
            truncated=truncated,
            token_budget=active_budget.available_tokens,
            estimated_tokens=used_tokens,
        )

    @staticmethod
    def _group_key(item: ContextItem) -> tuple[KnowledgeCategory, str, str]:
        return (item.category, item.title, item.section or "")

    @staticmethod
    def _is_active(item: ContextItem) -> bool:
        return (item.status or "").upper() == "ACTIVE"

    @classmethod
    def _apply_active_preference(cls, items: list[ContextItem]) -> list[ContextItem]:
        """Keep ACTIVE members per (category, title, section) group; drop the rest.

        DRAFT / ARCHIVED chunks remain usable only for groups that have no
        ACTIVE version at all (defensive; normal retrieval never returns them).
        """
        active_groups = {
            cls._group_key(item) for item in items if cls._is_active(item)
        }
        kept: list[ContextItem] = []
        for item in items:
            if cls._is_active(item) or cls._group_key(item) not in active_groups:
                kept.append(item)
        return kept

    @staticmethod
    def _dedupe(items: list[ContextItem]) -> list[ContextItem]:
        seen_chunk_ids: set[int] = set()
        seen_document_content: set[tuple[int, str | None, str]] = set()
        deduped: list[ContextItem] = []
        for item in items:
            if item.chunk_id in seen_chunk_ids:
                continue
            key = (item.document_id, item.section, _content_key(item.content))
            if key in seen_document_content:
                continue
            seen_chunk_ids.add(item.chunk_id)
            seen_document_content.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _fit_budget(
        items: list[ContextItem], budget: ContextBudget
    ) -> tuple[list[ContextItem], int]:
        """Add whole chunks in relevance order until the budget is exhausted.

        A chunk that would exceed the remaining budget stops the loop (never
        split mid-chunk, so no context is truncated to an unreadable degree).
        An empty result is explicit: raise the budget or lower reserve_tokens.
        """
        included: list[ContextItem] = []
        used = 0
        available = budget.available_tokens
        for item in items:
            item_tokens = estimate_tokens(item.content)
            if used + item_tokens > available:
                break
            included.append(item)
            used += item_tokens
        return included, used

    @staticmethod
    def _to_item(entry: RerankedCandidate) -> ContextItem:
        candidate = entry.candidate
        metadata = candidate.metadata or {}
        return ContextItem(
            chunk_id=candidate.chunk_id,
            document_id=candidate.document_id,
            source_id=str(metadata.get("source", "")),
            title=candidate.title,
            category=candidate.category,
            version=candidate.version,
            status=_as_optional_str(metadata.get("status")),
            section=candidate.section,
            language=_as_optional_str(metadata.get("language")),
            content=candidate.content,
            relevance_score=entry.rerank_score,
            retrieval_methods=candidate.retrieval_methods,
        )