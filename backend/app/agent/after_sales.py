"""After-sales case understanding for the agent (Phase 9B).

Phase 9B gives the agent ONE new capability: recognising that a user message is
an after-sales *handling* request ("my earbuds broke", "I want an exchange")
and turning it into structured case information (case type, requested action,
problem statement, missing information).

Boundary rules:
    - This module is pure domain logic: no SQLAlchemy import, no LLM call, no
      persistence. The DB-facing orchestration lives in
      ``app.services.after_sales_case_manager``; ``AgentWorkflow`` only sees the
      ``AfterSalesCaseManagerLike`` Protocol.
    - The vocabulary below mirrors ``app.db.enums`` (AfterSalesCaseType /
      AfterSalesRequestedAction / AfterSalesCaseStatus) as plain strings so the
      agent layer keeps its "no persistence import" rule. The service layer
      validates these values against the real enums, so a free-form model output
      can never invent a new case type.
    - Detection is deterministic and testable. A future LLM-based detector can
      replace ``DeterministicAfterSalesCaseDetector`` behind the same Protocol;
      an LLM proposal is only ever an input to the case, never a business fact
      (see docs/DECISIONS.md Decision 044).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.agent.entities import ExtractedEntities

# ---- vocabulary (mirrors app.db.enums as plain strings) --------------------

CASE_TYPE_QUALITY_ISSUE = "QUALITY_ISSUE"
CASE_TYPE_LOGISTICS_DISPUTE = "LOGISTICS_DISPUTE"
CASE_TYPE_OTHER = "OTHER"

ACTION_REFUND = "REFUND"
ACTION_EXCHANGE = "EXCHANGE"
ACTION_REPAIR = "REPAIR"
ACTION_UNKNOWN = "UNKNOWN"

STATUS_INFORMATION_COLLECTION = "INFORMATION_COLLECTION"
STATUS_ELIGIBILITY_CHECK = "ELIGIBILITY_CHECK"
STATUS_PROCESSING = "PROCESSING"
STATUS_REJECTED = "REJECTED"

MISSING_ORDER_ID = "order_id"
MISSING_REQUESTED_ACTION = "requested_action"
MISSING_PROBLEM_DESCRIPTION = "problem_description"


@dataclass(frozen=True)
class AfterSalesSignal:
    """What one message says about an after-sales case (detector output)."""

    is_case_request: bool
    case_type: str | None = None
    requested_action: str | None = None
    problem_description: str | None = None
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_case_request": self.is_case_request,
            "case_type": self.case_type,
            "requested_action": self.requested_action,
            "problem_description": self.problem_description,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class AfterSalesCaseOutcome:
    """Structured result of one case upsert (created or updated).

    ``order_id`` is the *resolved* business order key (an int FK) and is only
    set when the referenced order really exists in the business system.
    ``order_ref`` preserves the reference the user actually gave (e.g.
    "ORD-1004") even when it cannot be resolved; an unresolved reference is
    never promoted to a business fact.
    """

    case_id: str
    user_id: int
    case_type: str
    requested_action: str
    problem_description: str
    status: str
    risk_level: str
    missing_information: tuple[str, ...] = ()
    collected_information: dict[str, Any] = field(default_factory=dict)
    order_id: int | None = None
    order_ref: str | None = None
    created: bool = False

    @property
    def needs_information(self) -> bool:
        return bool(self.missing_information)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "user_id": self.user_id,
            "order_id": self.order_id,
            "order_ref": self.order_ref,
            "case_type": self.case_type,
            "requested_action": self.requested_action,
            "problem_description": self.problem_description,
            "status": self.status,
            "risk_level": self.risk_level,
            "collected_information": dict(self.collected_information),
            "missing_information": list(self.missing_information),
            "created": self.created,
        }


class AfterSalesCaseManagerLike(Protocol):
    """Narrow interface the workflow depends on (Phase 9B).

    ``AfterSalesCaseManager`` (service layer) structurally satisfies it. The
    workflow never imports SQLAlchemy / services directly.
    """

    def handle(
        self,
        *,
        user_id: int | None,
        session_id: str | None,
        user_message: str,
        entities: ExtractedEntities | None = None,
        active_case_id: str | None = None,
    ) -> AfterSalesCaseOutcome | None:
        """Return the upserted case, or None when the message is not a case."""
        ...


class AfterSalesCaseDetector(Protocol):
    """Interface implemented by the deterministic and future detectors."""

    def detect(self, text: str) -> AfterSalesSignal:
        """Return the after-sales signal found in one user message."""
        ...


# ---------------------------------------------------------------------------
# Deterministic rule vocabulary (test/architecture implementation, not NLP).
# ---------------------------------------------------------------------------

_QUALITY_MARKERS = (
    "坏",
    "损坏",
    "破损",
    "故障",
    "失灵",
    "质量问题",
    "质量有问题",
    "质量差",
    "质量不行",
    "质量不好",
    "有瑕疵",
    "瑕疵",
    "有毛病",
    "缺陷",
    "不能用",
    "无法使用",
    "不好用",
    "用不了",
    "碎了",
    "裂了",
    "开胶",
    "漏液",
    "划痕",
    "发霉",
    "变质",
    "过期",
    "缺件",
    "少件",
)

_LOGISTICS_DISPUTE_MARKERS = (
    "少发",
    "漏发",
    "错发",
    "发错",
    "没收到",
    "未收到",
    "丢件",
    "包裹丢失",
    "没发货",
    "未发货",
    "物流异常",
)

_EXCHANGE_MARKERS = ("换货", "换新", "更换", "换一个", "换一件", "换台")
_REPAIR_MARKERS = ("维修", "修理", "保修", "返修", "修一下", "修好", "修一修")
_REFUND_MARKERS = ("退款", "退货", "退钱", "退掉", "退了", "退单")

# An explicit human-handoff request keeps its existing meaning: it is NOT turned
# into an after-sales case (the escalate path owns it).
_HANDOFF_MARKERS = (
    "转人工",
    "人工客服",
    "人工坐席",
    "人工处理",
    "人工服务",
    "找客服",
    "人工介入",
)

_PROBLEM_MARKERS = _QUALITY_MARKERS + _LOGISTICS_DISPUTE_MARKERS
# NFKC normalisation maps full-width punctuation to ASCII, so both forms are
# split here (the detector works on the normalised surface form).
_CLAUSE_SPLIT_RE = re.compile(r"[,，、。.!！?？;；:：\n\r]+")


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _matches(text: str, markers: tuple[str, ...]) -> list[str]:
    return [marker for marker in markers if marker in text]


class DeterministicAfterSalesCaseDetector:
    """Deterministic after-sales request detector (architecture/test impl).

    Rules (documented, ordered, no guessing):
        case_type         quality markers -> QUALITY_ISSUE,
                          logistics markers -> LOGISTICS_DISPUTE,
                          otherwise None (the service stores OTHER).
        requested_action  exchange > repair > refund priority.
        problem_statement the first clause that contains a problem marker
                          (e.g. "我的耳机坏了，帮我处理一下。" -> "我的耳机坏了").
        is_case_request   a problem / exchange / repair signal is present and
                          the user did not ask for a human hand-off. A bare
                          refund phrase is NOT a case request: refund requests
                          keep their existing REFUND_REQUEST flow.
    """

    name = "deterministic_after_sales"

    def detect(self, text: str) -> AfterSalesSignal:
        surface = _normalize(text)
        if not surface.strip():
            return AfterSalesSignal(False, reason="empty message")
        handoff = _matches(surface, _HANDOFF_MARKERS)
        if handoff:
            return AfterSalesSignal(
                False, reason="explicit human handoff: " + ", ".join(handoff)
            )

        quality = _matches(surface, _QUALITY_MARKERS)
        logistics = _matches(surface, _LOGISTICS_DISPUTE_MARKERS)
        exchange = _matches(surface, _EXCHANGE_MARKERS)
        repair = _matches(surface, _REPAIR_MARKERS)
        refund = _matches(surface, _REFUND_MARKERS)

        if quality:
            case_type: str | None = CASE_TYPE_QUALITY_ISSUE
        elif logistics:
            case_type = CASE_TYPE_LOGISTICS_DISPUTE
        else:
            case_type = None

        if exchange:
            action: str | None = ACTION_EXCHANGE
        elif repair:
            action = ACTION_REPAIR
        elif refund:
            action = ACTION_REFUND
        else:
            action = None

        is_case_request = bool(quality or logistics or exchange or repair)
        problem = self._problem_clause(surface) if is_case_request else None
        evidence = quality + logistics + exchange + repair + refund
        return AfterSalesSignal(
            is_case_request=is_case_request,
            case_type=case_type,
            requested_action=action,
            problem_description=problem,
            reason=("after-sales markers: " + ", ".join(evidence))
            if evidence
            else "no after-sales marker",
        )

    @staticmethod
    def _problem_clause(surface: str) -> str | None:
        for clause in _CLAUSE_SPLIT_RE.split(surface):
            cleaned = clause.strip()
            if cleaned and any(marker in cleaned for marker in _PROBLEM_MARKERS):
                return cleaned
        return None


@dataclass(frozen=True)
class AfterSalesInvestigationOutcome:
    """Result of investigating one ELIGIBILITY_CHECK case (Phase 9C).

    Pure, serializable data produced by the service layer:

        case           the updated AfterSalesCaseOutcome (its ``status`` is the
                       new case status: PROCESSING / REJECTED /
                       INFORMATION_COLLECTION)
        eligibility    the serialized EligibilityResult (eligible / reason /
                       failed_rules / policy_citations / business_facts)
        investigation  the order + policy evidence actually used

    The workflow never re-derives any of these values: eligibility is decided
    by the deterministic engine, not by the agent layer (docs/DECISIONS.md
    Decision 045).
    """

    case: AfterSalesCaseOutcome
    eligibility: dict[str, Any] = field(default_factory=dict)
    investigation: dict[str, Any] = field(default_factory=dict)

    @property
    def status(self) -> str:
        return self.case.status

    @property
    def needs_information(self) -> bool:
        return self.case.needs_information

    def to_dict(self) -> dict[str, Any]:
        return {
            "case": self.case.to_dict(),
            "eligibility": dict(self.eligibility),
            "investigation": dict(self.investigation),
        }


class CaseInvestigatorLike(Protocol):
    """Investigation interface the workflow depends on (Phase 9C).

    ``AfterSalesInvestigationService`` (service layer) structurally satisfies
    it: it owns OrderService + the existing RAG pipeline + the deterministic
    EligibilityEngine. The agent layer never imports SQLAlchemy or retrieval,
    and never touches the database itself.
    """

    def investigate(
        self, case: AfterSalesCaseOutcome
    ) -> AfterSalesInvestigationOutcome:
        """Investigate one complete case and return the updated case."""
        ...
