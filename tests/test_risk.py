"""Phase 5 Risk Engine / Policy unit tests (spec items 1-6 plus guards).

Pure decision tests: no database, no services. The risk layer stays
repository-free - it maps (operation, business context) -> RiskDecision.
"""
from decimal import Decimal

from app.risk import (
    HIGH_VALUE_REFUND_THRESHOLD,
    RiskAction,
    RiskContext,
    RiskDecision,
    RiskEngine,
    RiskLevel,
)


def _decision(operation, amount=None):
    context = RiskContext(refund_amount=amount) if amount is not None else RiskContext()
    return RiskEngine().evaluate(operation, context)


def test_faq_is_low_auto_execute():
    # Spec 1: FAQ / KNOWLEDGE_QA -> LOW / AUTO_EXECUTE
    for operation in ("FAQ", "KNOWLEDGE_QA", "REFUND_INQUIRY"):
        decision = _decision(operation)
        assert decision.risk_level is RiskLevel.LOW
        assert decision.action is RiskAction.AUTO_EXECUTE


def test_order_status_is_low_auto_execute():
    # Spec 2: ORDER_STATUS -> LOW / AUTO_EXECUTE
    for operation in ("ORDER_STATUS", "GET_ORDER"):
        decision = _decision(operation)
        assert decision.risk_level is RiskLevel.LOW
        assert decision.action is RiskAction.AUTO_EXECUTE


def test_logistics_tracking_is_low_auto_execute():
    # Spec 3: LOGISTICS_TRACKING -> LOW / AUTO_EXECUTE
    for operation in ("LOGISTICS_TRACKING", "GET_LOGISTICS"):
        decision = _decision(operation)
        assert decision.risk_level is RiskLevel.LOW
        assert decision.action is RiskAction.AUTO_EXECUTE


def test_cancel_order_is_medium_user_confirm():
    # Spec 4: CANCEL_ORDER -> MEDIUM / USER_CONFIRM
    for operation in ("CANCEL_ORDER", "CANCEL_TOOL"):
        decision = _decision(operation)
        assert decision.risk_level is RiskLevel.MEDIUM
        assert decision.action is RiskAction.USER_CONFIRM


def test_refund_execution_is_high_human_approval():
    # Spec 5: normal refund -> HIGH / HUMAN_APPROVAL (amount is authoritative)
    for operation in ("REFUND_REQUEST", "CREATE_REFUND"):
        decision = _decision(operation)
        assert decision.risk_level is RiskLevel.HIGH
        assert decision.action is RiskAction.HUMAN_APPROVAL
        assert decision.policy_id == "P-REFUND"


def test_high_value_refund_is_critical_human_approval():
    # Spec 6: high-value refund -> CRITICAL / HUMAN_APPROVAL
    decision = _decision("CREATE_REFUND", amount=Decimal("1299.00"))
    assert decision.risk_level is RiskLevel.CRITICAL
    assert decision.action is RiskAction.HUMAN_APPROVAL
    assert decision.policy_id == "P-REFUND-HIGH-VALUE"


def test_refund_below_threshold_stays_high():
    below = HIGH_VALUE_REFUND_THRESHOLD - Decimal("1")
    decision = _decision("CREATE_REFUND", amount=below)
    assert decision.risk_level is RiskLevel.HIGH
    assert decision.action is RiskAction.HUMAN_APPROVAL


def test_refund_at_threshold_is_critical():
    decision = _decision("CREATE_REFUND", amount=HIGH_VALUE_REFUND_THRESHOLD)
    assert decision.risk_level is RiskLevel.CRITICAL
    assert decision.policy_id == "P-REFUND-HIGH-VALUE"


def test_check_refund_eligibility_is_read_only_low():
    decision = _decision("CHECK_REFUND_ELIGIBILITY")
    assert decision.risk_level is RiskLevel.LOW
    assert decision.action is RiskAction.AUTO_EXECUTE


def test_unknown_operation_is_blocked():
    # Fail-closed allowlist: an unregistered operation never auto-executes.
    decision = _decision("DROP_DATABASE")
    assert decision.risk_level is RiskLevel.CRITICAL
    assert decision.action is RiskAction.BLOCK
    assert decision.policy_id == "P-UNKNOWN"
    assert "no risk policy rule" in decision.reason.lower()


def test_risk_decision_serialization_roundtrip():
    decision = RiskDecision(
        risk_level=RiskLevel.CRITICAL,
        action=RiskAction.HUMAN_APPROVAL,
        reason="high value refund",
        policy_id="P-REFUND-HIGH-VALUE",
    )
    restored = RiskDecision.from_dict(decision.to_dict())
    assert restored == decision