"""Phase 5 Human Approval -> Resume -> Verify + business safety tests.

Real full stack (in-memory SQLite):

    Agent -> ToolRequest -> RiskEngine -> Risk Gate -> Approval
    approve -> resume ORIGINAL snapshot -> ToolExecutor -> Service -> DB -> Verify

Covers spec items 10-22: approval lifecycle, binding, verification and
business-safety invariants (amount not user-controlled, duplicate / cancelled
refund still refused by the Service layer).
"""
from decimal import Decimal

import pytest

import phase5_helpers as helpers
from app.agent.state import AgentResultStatus, AgentRunStatus
from app.agent.workflow import AgentWorkflow
from app.db.enums import OrderStatus, RefundStatus
from app.services.approval_service import ApprovalService
from app.services.verification import BusinessVerifier
from app.tools.base import ToolExecutionContext, ToolResult, ToolResultStatus
from app.tools.executor import ToolExecutor
from app.tools.handlers import build_default_registry

REFUND_1001 = "帮我把 ORD-1001 退款"
REFUND_1003 = "帮我把 ORD-1003 退款"


@pytest.fixture()
def session():
    engine, session = helpers.build_in_memory_session()
    helpers.seed_extended(session)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


class FakeSuccessExecutor:
    """Executor that reports SUCCESS without writing anything to the DB."""

    def __init__(self, data):
        self.data = data

    def execute(self, tool_name, arguments, context, *, requires_confirmation=False):
        return ToolResult(
            tool_name=tool_name,
            status=ToolResultStatus.SUCCESS,
            data=dict(self.data),
        )


def _waiting_approval(workflow, message, request_id):
    state, result = workflow.execute(
        request_id, message, user_id=1, session_id=f"sess-{request_id}"
    )
    assert result.status is AgentResultStatus.WAITING_HUMAN_APPROVAL
    assert result.approval_id is not None
    return result.approval_id


# ---------------------------------------------------------------- approval


def test_approval_created_as_pending_without_execution(session):
    workflow = helpers.make_workflow(session)
    approval_id = _waiting_approval(workflow, REFUND_1001, "req-a")
    row = helpers.approvals_of(session)[0]
    assert row.id == approval_id
    assert row.status.value == "PENDING"
    assert helpers.refunds_of(session, 1001) == []


def test_approval_binds_original_tool_request_snapshot(session):
    # Spec 15: the PENDING approval stores tool_name + original arguments.
    workflow = helpers.make_workflow(session)
    _waiting_approval(workflow, REFUND_1001, "req-bind")
    row = helpers.approvals_of(session)[0]
    assert row.tool_name == "create_refund"
    assert row.tool_arguments == {"order_id": "ORD-1001"}
    assert row.risk_level == "CRITICAL"  # 1299.00 >= threshold 500
    assert row.user_id == 1
    assert row.reason and "approval" in row.reason.lower() or row.reason is not None


def test_approve_resumes_original_request_executes_and_verifies(session):
    # Spec 11 + 17: approve -> resume snapshot -> execute -> refund verified.
    workflow = helpers.make_workflow(session)
    approval_id = _waiting_approval(workflow, REFUND_1001, "req-ok")
    state, result = workflow.resume_after_approval(approval_id, approved=True)

    assert result.status is AgentResultStatus.SUCCESS
    assert state.run_status is AgentRunStatus.COMPLETED
    rows = helpers.refunds_of(session, 1001)
    assert len(rows) == 1
    assert rows[0].amount == Decimal("1299.00")
    assert rows[0].status is RefundStatus.PENDING
    assert helpers.approvals_of(session)[0].status.value == "APPROVED"


def test_reject_never_executes(session):
    # Spec 12: reject -> REJECTED and nothing runs.
    workflow = helpers.make_workflow(session)
    approval_id = _waiting_approval(workflow, REFUND_1001, "req-no")
    state, result = workflow.resume_after_approval(approval_id, approved=False)

    assert result.status is AgentResultStatus.REJECTED
    assert state.run_status is AgentRunStatus.REJECTED
    assert helpers.refunds_of(session, 1001) == []
    assert helpers.approvals_of(session)[0].status.value == "REJECTED"


def test_resume_uses_each_approval_own_snapshot(session):
    # Spec 16: resume never re-plans; each approval executes its stored order.
    workflow = helpers.make_workflow(session)
    id_high = _waiting_approval(workflow, REFUND_1001, "req-h")
    id_normal = _waiting_approval(workflow, REFUND_1003, "req-n")
    rows = helpers.approvals_of(session)
    assert [r.tool_arguments for r in rows] == [
        {"order_id": "ORD-1001"},
        {"order_id": "ORD-1003"},
    ]
    assert rows[1].risk_level == "HIGH"  # 199.00 < threshold 500

    _, high_result = workflow.resume_after_approval(id_high, approved=True)
    assert high_result.status is AgentResultStatus.SUCCESS
    _, normal_result = workflow.resume_after_approval(id_normal, approved=True)
    assert normal_result.status is AgentResultStatus.SUCCESS

    refund_1001 = helpers.refunds_of(session, 1001)
    refund_1003 = helpers.refunds_of(session, 1003)
    assert len(refund_1001) == 1 and refund_1001[0].amount == Decimal("1299.00")
    assert len(refund_1003) == 1 and refund_1003[0].amount == Decimal("199.00")
    assert helpers.refunds_of(session, 1002) == []


def test_approve_twice_second_resume_is_rejected(session):
    # Spec 13: APPROVED cannot be approved again.
    workflow = helpers.make_workflow(session)
    approval_id = _waiting_approval(workflow, REFUND_1001, "req-dup")
    _, first = workflow.resume_after_approval(approval_id, approved=True)
    assert first.status is AgentResultStatus.SUCCESS
    state, second = workflow.resume_after_approval(approval_id, approved=True)
    assert second.status is AgentResultStatus.ERROR
    assert state.run_status is AgentRunStatus.FAILED
    assert "already been resolved" in second.error
    assert len(helpers.refunds_of(session, 1001)) == 1


def test_resume_after_reject_is_rejected(session):
    # Spec 14: a REJECTED approval cannot later be approved.
    workflow = helpers.make_workflow(session)
    approval_id = _waiting_approval(workflow, REFUND_1001, "req-rj")
    _, rejected = workflow.resume_after_approval(approval_id, approved=False)
    assert rejected.status is AgentResultStatus.REJECTED
    state, later = workflow.resume_after_approval(approval_id, approved=True)
    assert later.status is AgentResultStatus.ERROR
    assert state.run_status is AgentRunStatus.FAILED
    assert helpers.refunds_of(session, 1001) == []


def test_resume_does_not_bypass_duplicate_refund_rule(session):
    # Two pending approvals for the same order: the second resume hits the
    # Service DUPLICATE_REFUND rule instead of creating a second refund.
    workflow = helpers.make_workflow(session)
    first = _waiting_approval(workflow, REFUND_1001, "req-d1")
    second = _waiting_approval(workflow, REFUND_1001, "req-d2")
    assert first != second

    _, ok = workflow.resume_after_approval(first, approved=True)
    assert ok.status is AgentResultStatus.SUCCESS
    state, dup = workflow.resume_after_approval(second, approved=True)
    assert dup.status is AgentResultStatus.ERROR
    assert state.run_status is AgentRunStatus.FAILED
    assert state.tool_results[-1]["error_code"] == "DUPLICATE_REFUND"
    assert len(helpers.refunds_of(session, 1001)) == 1


# ------------------------------------------------------------- verification


def test_cancel_verified_against_database(session):
    # Spec 18: cancel -> verify order.status == CANCELLED.
    workflow = helpers.make_workflow(session)
    state, result = workflow.execute(
        "req-cancel", "帮我取消订单 ORD-1002", user_id=1, session_id="sess-c",
        user_confirmed=True,
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert state.run_status is AgentRunStatus.COMPLETED
    assert helpers.order_of(session, 1002).status is OrderStatus.CANCELLED


def test_refund_verification_failed_when_record_missing(session):
    # Spec 19: tool says SUCCESS but no refund row exists -> VERIFICATION_FAILED.
    approval = ApprovalService(session).create(
        request_id="req-vf",
        tool_name="create_refund",
        tool_arguments={"order_id": "ORD-1001"},
        risk_level="CRITICAL",
        reason="verification probe",
        user_id=1,
    )
    fake = AgentWorkflow(
        tool_executor=FakeSuccessExecutor(
            {
                "id": 999999,
                "order_id": 1001,
                "amount": 1299.0,
                "status": "PENDING",
                "reason": None,
                "created_at": None,
            }
        ),
        approval_gateway=ApprovalService(session),
        verifier=BusinessVerifier(session),
    )
    state, result = fake.resume_after_approval(approval.id, approved=True)
    assert result.status is AgentResultStatus.VERIFICATION_FAILED
    assert state.run_status is AgentRunStatus.VERIFICATION_FAILED
    assert "missing" in result.error.lower()
    assert helpers.refunds_of(session, 1001) == []


def test_cancel_verification_failed_when_state_unchanged(session):
    # Spec 19 (cancel): tool claims CANCELLED but the DB row is still PAID.
    approval = ApprovalService(session).create(
        request_id="req-vfc",
        tool_name="cancel_order",
        tool_arguments={"order_id": "ORD-1002"},
        risk_level="MEDIUM",
        reason="verification probe",
        user_id=1,
    )
    fake = AgentWorkflow(
        tool_executor=FakeSuccessExecutor(
            {
                "order_id": 1002,
                "previous_status": "PAID",
                "status": "CANCELLED",
                "message": "cancelled",
            }
        ),
        approval_gateway=ApprovalService(session),
        verifier=BusinessVerifier(session),
    )
    state, result = fake.resume_after_approval(approval.id, approved=True)
    assert result.status is AgentResultStatus.VERIFICATION_FAILED
    assert state.run_status is AgentRunStatus.VERIFICATION_FAILED
    assert helpers.order_of(session, 1002).status is OrderStatus.PAID


# ---------------------------------------------------------- business safety


def test_refund_amount_cannot_be_user_controlled(session):
    # Spec 20: an injected amount argument is ignored; the Service uses the
    # authoritative order total (1299.00), never 0.01.
    ctx = ToolExecutionContext(request_id="req-am", session_id="s", user_id=1)
    executor = ToolExecutor(build_default_registry(session))
    result = executor.execute(
        "create_refund",
        {"order_id": "ORD-1001", "amount": 0.01, "user_id": 99},
        ctx,
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["amount"] == 1299.0
    assert result.data["order_id"] == 1001
    assert helpers.refunds_of(session, 1001)[0].amount == Decimal("1299.00")


def test_cancelled_order_cannot_be_refunded(session):
    # Spec 22: refunding an already cancelled order is refused by the Service.
    ctx = ToolExecutionContext(request_id="req-cc", session_id="s", user_id=1)
    executor = ToolExecutor(build_default_registry(session))
    cancelled = executor.execute("cancel_order", {"order_id": "ORD-1002"}, ctx)
    assert cancelled.status is ToolResultStatus.SUCCESS
    assert helpers.order_of(session, 1002).status is OrderStatus.CANCELLED

    eligibility = executor.execute(
        "check_refund_eligibility", {"order_id": "ORD-1002"}, ctx
    )
    assert eligibility.data["eligible"] is False

    refused = executor.execute("create_refund", {"order_id": "ORD-1002"}, ctx)
    assert refused.status is ToolResultStatus.BUSINESS_ERROR
    assert refused.error_code == "REFUND_NOT_ELIGIBLE"
    assert helpers.refunds_of(session, 1002) == []


def test_duplicate_refund_still_rejected_by_service(session):
    # Spec 21: a second refund for the same order is a ConflictError, even
    # when it reaches the Service directly.
    ctx = ToolExecutionContext(request_id="req-dr", session_id="s", user_id=1)
    executor = ToolExecutor(build_default_registry(session))
    first = executor.execute("create_refund", {"order_id": "ORD-1001"}, ctx)
    assert first.status is ToolResultStatus.SUCCESS
    second = executor.execute("create_refund", {"order_id": "ORD-1001"}, ctx)
    assert second.status is ToolResultStatus.BUSINESS_ERROR
    assert second.error_code == "DUPLICATE_REFUND"
    assert len(helpers.refunds_of(session, 1001)) == 1