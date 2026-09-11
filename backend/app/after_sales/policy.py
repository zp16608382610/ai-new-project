"""Policy investigation helpers (Phase 9C).

Turns the RETRIEVED policy evidence (the existing Hybrid Retrieval -> Reranker
-> Context Assembly pipeline, reused as-is) into the small structured
``PolicyFacts`` the eligibility engine consumes.

Why a tiny adapter instead of hard-coded rules: the after-sales conditions
already exist in the knowledge base ("签收后十五天内,商品存在质量问题或与描述
不符时支持换货"), but they are natural-language documents. Parsing the window out
of the RETRIEVED chunk - and keeping that chunk's citation - preserves a single
policy source of truth: change the policy document and the eligibility window
changes with it. Nothing is parsed when the relevant document was not retrieved
(the engine then refuses to conclude instead of inventing a policy).

Boundary: pure text/evidence -> structured facts. No DB access, no LLM, no
second policy store, no hand-written second copy of the policy text.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.after_sales.eligibility import (
    ACTION_EXCHANGE,
    ACTION_REFUND,
    CASE_TYPE_LOGISTICS_DISPUTE,
    CASE_TYPE_QUALITY_ISSUE,
    PolicyFacts,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.retrieval.context import ContextPackage

JsonDict = dict[str, Any]

# Which knowledge category is the authoritative evidence for a requested action.
# REPAIR / UNKNOWN deliberately have no entry: the knowledge base has no repair
# policy, so the engine must report "not covered" instead of guessing one.
POLICY_CATEGORY_BY_ACTION = {
    ACTION_REFUND: "REFUND",
    ACTION_EXCHANGE: "EXCHANGE",
}

_ACTION_QUERY_LABEL = {
    ACTION_REFUND: "退款",
    ACTION_EXCHANGE: "换货",
    "REPAIR": "维修",
}

_CASE_TYPE_QUERY_LABEL = {
    CASE_TYPE_QUALITY_ISSUE: "商品质量问题",
    CASE_TYPE_LOGISTICS_DISPUTE: "物流异常",
}

# "十五天" / "十五个自然日" / "7天内" -> the after-sales window in days.
_WINDOW_RE = re.compile(
    r"(?P<num>\d{1,3}|[一二三四五六七八九十两]+)\s*(?:个)?(?:自然)?(?:天|日)"
)
_CN_DIGITS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
}

_MAX_EVIDENCE = 5
_EXCERPT_CHARS = 160


def build_policy_query(case_type: str | None, requested_action: str | None) -> str:
    """Build the RAG query for one case ("商品质量问题 换货 售后政策")."""
    case_label = _CASE_TYPE_QUERY_LABEL.get(str(case_type or ""), "售后")
    action_label = _ACTION_QUERY_LABEL.get(str(requested_action or ""), "处理")
    return f"{case_label} {action_label} 售后政策"


def parse_window_days(text: str) -> int | None:
    """Parse the first after-sales window expressed in days, or None."""
    match = _WINDOW_RE.search(text or "")
    if match is None:
        return None
    raw = match.group("num")
    if raw.isdigit():
        value = int(raw)
        return value if value > 0 else None
    numbers: list[int] = []
    for char in raw:
        if char == "十":
            numbers.append(10)
        elif char in _CN_DIGITS:
            numbers.append(_CN_DIGITS[char])
        else:
            return None
    if raw.startswith("十"):
        return 10 + (numbers[1] if len(numbers) > 1 else 0)
    if 10 in numbers:
        # "三十" -> 30, "二十一" -> 21, "十五" -> 15 (one ten only).
        index = numbers.index(10)
        tens = 1 if index == 0 else numbers[0]
        rest = numbers[index + 1 :]
        return tens * 10 + (rest[0] if rest else 0)
    if len(numbers) == 1:
        return numbers[0]
    return None


def extract_policy_facts(
    package: "ContextPackage | None",
    *,
    action: str,
    case_type: str | None = None,
) -> PolicyFacts:
    """Extract structured policy conditions from retrieved evidence."""
    query = getattr(package, "query", "") or ""
    items = list(getattr(package, "items", ()) or ())
    citations = tuple(str(item.citation) for item in items)
    evidence = tuple(_evidence_item(item) for item in items[:_MAX_EVIDENCE])
    expected_category = POLICY_CATEGORY_BY_ACTION.get(str(action or ""))

    window_days: int | None = None
    window_citation: str | None = None
    requires_quality_issue = False
    covers_action = False
    if expected_category is not None:
        for item in items:
            category = getattr(item, "category", None)
            category_value = getattr(category, "value", category)
            if str(category_value) != expected_category:
                continue
            covers_action = True
            window_days = parse_window_days(str(getattr(item, "content", "")))
            if window_days is not None:
                window_citation = str(item.citation)
            requires_quality_issue = "质量问题" in str(getattr(item, "content", ""))
            break

    return PolicyFacts(
        action=str(action or ""),
        query=query or build_policy_query(case_type, action),
        covers_action=covers_action,
        window_days=window_days,
        window_citation=window_citation,
        requires_quality_issue=requires_quality_issue,
        citations=citations,
        evidence=evidence,
    )


def _evidence_item(item: Any) -> JsonDict:
    content = str(getattr(item, "content", "") or "")
    category = getattr(item, "category", None)
    return {
        "citation": str(getattr(item, "citation", "")),
        "title": str(getattr(item, "title", "")),
        "version": str(getattr(item, "version", "")),
        "section": str(getattr(item, "section", "")),
        "category": str(getattr(category, "value", category) or ""),
        "relevance_score": round(float(getattr(item, "relevance_score", 0.0) or 0.0), 4),
        "excerpt": content[:_EXCERPT_CHARS],
    }
