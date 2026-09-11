"""Risk policy rules (Phase 5 MVP) - the single place where risk rules live.

Rules are data, not code inside Tool Handlers:

    FAQ / knowledge QA      -> LOW      AUTO_EXECUTE
    ORDER_STATUS            -> LOW      AUTO_EXECUTE
    LOGISTICS_TRACKING      -> LOW      AUTO_EXECUTE
    Cancel order            -> MEDIUM   USER_CONFIRM
    Refund (standard, already verified low risk) -> LOW AUTO_EXECUTE
    Refund (anything else)  -> HIGH     HUMAN_APPROVAL
    Refund (high value)     -> CRITICAL HUMAN_APPROVAL

high_value_refund_threshold is policy/config data (500.00 in the demo).
Amounts are only read from RiskContext.refund_amount, which the Workflow fills
from the authoritative eligibility ToolResult / Service - never from the user.
"""
from __future__ import annotations

from decimal import Decimal

from app.risk.types import RiskAction, RiskContext, RiskDecision, RiskLevel

# Demo threshold: refunds at or above this amount are CRITICAL (still Human
# Approval). Keeping it here proves the policy is not hard-coded per handler.
HIGH_VALUE_REFUND_THRESHOLD = Decimal("500")

# Phase 9F: the policy id of the ONLY automatic refund path. Kept next to the
# threshold so the whole "what may run without a human" decision lives in one
# place and is visible in traces / Evaluation.
LOW_RISK_REFUND_POLICY_ID = "P-REFUND-LOW-RISK-AUTO"

# Business vocabulary mirrored on purpose: the risk layer must stay free of
# app.db / app.after_sales imports (see app/risk/types.py boundary rules).
_ACTION_REFUND = "REFUND"
_CASE_TYPE_QUALITY_ISSUE = "QUALITY_ISSUE"
_ORDER_STATUS_DELIVERED = "DELIVERED"


# Base rule per operation. Keys accept both user-facing intent/route labels and
# the concrete tool names so the same table drives FAQ / QA classification and
# ToolRequest gating.
_BASE_RULES: dict[str, tuple[str, RiskLevel, RiskAction]] = {
    # FAQ / knowledge Q&A - no write tool executes; answer may be auto-produced.
    "FAQ": ("P-FAQ", RiskLevel.LOW, RiskAction.AUTO_EXECUTE),
    "KNOWLEDGE_QA": ("P-FAQ", RiskLevel.LOW, RiskAction.AUTO_EXECUTE),
    "REFUND_INQUIRY": ("P-FAQ", RiskLevel.LOW, RiskAction.AUTO_EXECUTE),
    # Read-only order / logistics lookups.
    "ORDER_STATUS": ("P-ORDER", RiskLevel.LOW, RiskAction.AUTO_EXECUTE),
    "GET_ORDER": ("P-ORDER", RiskLevel.LOW, RiskAction.AUTO_EXECUTE),
    "LOGISTICS_TRACKING": ("P-LOGISTICS", RiskLevel.LOW, RiskAction.AUTO_EXECUTE),
    "GET_LOGISTICS": ("P-LOGISTICS", RiskLevel.LOW, RiskAction.AUTO_EXECUTE),
    # Read-only refund eligibility check.
    "CHECK_REFUND_ELIGIBILITY": (
        "P-REFUND-ELIGIBILITY",
        RiskLevel.LOW,
        RiskAction.AUTO_EXECUTE,
    ),
    # Refund execution: HUMAN_APPROVAL by default, CRITICAL when high value.
    "REFUND_REQUEST": ("P-REFUND", RiskLevel.HIGH, RiskAction.HUMAN_APPROVAL),
    "CREATE_REFUND": ("P-REFUND", RiskLevel.HIGH, RiskAction.HUMAN_APPROVAL),
    # Cancellation: the user must confirm before any write.
    "CANCEL_ORDER": ("P-CANCEL", RiskLevel.MEDIUM, RiskAction.USER_CONFIRM),
    "CANCEL_TOOL": ("P-CANCEL", RiskLevel.MEDIUM, RiskAction.USER_CONFIRM),
    # Ticket creation is non-monetary and traceable: auto in the MVP.
    "CREATE_TICKET": ("P-TICKET", RiskLevel.LOW, RiskAction.AUTO_EXECUTE),
}


class RiskPolicy:
    """Deterministic MVP risk policy. Pure rule data + decision logic."""

    name = "phase5_mvp"
    high_value_refund_threshold: Decimal = HIGH_VALUE_REFUND_THRESHOLD

    def decide(self, operation: str, context: RiskContext | None = None) -> RiskDecision:
        """Map one operation (+ business context) to a RiskDecision."""
        key = (operation or "").strip().upper()
        rule = _BASE_RULES.get(key)
        if rule is None:
            # Allowlist behaviour: operations without an explicit rule are
            # blocked instead of silently auto-executed.
            return RiskDecision(
                risk_level=RiskLevel.CRITICAL,
                action=RiskAction.BLOCK,
                reason=f"No risk policy rule registered for operation '{key}'.",
                policy_id="P-UNKNOWN",
            )

        policy_id, level, action = rule
        amount = context.refund_amount if context is not None else None
        if key in ("CREATE_REFUND", "REFUND_REQUEST"):
            if amount is not None and amount >= self.high_value_refund_threshold:
                return RiskDecision(
                    risk_level=RiskLevel.CRITICAL,
                    action=RiskAction.HUMAN_APPROVAL,
                    reason=(
                        "Refund amount meets the high-value threshold; requires "
                        "human approval before execution."
                    ),
                    policy_id="P-REFUND-HIGH-VALUE",
                )
            # Phase 9F: the ONLY automatic refund path. Every condition is an
            # already-verified business fact; anything unknown or exceptional
            # falls through to HUMAN_APPROVAL below.
            automatic = self._standard_low_risk_refund(context)
            if automatic is not None:
                return automatic
            return RiskDecision(
                risk_level=RiskLevel.HIGH,
                action=RiskAction.HUMAN_APPROVAL,
                reason=(
                    "Refund execution can cause real financial loss; requires "
                    "human approval before execution."
                ),
                policy_id=policy_id,
            )
        return RiskDecision(
            risk_level=level,
            action=action,
            reason=f"Policy '{policy_id}': operation '{key}'.",
            policy_id=policy_id,
        )

    def _standard_low_risk_refund(
        self, context: RiskContext | None
    ) -> RiskDecision | None:
        """The single, explicit low-risk auto-execute condition for refunds.

        Allowed only when EVERY one of these already-verified business facts
        holds (any missing fact means "cannot prove low risk" -> None):

            * the deterministic eligibility engine concluded eligible=True
              (Eligibility is therefore never bypassed);
            * the case asked for REFUND and is a standard quality-issue case;
            * the order is DELIVERED and belongs to a known order id;
            * there is no active refund on the order (idempotency guard);
            * the items are proven returnable (unknown can never be low risk);
            * the authoritative amount exists and is below the demo threshold.

        The amount always comes from RiskContext.refund_amount, which the
        workflow fills from the RefundService / eligibility ToolResult - never
        from the user or the model. Everything else keeps HUMAN_APPROVAL.

        days_since_delivery is carried for observability only: the after-sales
        window is decided once, by the deterministic Eligibility Engine, and is
        not re-derived here (docs/DECISIONS.md Decision 049).
        """
        if context is None:
            return None
        if context.eligibility_passed is not True:
            return None
        if context.requested_action != _ACTION_REFUND:
            return None
        if context.case_type != _CASE_TYPE_QUALITY_ISSUE:
            return None
        if (context.order_status or "").upper() != _ORDER_STATUS_DELIVERED:
            return None
        if context.active_refund_count != 0:
            return None
        if context.items_returnable is not True:
            return None
        if context.order_id is None:
            return None
        amount = context.refund_amount
        if amount is None or amount >= self.high_value_refund_threshold:
            return None
        return RiskDecision(
            risk_level=RiskLevel.LOW,
            action=RiskAction.AUTO_EXECUTE,
            reason=(
                "Standard quality-issue refund within the low-risk limit "
                f"(amount {amount} < {self.high_value_refund_threshold}): the "
                "order is delivered and returnable, has no active refund, and "
                "the deterministic eligibility check passed. Auto-executed and "
                "then verified against the business system."
            ),
            policy_id=LOW_RISK_REFUND_POLICY_ID,
        )
