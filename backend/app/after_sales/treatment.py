"""Deterministic after-sales treatment planner (Phase 9D).

Eligibility decides *whether* a case may continue; the treatment planner
decides *what* the standard handling is. The action is never chosen by the LLM
and never invented here:

    user request (requested_action)
      + case (case_type, problem)
      + deterministic EligibilityResult
      + business rules
      -> TreatmentPlan

``requested_action`` is the ONLY source of a business action: the planner can
confirm REFUND / EXCHANGE / REPAIR, and it can refuse to act (UNKNOWN, not
eligible, inconclusive). It can never turn a refund request into an exchange,
and it never executes anything - execution belongs to a later phase behind the
Risk Gate / Human-in-the-loop layers.

Boundary: this module is pure. No SQLAlchemy, no LLM, no retrieval and no
service import; ticket creation lives in
``app.services.after_sales_treatment`` (docs/DECISIONS.md Decision 047).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---- vocabulary (mirrors app.db.enums / app.after_sales.eligibility) -------

ACTION_REFUND = "REFUND"
ACTION_EXCHANGE = "EXCHANGE"
ACTION_REPAIR = "REPAIR"
ACTION_UNKNOWN = "UNKNOWN"

# The ONLY actions a treatment plan may ever carry.
EXECUTABLE_ACTIONS: tuple[str, ...] = (ACTION_REFUND, ACTION_EXCHANGE, ACTION_REPAIR)

CASE_TYPE_QUALITY_ISSUE = "QUALITY_ISSUE"
CASE_TYPE_LOGISTICS_DISPUTE = "LOGISTICS_DISPUTE"
CASE_TYPE_OTHER = "OTHER"

STATUS_INFORMATION_COLLECTION = "INFORMATION_COLLECTION"
STATUS_ELIGIBILITY_CHECK = "ELIGIBILITY_CHECK"
STATUS_PROCESSING = "PROCESSING"
STATUS_REJECTED = "REJECTED"

# One ticket category for every after-sales treatment ticket (Phase 9D keeps
# priority/category deliberately simple; risk routing is Phase 9E's job).
TICKET_CATEGORY_AFTER_SALES = "AFTER_SALES"

_ACTION_LABEL = {
    ACTION_REFUND: "退款",
    ACTION_EXCHANGE: "换货",
    ACTION_REPAIR: "维修",
    ACTION_UNKNOWN: "售后",
}

_CASE_TYPE_LABEL = {
    CASE_TYPE_QUALITY_ISSUE: "质量问题",
    CASE_TYPE_LOGISTICS_DISPUTE: "物流异常",
    CASE_TYPE_OTHER: "其他售后问题",
}

# What happens after the ticket exists. Execution itself is NOT part of 9D.
_NEXT_STEP = {
    ACTION_REFUND: "等待后续退款执行",
    ACTION_EXCHANGE: "等待后续换货执行",
    ACTION_REPAIR: "等待后续维修执行",
}

AWAIT_REQUESTED_ACTION = "向用户确认售后诉求（退款/换货/维修）"
ESCALATE_TO_HUMAN = "转人工客服复核"


def action_label(value: str | None) -> str:
    return _ACTION_LABEL.get(str(value or ""), "售后")


def case_type_label(value: str | None) -> str:
    return _CASE_TYPE_LABEL.get(str(value or ""), "其他售后问题")


@dataclass(frozen=True)
class EligibilitySummary:
    """The eligibility fields the planner is allowed to read.

    Built from the serialized ``EligibilityResult`` the 9C investigation
    already produced; the planner never re-derives eligibility and never
    re-runs the business or policy rules.
    """

    eligible: bool | None
    status: str = STATUS_ELIGIBILITY_CHECK
    reason: str = ""
    failed_rules: tuple[str, ...] = ()
    policy_citations: tuple[str, ...] = ()
    requires_human_review: bool = False
    # Read-only copy of the authoritative business facts behind the conclusion
    # (order status is quoted into the ticket description verbatim).
    business_facts: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EligibilitySummary":
        source = data if isinstance(data, dict) else {}
        eligible = source.get("eligible")
        return cls(
            eligible=eligible if isinstance(eligible, bool) else None,
            status=str(source.get("status") or STATUS_ELIGIBILITY_CHECK),
            reason=str(source.get("reason") or ""),
            failed_rules=tuple(str(item) for item in (source.get("failed_rules") or [])),
            policy_citations=tuple(
                str(item) for item in (source.get("policy_citations") or [])
            ),
            requires_human_review=bool(source.get("requires_human_review")),
            business_facts=(
                dict(source["business_facts"])
                if isinstance(source.get("business_facts"), dict)
                else {}
            ),
        )


@dataclass(frozen=True)
class TreatmentPlan:
    """A standard, constrained handling plan for one after-sales case.

    ``executable`` is True only when a real business action was confirmed by
    both the case's ``requested_action`` and the deterministic eligibility
    result. ``requires_execution`` then means "the next phase must execute this
    action" - it never means "this phase executed it".
    """

    case_id: str
    case_status: str
    action: str | None = None
    reason: str = ""
    case_type: str = CASE_TYPE_OTHER
    order_id: int | None = None
    order_ref: str | None = None
    required_next_step: str = ""
    requires_execution: bool = False
    requires_human_review: bool = False
    executable: bool = False
    # Set once the after-sales ticket really exists; stays None when nothing
    # was registered (not eligible / no action / creation failed).
    ticket_id: int | None = None
    ticket_category: str = TICKET_CATEGORY_AFTER_SALES
    policy_citations: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "action_label": action_label(self.action) if self.action else None,
            "reason": self.reason,
            "case_id": self.case_id,
            "case_status": self.case_status,
            "case_type": self.case_type,
            "order_id": self.order_id,
            "order_ref": self.order_ref,
            "required_next_step": self.required_next_step,
            "requires_execution": self.requires_execution,
            "requires_human_review": self.requires_human_review,
            "executable": self.executable,
            "ticket_id": self.ticket_id,
            "ticket_category": self.ticket_category,
            "policy_citations": list(self.policy_citations),
            "notes": list(self.notes),
        }


class TreatmentPlanner:
    """Deterministic, side-effect-free treatment planning.

    Order of decisions (first match wins) is deliberate: nothing is planned
    unless eligibility is a definite True, and no action is ever selected on
    the user's behalf.
    """

    def plan(self, *, case: Any, eligibility: EligibilitySummary) -> TreatmentPlan:
        base: dict[str, Any] = {
            "case_id": str(case.case_id),
            "case_status": eligibility.status,
            "case_type": str(case.case_type or CASE_TYPE_OTHER),
            "order_id": case.order_id,
            "order_ref": case.order_ref,
            "policy_citations": eligibility.policy_citations,
        }

        if eligibility.eligible is False:
            return TreatmentPlan(
                reason=eligibility.reason or "该订单不符合当前售后政策条件。",
                required_next_step="告知用户结论，如有疑问转人工客服",
                requires_human_review=eligibility.requires_human_review,
                executable=False,
                **base,
            )

        if eligibility.eligible is None:
            # No conclusion: information problem or uncovered policy. Never a
            # rejection, and never a self-chosen action.
            return TreatmentPlan(
                reason=eligibility.reason or "售后资格尚未得出结论，需要人工复核。",
                required_next_step=(
                    "补充权威信息后重新判定"
                    if eligibility.status == STATUS_INFORMATION_COLLECTION
                    else ESCALATE_TO_HUMAN
                ),
                requires_human_review=True,
                executable=False,
                **base,
            )

        action = str(case.requested_action or ACTION_UNKNOWN)
        if action not in EXECUTABLE_ACTIONS:
            # eligible=True but the user never said whether they want a refund,
            # an exchange or a repair: ask, never choose for them.
            return TreatmentPlan(
                reason="用户尚未明确售后诉求（退款/换货/维修），不能自动选择业务动作。",
                required_next_step=AWAIT_REQUESTED_ACTION,
                requires_human_review=True,
                executable=False,
                notes=(f"requested_action={action}",),
                **base,
            )

        return TreatmentPlan(
            action=action,
            reason=eligibility.reason,
            required_next_step=_NEXT_STEP.get(action, "等待后续售后执行"),
            requires_execution=True,
            requires_human_review=False,
            executable=True,
            **base,
        )


def build_ticket_description(
    *,
    case: Any,
    eligibility: EligibilitySummary,
    plan: TreatmentPlan,
) -> str:
    """Structured after-sales ticket description (no LLM-written free text).

    Every business claim comes from the case row or from the investigation
    result that produced the eligibility conclusion: the problem statement is
    the user's own report, the order status comes from the business system and
    the policy line comes from the retrieved citations.
    """
    order_status = eligibility.business_facts.get("order_status")
    ref = plan.order_ref or (f"ORD-{plan.order_id}" if plan.order_id is not None else "未提供")

    lines = [
        "【售后处理工单】",
        f"售后类型：{case_type_label(plan.case_type)}",
        f"申请动作：{action_label(plan.action)}",
        f"问题描述：{str(getattr(case, 'problem_description', '') or '').strip()}",
        f"订单：{ref}" + (f"（订单状态：{order_status}）" if order_status else ""),
        f"资格判断：{eligibility.reason or '符合售后条件'}",
    ]
    if plan.policy_citations:
        lines.append("政策依据：" + "；".join(plan.policy_citations))
    else:
        lines.append("政策依据：未取得可引用的政策条文")
    lines.append(f"下一步：{plan.required_next_step}")
    return "\n".join(lines)


__all__ = [
    "ACTION_EXCHANGE",
    "ACTION_REFUND",
    "ACTION_REPAIR",
    "ACTION_UNKNOWN",
    "AWAIT_REQUESTED_ACTION",
    "CASE_TYPE_LOGISTICS_DISPUTE",
    "CASE_TYPE_OTHER",
    "CASE_TYPE_QUALITY_ISSUE",
    "EXECUTABLE_ACTIONS",
    "EligibilitySummary",
    "STATUS_ELIGIBILITY_CHECK",
    "STATUS_INFORMATION_COLLECTION",
    "STATUS_PROCESSING",
    "STATUS_REJECTED",
    "TICKET_CATEGORY_AFTER_SALES",
    "TreatmentPlan",
    "TreatmentPlanner",
    "action_label",
    "build_ticket_description",
    "case_type_label",
]
