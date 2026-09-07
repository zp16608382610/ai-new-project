"""Risk policy rules (Phase 5 MVP) - the single place where risk rules live.

Rules are data, not code inside Tool Handlers:

    FAQ / knowledge QA      -> LOW      AUTO_EXECUTE
    ORDER_STATUS            -> LOW      AUTO_EXECUTE
    LOGISTICS_TRACKING      -> LOW      AUTO_EXECUTE
    Cancel order            -> MEDIUM   USER_CONFIRM
    Refund (normal)         -> HIGH     HUMAN_APPROVAL
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
