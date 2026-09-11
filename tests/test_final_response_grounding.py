"""Phase 9F: the final response must reflect the workflow's own verdict.

Regression for the online bug: ORD-1003 was auto-executed (LOW / AUTO_EXECUTE)
and verified (execution COMPLETED, no approval row), yet the final response
explained the refund record's ``PENDING`` status as "waiting for human review".

The LLM stays offline here: a canned responder stands in for DeepSeek, so the
tests lock the grounding contract (evidence + terminal-verdict guard + prompt
rules) instead of a model's mood.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.agent.after_sales import (
    EXECUTION_COMPLETED,
    EXECUTION_FAILED,
    EXECUTION_NOT_EXECUTED,
    EXECUTION_PENDING_APPROVAL,
    EXECUTION_REJECTED,
    EXECUTION_VERIFICATION_FAILED,
)
from app.agent.state import (
    AgentResultStatus,
    AgentRunStatus,
    AgentState,
)
from app.db.enums import ApprovalStatus, RefundStatus
from app.db.models import ApprovalRequest, Refund
from app.db.session import create_db_engine, create_session_factory
from app.demo.demo_seed import prepare_demo_database
from app.demo.payloads import (
    _refund_status_label,
    _terminal_execution_verdict,
    build_run_payload,
)
from app.demo.service import DemoComponents
from app.llm.prompts import FINAL_RESPONSE_INSTRUCTIONS
from app.llm.respond import build_evidence_block

UTC = timezone.utc
# The demo seed anchors its timestamps to an injectable "now"; pinning it here
# keeps the after-sales window deterministic (same anchor as Phase 9C/9D/9E).
ANCHOR = datetime(2026, 9, 11, 12, tzinfo=UTC)
LOW_RISK_MESSAGE = "\u6211\u7684\u8033\u673a\u5de6\u8fb9\u574f\u4e86\uff0c\u8ba2\u5355\u662f ORD-1003\uff0c\u6211\u8981\u9000\u6b3e\u3002"
HIGH_RISK_MESSAGE = "\u6211\u7684\u8033\u673a\u5de6\u8fb9\u574f\u4e86\uff0c\u8ba2\u5355\u662f ORD-1001\uff0c\u6211\u8981\u9000\u6b3e\u3002"
MISLEADING_LLM_TEXT = "\u60a8\u7684\u9000\u6b3e\u7533\u8bf7\u5df2\u63d0\u4ea4\uff0c\u6b63\u5728\u7b49\u5f85\u4eba\u5de5\u5ba1\u6838\uff0c\u8bf7\u8010\u5fc3\u7b49\u5f85\u3002"


class _CannedResponder:
    """Stands in for DeepSeek: fixed sentence, records the evidence it received."""

    def __init__(self, text: str = MISLEADING_LLM_TEXT) -> None:
        self.text = text
        self.calls: list[dict] = []

    def respond(self, user_message, evidence, *, history=None):  # noqa: ANN001
        self.calls.append({"user_message": user_message, "evidence": evidence})
        return self.text


@pytest.fixture()
def session(tmp_path):
    url = "sqlite+pysqlite:///" + (tmp_path / "demo.db").as_posix()
    prepare_demo_database(url, now=ANCHOR)
    engine = create_db_engine(url)
    db = create_session_factory(engine)()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


def _run(session, responder, message, *, session_id="s-9f", user_id=1):
    """One real workflow run (same components the demo uses) + payload."""
    components = DemoComponents(
        session,
        llm_responder=responder,
        investigation_reference_time=ANCHOR,
    )
    state, result = components.workflow.execute(
        f"req-{session_id}", message, user_id=user_id, session_id=session_id
    )
    payload = build_run_payload(
        state, result, session_id=session_id, user_message=message
    )
    return state, result, payload


# ---------------------------------------------------------------------------
# Scenario A: low-risk auto refund (ORD-1003)
# ---------------------------------------------------------------------------


def test_low_risk_auto_refund_never_reports_human_review(session):
    responder = _CannedResponder()
    state, result, payload = _run(session, responder, LOW_RISK_MESSAGE)

    # The real chain the demo shows: LOW -> AUTO_EXECUTE -> Execute -> Verify.
    assert result.status is AgentResultStatus.SUCCESS
    assert state.run_status is AgentRunStatus.COMPLETED
    assert state.after_sales_execution["status"] == EXECUTION_COMPLETED
    assert state.after_sales_verification["passed"] is True
    assert result.approval_id is None
    assert list(session.scalars(select(ApprovalRequest))) == []

    refunds = list(session.scalars(select(Refund).where(Refund.order_id == 1003)))
    assert len(refunds) == 1
    assert refunds[0].status is RefundStatus.PENDING
    refund_id = refunds[0].id

    # The LLM really ran and really did produce the misleading sentence ...
    assert responder.calls, "the canned responder must actually be exercised"
    assert result.response == MISLEADING_LLM_TEXT

    # ... and the terminal-verdict guard keeps it away from the user.
    text = payload["text"]
    assert MISLEADING_LLM_TEXT not in text
    assert "\u4eba\u5de5\u5ba1\u6838" not in text          # 人工审核
    assert "\u9700\u8981\u4eba\u5de5" not in text          # 需要人工
    assert "\u5df2\u5b8c\u6210\u5904\u7406" in text       # 已完成处理
    assert "\u5df2\u7531\u6570\u636e\u5e93\u6743\u5a01\u72b6\u6001\u6821\u9a8c" in text  # 已由数据库权威状态校验
    assert "\u00a5199.0" in text                              # ¥199.0
    assert f"REFUND-{refund_id}" in text


def test_llm_evidence_carries_the_workflow_verdict(session):
    responder = _CannedResponder()
    _run(session, responder, LOW_RISK_MESSAGE)

    evidence = responder.calls[0]["evidence"]
    outcome = evidence["outcome"]
    assert outcome["risk_action"] == "AUTO_EXECUTE"
    assert outcome["risk_level"] == "LOW"
    assert outcome["execution_status"] == EXECUTION_COMPLETED
    assert outcome["verification_passed"] is True
    assert outcome["verification_checked"]
    assert outcome["approval_id"] is None
    assert outcome["approval_pending"] is False
    assert outcome["case_status"] == "COMPLETED"

    block = build_evidence_block(evidence)
    assert "\u7cfb\u7edf\u7ed3\u8bba" in block                     # 系统结论
    assert "\u6267\u884c\u7ed3\u679c:COMPLETED" in block           # 执行结果:COMPLETED
    assert "\u6267\u884c\u540e\u6821\u9a8c:\u901a\u8fc7" in block  # 执行后校验:通过
    assert "AUTO_EXECUTE" in block
    assert "\u4eba\u5de5\u5ba1\u6279:\u65e0" in block             # 人工审批:无
    assert "\u4e0d\u4ee3\u8868\u7b49\u5f85\u4eba\u5de5\u5ba1\u6838" in block  # 不代表等待人工审核


def test_evidence_block_marks_a_completed_human_approval():
    """A resumed (human-approved) run must not look like an automatic one."""
    block = build_evidence_block(
        {
            "outcome": {
                "risk_level": "CRITICAL",
                "risk_action": "HUMAN_APPROVAL",
                "risk_tool": "create_refund",
                "approval_id": None,
                "approval_pending": False,
                "resolved_approval_id": 9,
            }
        }
    )
    assert "\u5df2\u7531\u4eba\u5de5\u5ba1\u6279\u901a\u8fc7" in block   # 已由人工审批通过
    assert "#9" in block
    assert "\u5f85\u5ba1\u6279\u5355" not in block                          # 待审批单
    assert "\u4eba\u5de5\u5ba1\u6279:\u65e0" not in block                  # 人工审批:无


def test_evidence_block_is_empty_without_any_verdict():
    assert build_evidence_block({"outcome": {"approval_id": None, "approval_pending": False}}) == ""
    assert build_evidence_block({"outcome": {}}) == ""


def test_evidence_block_marks_a_real_pending_approval():
    block = build_evidence_block(
        {
            "outcome": {
                "risk_level": "CRITICAL",
                "risk_action": "HUMAN_APPROVAL",
                "risk_tool": "create_refund",
                "approval_id": 7,
                "approval_pending": True,
            }
        }
    )
    assert "#7" in block
    assert "\u5f85\u5ba1\u6279\u5355" in block                      # 待审批单
    assert "\u4eba\u5de5\u5ba1\u6279:\u65e0" not in block          # 人工审批:无


# ---------------------------------------------------------------------------
# Scenario B: high-risk refund still needs a human (ORD-1001)
# ---------------------------------------------------------------------------


def test_high_risk_refund_still_waits_for_human_approval(session):
    responder = _CannedResponder("\u9000\u6b3e\u5df2\u81ea\u52a8\u5b8c\u6210\u3002")
    state, result, payload = _run(session, responder, HIGH_RISK_MESSAGE)

    assert result.status is AgentResultStatus.WAITING_HUMAN_APPROVAL
    assert state.run_status is AgentRunStatus.WAITING_HUMAN_APPROVAL
    assert state.after_sales_execution["status"] == EXECUTION_PENDING_APPROVAL
    assert result.approval_id is not None

    # Nothing was written and the LLM is not allowed to answer a waiting run.
    assert list(session.scalars(select(Refund).where(Refund.order_id == 1001))) == []
    assert responder.calls == []

    approvals = list(session.scalars(select(ApprovalRequest)))
    assert len(approvals) == 1
    assert approvals[0].status is ApprovalStatus.PENDING
    assert approvals[0].risk_level == "CRITICAL"

    text = payload["text"]
    assert "\u4eba\u5de5\u5ba1\u6279" in text      # 人工审批
    assert "\u6210\u529f" not in text                # no "成功"
    assert "COMPLETED" not in text
    assert "\u5df2\u81ea\u52a8\u6267\u884c" not in text  # 已自动执行


def test_resumed_human_approved_refund_is_not_reported_as_automatic(session):
    """Approve the CRITICAL refund, then resume: still no "waiting" wording."""
    first = _CannedResponder()
    components = DemoComponents(
        session, llm_responder=first, investigation_reference_time=ANCHOR
    )
    _, waiting = components.workflow.execute(
        "req-resume-1", HIGH_RISK_MESSAGE, user_id=1, session_id="resume-9f"
    )
    assert waiting.status is AgentResultStatus.WAITING_HUMAN_APPROVAL
    approval_id = int(waiting.approval_id)

    resume_responder = _CannedResponder()
    resumed_components = DemoComponents(
        session, llm_responder=resume_responder, investigation_reference_time=ANCHOR
    )
    state, result = resumed_components.workflow.resume_after_approval(
        approval_id, approved=True, resolved_by="9f-tester"
    )
    payload = build_run_payload(
        state, result, session_id="resume-9f", user_message=HIGH_RISK_MESSAGE
    )

    assert result.status is AgentResultStatus.SUCCESS
    assert state.after_sales_execution["status"] == EXECUTION_COMPLETED
    assert state.after_sales_verification["passed"] is True
    approvals = list(session.scalars(select(ApprovalRequest)))
    assert len(approvals) == 1 and approvals[0].status is ApprovalStatus.APPROVED
    refunds = list(session.scalars(select(Refund).where(Refund.order_id == 1001)))
    assert len(refunds) == 1 and refunds[0].status is RefundStatus.PENDING

    # the LLM produced the misleading sentence again, and is again overruled
    assert resume_responder.calls
    assert result.response == MISLEADING_LLM_TEXT
    text = payload["text"]
    assert "\u4eba\u5de5\u5ba1\u6838" not in text
    assert "\u5df2\u7531\u6570\u636e\u5e93\u6743\u5a01\u72b6\u6001\u6821\u9a8c" in text

    # ... and the evidence told the model this was a human approval, not auto
    block = build_evidence_block(resume_responder.calls[0]["evidence"])
    assert "\u5df2\u7531\u4eba\u5de5\u5ba1\u6279\u901a\u8fc7" in block


# ---------------------------------------------------------------------------
# Guards + vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "status",
    [
        EXECUTION_COMPLETED,
        EXECUTION_REJECTED,
        EXECUTION_VERIFICATION_FAILED,
        EXECUTION_FAILED,
    ],
)
def test_terminal_verdicts_are_protected_from_the_llm(status):
    state = AgentState(request_id="t", user_message="")
    state.after_sales_execution = {"status": status}
    assert _terminal_execution_verdict(state) is True


@pytest.mark.parametrize(
    "status", [EXECUTION_PENDING_APPROVAL, EXECUTION_NOT_EXECUTED, None]
)
def test_non_terminal_verdicts_keep_the_llm_text(status):
    state = AgentState(request_id="t", user_message="")
    state.after_sales_execution = {"status": status} if status else None
    assert _terminal_execution_verdict(state) is False


def test_refund_pending_label_is_not_an_approval_label():
    label = _refund_status_label("PENDING")
    assert "\u8d44\u91d1" in label                 # 资金
    assert "\u5ba1\u6838" not in label              # 审核
    assert label != _refund_status_label("APPROVED")


def test_final_response_instructions_keep_safety_and_add_pending_rules():
    text = FINAL_RESPONSE_INSTRUCTIONS
    # original safety rules are still present
    assert "\u7edd\u5bf9\u4e0d\u5f97\u7f16\u9020" in text      # 绝对不得编造
    assert "\u5efa\u8bae\u8f6c\u4eba\u5de5\u5ba2\u670d" in text  # 建议转人工客服
    # the new grounding rules are present
    assert "\u4e0d\u5f97\u56de\u7b54" in text                    # 不得回答
    assert "approval_id" in text
    assert "PENDING" in text
    assert "\u4e0d\u5f97\u7531\u8be5\u5b57\u6bb5\u63a8\u65ad" in text  # 不得由该字段推断
