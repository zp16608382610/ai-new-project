"""After-sales domain logic (Phase 9C).

Pure, DB-free and LLM-free: deterministic eligibility rules plus the small
adapter that turns retrieved policy evidence into structured conditions.

    app.after_sales.eligibility  -> EligibilityEngine / EligibilityResult
    app.after_sales.policy       -> PolicyFacts extraction from RAG evidence

The DB-facing orchestration lives in
``app.services.after_sales_investigation``.
"""
from app.after_sales.eligibility import (
    ACTION_EXCHANGE,
    ACTION_REFUND,
    ACTION_REPAIR,
    ACTION_UNKNOWN,
    CASE_TYPE_LOGISTICS_DISPUTE,
    CASE_TYPE_OTHER,
    CASE_TYPE_QUALITY_ISSUE,
    STATUS_ELIGIBILITY_CHECK,
    STATUS_INFORMATION_COLLECTION,
    STATUS_PROCESSING,
    STATUS_REJECTED,
    BusinessFacts,
    CaseFacts,
    EligibilityEngine,
    EligibilityResult,
    PolicyFacts,
)
from app.after_sales.policy import (
    POLICY_CATEGORY_BY_ACTION,
    build_policy_query,
    extract_policy_facts,
    parse_window_days,
)

__all__ = [
    "ACTION_EXCHANGE",
    "ACTION_REFUND",
    "ACTION_REPAIR",
    "ACTION_UNKNOWN",
    "CASE_TYPE_LOGISTICS_DISPUTE",
    "CASE_TYPE_OTHER",
    "CASE_TYPE_QUALITY_ISSUE",
    "STATUS_ELIGIBILITY_CHECK",
    "STATUS_INFORMATION_COLLECTION",
    "STATUS_PROCESSING",
    "STATUS_REJECTED",
    "POLICY_CATEGORY_BY_ACTION",
    "BusinessFacts",
    "CaseFacts",
    "EligibilityEngine",
    "EligibilityResult",
    "PolicyFacts",
    "build_policy_query",
    "extract_policy_facts",
    "parse_window_days",
]
