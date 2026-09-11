"""Deterministic after-sales eligibility engine (Phase 9C).

Four responsibilities stay strictly separated (docs/DECISIONS.md Decision 045):

    LLM         -> natural-language understanding only; never ``eligible=true``
    Business    -> authoritative facts (order exists / owner / status / items /
                   refunds / timestamps)
    RAG         -> static policy evidence (window + conditions) and citations
    This engine -> the deterministic eligibility conclusion

The engine is pure: no SQLAlchemy, no retrieval, no LLM import. Callers pass
already-resolved facts in and receive a structured, serializable result.

``eligible`` is tri-state on purpose:

    True  -> the case may continue to the next phase (case status PROCESSING)
    False -> the case is deterministically NOT eligible (case status REJECTED)
    None  -> no conclusion could be reached (missing business facts or missing
             policy evidence). "Not found" / "not covered" must NEVER be
             reported as "the customer has no after-sales eligibility".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

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

ORDER_STATUS_DELIVERED = "DELIVERED"

MISSING_ORDER_ID = "order_id"
MISSING_REQUESTED_ACTION = "requested_action"

# ---- deterministic rule ids -------------------------------------------------

RULE_ORDER_AVAILABLE = "order_available"
RULE_ORDER_OWNED_BY_USER = "order_owned_by_user"
RULE_POLICY_EVIDENCE = "policy_evidence"
RULE_POLICY_COVERS_ACTION = "policy_covers_action"
RULE_ORDER_STATUS_DELIVERED = "order_status_delivered"
RULE_NO_ACTIVE_REFUND = "no_active_refund"
RULE_ITEMS_RETURNABLE = "items_returnable"
RULE_AFTER_SALES_WINDOW = "after_sales_window"
RULE_EXCHANGE_BRANCH = "exchange_branch"

_ACTION_LABEL = {
    ACTION_REFUND: "退款",
    ACTION_EXCHANGE: "换货",
    ACTION_REPAIR: "维修",
    ACTION_UNKNOWN: "售后",
}

_CASE_TYPE_LABEL = {
    CASE_TYPE_QUALITY_ISSUE: "商品质量问题",
    CASE_TYPE_LOGISTICS_DISPUTE: "物流异常",
    CASE_TYPE_OTHER: "其他售后问题",
}


def action_label(value: str | None) -> str:
    return _ACTION_LABEL.get(str(value or ""), "售后")


def case_type_label(value: str | None) -> str:
    return _CASE_TYPE_LABEL.get(str(value or ""), "其他售后问题")


@dataclass(frozen=True)
class CaseFacts:
    """What the case itself asks for (Phase 9B state, nothing inferred here)."""

    case_id: str
    user_id: int
    case_type: str
    requested_action: str
    problem_description: str = ""
    order_ref: str | None = None
    order_id: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "user_id": self.user_id,
            "case_type": self.case_type,
            "requested_action": self.requested_action,
            "order_ref": self.order_ref,
            "order_id": self.order_id,
        }


@dataclass(frozen=True)
class BusinessFacts:
    """Authoritative business facts (service layer -> this engine only).

    ``investigation_error`` carries an explicit failure code:
        ORDER_NOT_FOUND / ORDER_NOT_OWNED / ORDER_REF_UNRESOLVED
    A failure here means "we could not investigate", NOT "not eligible".
    """

    order_id: int | None = None
    order_exists: bool = False
    owner_user_id: int | None = None
    order_status: str | None = None
    total_amount: str | None = None
    currency: str | None = None
    items_returnable: bool | None = None
    active_refund_count: int = 0
    delivery_reference_at: str | None = None
    delivery_reference_source: str | None = None
    days_since_delivery: int | None = None
    investigation_error: str | None = None
    source: str = "OrderService"

    def to_dict(self) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "order_exists": self.order_exists,
            "owner_user_id": self.owner_user_id,
            "order_status": self.order_status,
            "total_amount": self.total_amount,
            "currency": self.currency,
            "items_returnable": self.items_returnable,
            "active_refund_count": self.active_refund_count,
            "delivery_reference_at": self.delivery_reference_at,
            "delivery_reference_source": self.delivery_reference_source,
            "days_since_delivery": self.days_since_delivery,
            "investigation_error": self.investigation_error,
            "source": self.source,
        }


@dataclass(frozen=True)
class PolicyFacts:
    """Structured conditions extracted from the RETRIEVED policy evidence.

    ``window_days`` / ``requires_quality_issue`` always come from a retrieved
    chunk (and keep that chunk's citation); they are never re-typed by hand in
    the engine. ``covers_action`` is computed from the retrieved document
    category, so a policy that was not retrieved can never be assumed.
    """

    action: str
    query: str = ""
    covers_action: bool = False
    window_days: int | None = None
    window_citation: str | None = None
    requires_quality_issue: bool = False
    citations: tuple[str, ...] = ()
    evidence: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "query": self.query,
            "covers_action": self.covers_action,
            "window_days": self.window_days,
            "window_citation": self.window_citation,
            "requires_quality_issue": self.requires_quality_issue,
            "citations": list(self.citations),
            "evidence": [dict(item) for item in self.evidence],
        }


@dataclass(frozen=True)
class EligibilityResult:
    """Structured eligibility conclusion (serializable, no ORM types)."""

    eligible: bool | None
    status: str
    reason: str
    failed_rules: tuple[str, ...] = ()
    missing_information: tuple[str, ...] = ()
    policy_citations: tuple[str, ...] = ()
    business_facts: dict[str, Any] = field(default_factory=dict)
    policy_facts: dict[str, Any] = field(default_factory=dict)
    investigation: dict[str, Any] = field(default_factory=dict)
    requires_human_review: bool = False

    @property
    def concluded(self) -> bool:
        return self.eligible is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "eligible": self.eligible,
            "status": self.status,
            "reason": self.reason,
            "failed_rules": list(self.failed_rules),
            "missing_information": list(self.missing_information),
            "policy_citations": list(self.policy_citations),
            "business_facts": dict(self.business_facts),
            "policy_facts": dict(self.policy_facts),
            "investigation": dict(self.investigation),
            "requires_human_review": self.requires_human_review,
        }


class EligibilityEngine:
    """Deterministic rule evaluation over business facts + policy evidence.

    Rule order (first match wins) is deliberate: an investigation failure is
    never turned into an ineligibility, and a definite business-rule failure is
    reported before any "cannot conclude" branch.
    """

    rules = (
        RULE_ORDER_AVAILABLE,
        RULE_ORDER_OWNED_BY_USER,
        RULE_POLICY_EVIDENCE,
        RULE_POLICY_COVERS_ACTION,
        RULE_ORDER_STATUS_DELIVERED,
        RULE_NO_ACTIVE_REFUND,
        RULE_ITEMS_RETURNABLE,
        RULE_AFTER_SALES_WINDOW,
        RULE_EXCHANGE_BRANCH,
    )

    def evaluate(
        self,
        *,
        case: CaseFacts,
        business: BusinessFacts | None,
        policy: PolicyFacts | None,
    ) -> EligibilityResult:
        business_facts = business.to_dict() if business is not None else {}
        policy_facts = policy.to_dict() if policy is not None else {}

        failure = self._investigation_failure(case, business, business_facts, policy_facts)
        if failure is not None:
            return failure

        assert business is not None  # guarded by _investigation_failure
        if business.owner_user_id != case.user_id:
            return self._investigation_failure(
                case,
                business,
                business_facts,
                policy_facts,
                error="ORDER_NOT_OWNED",
            )

        if policy is None or not policy.citations:
            return EligibilityResult(
                eligible=None,
                status=STATUS_ELIGIBILITY_CHECK,
                reason=(
                    "未检索到覆盖该售后诉求的政策依据,无法自动完成资格判定。"
                ),
                failed_rules=(RULE_POLICY_EVIDENCE,),
                business_facts=business_facts,
                policy_facts=policy_facts,
                requires_human_review=True,
            )

        if not policy.covers_action:
            return EligibilityResult(
                eligible=None,
                status=STATUS_ELIGIBILITY_CHECK,
                reason=(
                    f"现有知识库政策未覆盖「{action_label(case.requested_action)}」诉求,"
                    "无法自动完成资格判定。"
                ),
                failed_rules=(RULE_POLICY_COVERS_ACTION,),
                policy_citations=policy.citations,
                business_facts=business_facts,
                policy_facts=policy_facts,
                requires_human_review=True,
            )

        failed: list[str] = []
        if (business.order_status or "").upper() != ORDER_STATUS_DELIVERED:
            failed.append(RULE_ORDER_STATUS_DELIVERED)
        if business.active_refund_count > 0:
            failed.append(RULE_NO_ACTIVE_REFUND)
        if (
            case.requested_action == ACTION_REFUND
            and business.items_returnable is False
        ):
            failed.append(RULE_ITEMS_RETURNABLE)

        days = business.days_since_delivery
        window = policy.window_days
        if window is None or days is None:
            return EligibilityResult(
                eligible=None,
                status=STATUS_ELIGIBILITY_CHECK,
                reason="缺少可用的签收时间或政策时效,无法判断是否在售后期限内。",
                failed_rules=(RULE_AFTER_SALES_WINDOW,),
                policy_citations=policy.citations,
                business_facts=business_facts,
                policy_facts=policy_facts,
                requires_human_review=True,
            )
        if days > window:
            failed.append(RULE_AFTER_SALES_WINDOW)

        if case.requested_action == ACTION_EXCHANGE and failed == []:
            quality_branch = case.case_type == CASE_TYPE_QUALITY_ISSUE
            returnable_branch = business.items_returnable is True
            if not quality_branch and not returnable_branch:
                return EligibilityResult(
                    eligible=None,
                    status=STATUS_ELIGIBILITY_CHECK,
                    reason=(
                        "该换货诉求既非质量问题,订单商品也不支持无理由退换,"
                        "政策未覆盖该情形,需人工处理。"
                    ),
                    failed_rules=(RULE_EXCHANGE_BRANCH,),
                    policy_citations=policy.citations,
                    business_facts=business_facts,
                    policy_facts=policy_facts,
                    requires_human_review=True,
                )

        if failed:
            return EligibilityResult(
                eligible=False,
                status=STATUS_REJECTED,
                reason=self._failure_reason(failed[0], business, days, window),
                failed_rules=tuple(failed),
                policy_citations=policy.citations,
                business_facts=business_facts,
                policy_facts=policy_facts,
            )

        return EligibilityResult(
            eligible=True,
            status=STATUS_PROCESSING,
            reason=(
                f"订单已签收 {days} 天,用户反馈为{case_type_label(case.case_type)},"
                f"当前售后政策支持签收后 {window} 天内申请"
                f"{action_label(case.requested_action)}。"
            ),
            policy_citations=policy.citations,
            business_facts=business_facts,
            policy_facts=policy_facts,
        )

    # -- helpers ------------------------------------------------------------

    def _investigation_failure(
        self,
        case: CaseFacts,
        business: BusinessFacts | None,
        business_facts: dict[str, Any],
        policy_facts: dict[str, Any],
        *,
        error: str | None = None,
    ) -> EligibilityResult | None:
        """Return an INFORMATION_COLLECTION result when investigation failed.

        Never returns eligible=False: "we could not investigate this order" and
        "this customer has no after-sales eligibility" are different facts.
        """
        code = error or (business.investigation_error if business is not None else None)
        if code is None and business is not None and business.order_exists:
            return None
        if code is None:
            code = "ORDER_NOT_FOUND"
        ref = case.order_ref or (f"ORD-{case.order_id}" if case.order_id else "该订单")
        if code == "ORDER_NOT_OWNED":
            reason = f"订单 {ref} 不属于当前用户,无法完成售后资格核查。"
        else:
            reason = f"未找到订单 {ref} 的权威业务数据,无法完成售后资格核查。"
        return EligibilityResult(
            eligible=None,
            status=STATUS_INFORMATION_COLLECTION,
            reason=reason,
            failed_rules=(RULE_ORDER_AVAILABLE,),
            missing_information=(MISSING_ORDER_ID,),
            business_facts=business_facts,
            policy_facts=policy_facts,
        )

    @staticmethod
    def _failure_reason(
        rule: str, business: BusinessFacts, days: int | None, window: int | None
    ) -> str:
        if rule == RULE_AFTER_SALES_WINDOW:
            return (
                f"该订单已超过当前政策规定的售后期限（政策为签收后 {window} 天内,"
                f"该订单已签收 {days} 天）。"
            )
        if rule == RULE_ORDER_STATUS_DELIVERED:
            return (
                f"订单当前状态为 {business.order_status or '未知'},"
                "售后政策要求订单已签收(DELIVERED)后才能申请。"
            )
        if rule == RULE_NO_ACTIVE_REFUND:
            return "该订单存在在途退款申请,按政策不能重复发起售后申请。"
        if rule == RULE_ITEMS_RETURNABLE:
            return "该订单包含不可退商品,不符合退款条件。"
        return "该订单不符合当前售后政策条件。"
