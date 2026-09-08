"""Phase 7C Observability / Trace tests.

Covers the minimal trace additions on top of the existing Agent Run structure:

    - every Risk Gate decision is recorded on AgentState (tool, risk level,
      action, policy id, reason) and survives state serialization;
    - the demo payload timeline exposes structured Risk Gate steps;
    - tool / approval / execute / verify steps stay observable in payloads.
"""
import pytest

import phase5_helpers as helpers
from app.agent.state import AgentResultStatus, AgentRunStatus, AgentState
from app.demo.payloads import build_run_payload
from app.services.approval_service import ApprovalService


@pytest.fixture()
def session():
    engine, session = helpers.build_in_memory_session()
    helpers.seed_extended(session)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _workflow(session):
    return helpers.make_workflow(session)


def test_risk_gate_decisions_recorded_on_state(session):
    workflow = _workflow(session)
    state, result = workflow.execute(
        "req-cancel", "帮我取消订单 ORD-2", user_id=1, session_id="sess-c"
    )
    assert result.status is AgentResultStatus.WAITING_USER_CONFIRMATION
    assert state.risk_decisions, "expected recorded Risk Gate decisions"
    last = state.risk_decisions[-1]
    assert last["tool"] == "cancel_order"
    assert last["risk_level"] == "MEDIUM"
    assert last["risk_action"] == "USER_CONFIRM"
    assert last["policy_id"] == "P-CANCEL"


def test_risk_decisions_survive_serialization(session):
    workflow = _workflow(session)
    state, _ = workflow.execute(
        "req-s", "帮我查一下订单 ORD-1001", user_id=1, session_id="sess-s"
    )
    assert state.risk_decisions
    restored = AgentState.from_dict(state.to_dict())
    assert restored.risk_decisions == state.risk_decisions


def test_completed_tool_run_timeline_has_structured_risk_gate(session):
    workflow = _workflow(session)
    state, result = workflow.execute(
        "req-o", "帮我查一下订单 ORD-1001", user_id=1, session_id="sess-o"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert state.run_status is AgentRunStatus.COMPLETED
    payload = build_run_payload(
        state,
        result,
        session_id="sess-o",
        user_message="帮我查一下订单 ORD-1001",
    )
    labels = [step["label"] for step in payload["steps"]]
    assert "get_order" in labels
    assert "Risk Gate" in labels
    gate = next(step for step in payload["steps"] if step["label"] == "Risk Gate")
    assert gate["state"] == "success"
    assert gate["risk_level"] == "LOW"
    assert gate["risk_action"] == "AUTO_EXECUTE"


def test_refund_waiting_payload_exposes_approval_chain(session):
    workflow = _workflow(session)
    state, result = workflow.execute(
        "req-r", "帮我把 ORD-1003 退款", user_id=1, session_id="sess-r"
    )
    assert result.status is AgentResultStatus.WAITING_HUMAN_APPROVAL
    assert result.approval_id is not None
    approval = ApprovalService(session).get(int(result.approval_id))
    payload = build_run_payload(
        state,
        result,
        session_id="sess-r",
        user_message="帮我把 ORD-1003 退款",
        approval_view=approval,
    )
    labels = [step["label"] for step in payload["steps"]]
    assert "Human Approval" in labels
    assert "Execute" in labels
    assert "Verify" in labels
    assert payload["risk"]["level"] == "HIGH"
    assert payload["approval"]["risk_level"] == "HIGH"
