"""Phase 5 Risk Gate + User Confirmation workflow tests.

Every scenario runs the real chain with the injected Risk Engine:

    ToolRequest -> RiskEngine -> Risk Gate -> ToolExecutor -> Verify

Covers the spec list: LOW auto execution, MEDIUM waits for user
confirmation, confirmed executes, rejected never executes, and HIGH without an
approval gateway fails closed.
"""
import pytest

import phase5_helpers as helpers
from app.agent.state import AgentResultStatus, AgentRunStatus, ToolRequestStatus
from app.db.enums import OrderStatus

REFUND_1001 = "帮我把 ORD-1001 退款"
CANCEL_1002 = "帮我取消订单 ORD-1002"


@pytest.fixture()
def session():
    engine, session = helpers.build_in_memory_session()
    helpers.seed_extended(session)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def test_order_lookup_auto_executes_under_risk_gate(session):
    # LOW / AUTO_EXECUTE: the read tool runs without any confirmation.
    workflow = helpers.make_workflow(session, verifier=False)
    state, result = workflow.execute(
        "req-1", "帮我查一下 ORD-1001 的订单状态", user_id=1, session_id="s-1"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert state.run_status is AgentRunStatus.COMPLETED
    assert state.tool_results[0]["tool_name"] == "get_order"
    assert state.tool_results[0]["status"] == "SUCCESS"
    assert state.tool_results[0]["data"]["status"] == "DELIVERED"
    # read-only: the order row is untouched
    assert helpers.order_of(session, 1001).status is OrderStatus.DELIVERED


def test_cancel_without_confirmation_never_executes(session):
    # Spec 7: cancel without confirmed=True must NOT execute.
    workflow = helpers.make_workflow(session)
    state, result = workflow.execute(
        "req-2", CANCEL_1002, user_id=1, session_id="s-2"
    )
    assert result.status is AgentResultStatus.WAITING_USER_CONFIRMATION
    assert state.run_status is AgentRunStatus.WAITING_USER_CONFIRMATION
    assert state.tool_requests[0].tool_name == "cancel_order"
    assert state.tool_requests[0].status is ToolRequestStatus.PENDING
    assert result.tool_requests[0].status is ToolRequestStatus.PENDING
    assert result.confirmation_message is not None
    assert "ORD-1002" in result.confirmation_message
    assert "确认" in result.confirmation_message
    assert helpers.order_of(session, 1002).status is OrderStatus.PAID
    assert helpers.order_of(session, 1002).status is not OrderStatus.CANCELLED


def test_cancel_confirmed_executes_and_verifies(session):
    # Spec 8: confirmed=True -> execute -> verify order.status == CANCELLED.
    workflow = helpers.make_workflow(session)
    state, result = workflow.execute(
        "req-3", CANCEL_1002, user_id=1, session_id="s-3", user_confirmed=True
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert state.run_status is AgentRunStatus.COMPLETED
    assert helpers.order_of(session, 1002).status is OrderStatus.CANCELLED
    assert state.tool_results[0]["tool_name"] == "cancel_order"
    assert state.tool_results[0]["data"]["status"] == "CANCELLED"


def test_cancel_rejected_never_executes(session):
    # Spec 9: rejected confirmation ends the run with REJECTED, no execution.
    workflow = helpers.make_workflow(session)
    state, result = workflow.execute(
        "req-4", CANCEL_1002, user_id=1, session_id="s-4", user_confirmed=False
    )
    assert result.status is AgentResultStatus.REJECTED
    assert state.run_status is AgentRunStatus.REJECTED
    assert state.tool_results == ()
    assert state.tool_requests[0].status is ToolRequestStatus.PENDING
    assert helpers.order_of(session, 1002).status is OrderStatus.PAID


def test_refund_without_approval_gateway_fails_closed(session):
    # HIGH requires HUMAN_APPROVAL; without a gateway nothing executes.
    workflow = helpers.make_workflow(session, approval_gateway=False)
    state, result = workflow.execute(
        "req-5", REFUND_1001, user_id=1, session_id="s-5"
    )
    assert result.status is AgentResultStatus.ERROR
    assert state.run_status is AgentRunStatus.FAILED
    assert "approval gateway" in result.error
    assert helpers.refunds_of(session, 1001) == []
    # only the read-only eligibility check ran, never a refund write
    assert state.tool_results[-1]["tool_name"] == "check_refund_eligibility"


def test_refund_request_creates_pending_approval_bound_to_snapshot(session):
    # Spec 10: refund -> PENDING approval persists with the original ToolRequest.
    workflow = helpers.make_workflow(session, verifier=False)
    state, result = workflow.execute(
        "req-6", REFUND_1001, user_id=1, session_id="s-6"
    )
    assert result.status is AgentResultStatus.WAITING_HUMAN_APPROVAL
    assert state.run_status is AgentRunStatus.WAITING_HUMAN_APPROVAL
    assert result.approval_id is not None
    assert helpers.refunds_of(session, 1001) == []

    approvals = helpers.approvals_of(session)
    assert len(approvals) == 1
    row = approvals[0]
    assert row.status.value == "PENDING"
    assert row.tool_name == "create_refund"
    assert row.tool_arguments == {"order_id": "ORD-1001"}  # original snapshot
    assert row.risk_level in ("HIGH", "CRITICAL")
    assert row.user_id == 1
    assert result.confirmation_message is None