"""Deterministic query / text preprocessing for retrieval (Phase 3B).

Boundary: NO LLM-based query rewriting here - rewriting belongs to a later phase.
Only: Unicode NFKC normalization, whitespace normalization, empty-query
validation, and a dependency-free deterministic tokenizer for BM25.

Tokenizer: ASCII alnum runs become one token; CJK runs become sliding character
bigrams (single character when the run has length 1). CJK bigrams that contain
a high-frequency function character (的/了/吗/... ) are dropped because they
create spurious matches (e.g. 的电 matching 电影) that hurt ranking quality.
This is intentionally not a linguistic segmenter; it is deterministic and
adequate for the fixed Chinese development corpus.
"""
from __future__ import annotations

import re
import unicodedata

from app.retrieval.types import EmptyQueryError

_WHITESPACE = re.compile(r"\s+")
_ASCII_WORD = re.compile(r"[A-Za-z0-9]+")
_CJK_RUN = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")

# High-frequency function characters: bigrams containing one of these are noise.
CJK_STOP_CHARS = frozenset(
    "的了么吗呢啊吧呀哦嘛喔在是有可也这那和与就都会能把请向问什么怎让别不还又"
)


def preprocess_query(raw: str) -> str:
    """Normalize a raw user query. Empty / punctuation-only input raises."""
    if not isinstance(raw, str):
        raise EmptyQueryError("query must be a string")
    text = unicodedata.normalize("NFKC", raw)
    text = _WHITESPACE.sub(" ", text).strip()
    if not text:
        raise EmptyQueryError("query is empty after preprocessing")
    if not any(ch.isalnum() for ch in text):
        raise EmptyQueryError("query has no searchable content")
    return text


def _split_runs(text: str) -> list[str]:
    runs: list[str] = []
    index = 0
    for match in _CJK_RUN.finditer(text):
        if match.start() > index:
            runs.extend(_ASCII_WORD.findall(text[index : match.start()]))
        runs.append(match.group(0))
        index = match.end()
    if index < len(text):
        runs.extend(_ASCII_WORD.findall(text[index:]))
    return [r for r in runs if r]


def _clean_pair(left: str, right: str) -> bool:
    return left not in CJK_STOP_CHARS and right not in CJK_STOP_CHARS


def tokenize(text: str) -> list[str]:
    """Deterministic tokens for BM25 (no external dependencies)."""
    normalized = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    for run in _split_runs(normalized):
        if not run:
            continue
        if run.isascii():
            tokens.append(run)
            continue
        chars = list(run)
        if len(chars) == 1:
            if chars[0] not in CJK_STOP_CHARS:
                tokens.append(chars[0])
            continue
        for left, right in zip(chars, chars[1:]):
            if _clean_pair(left, right):
                tokens.append(left + right)
    return tokens