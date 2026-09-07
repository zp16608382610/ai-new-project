"""Phase 6 Agent <-> MCP integration tests.

Proves the Agent routes ORDER_STATUS / LOGISTICS_TRACKING / CREATE_TICKET
through MCP while REFUND / CANCEL keep using the internal Tool Executor behind
the Phase 5 Risk Gate. A RecordingClient spies on the real stdio client so
each test can assert exactly which MCP tools were called.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.agent.state import AgentResultStatus, AgentRunStatus, Intent, Route
from app.db.models import Order, Refund, Ticket, User
from app.mcp.adapter import MCPToolAdapter
from app.tools.base import ToolExecutionContext, ToolResult, ToolResultStatus
import mcp_helpers as helpers


@pytest.fixture()
def seeded(tmp_path):
    url, factory, engine = helpers.build_file_db(tmp_path / "adapter.db")
    yield url, factory
    engine.dispose()


@pytest.fixture()
def client(seeded):
    url, _ = seeded
    return helpers.make_mcp_client(url)


@pytest.fixture()
def recording(seeded, client):
    return helpers.RecordingClient(client)


@pytest.fixture()
def session(seeded):
    _, factory = seeded
    session = factory()
    yield session
    session.close()


@pytest.fixture()
def workflow(seeded, session, recording):
    url, _ = seeded
    return helpers.make_mcp_workflow(session, recording, database_url=url)


def _alice(session) -> int:
    user = session.scalar(select(User).where(User.email == "alice@example.com"))
    assert user is not None
    return user.id


# 14. Agent ORDER_STATUS -> MCP get_order


def test_agent_order_status_runs_through_mcp(workflow, session, recording):
    alice = _alice(session)
    state, result = workflow.execute(
        "req-mcp-order", "查一下订单状态 ORD-1001", user_id=alice, session_id="s1"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert state.route is Route.ORDER_TOOL
    assert recording.calls == [("get_order", {"order_id": "ORD-1001", "user_id": alice})]
    tool_result = state.tool_results[0]
    assert tool_result["tool_name"] == "get_order"
    assert tool_result["status"] == "SUCCESS"
    assert tool_result["data"]["order_id"] == 1001
    assert tool_result["data"]["status"] == "DELIVERED"


# 15. Agent LOGISTICS_TRACKING -> MCP get_logistics


def test_agent_logistics_tracking_runs_through_mcp(workflow, session, recording):
    alice = _alice(session)
    state, result = workflow.execute(
        "req-mcp-logistics", "帮我查订单 ORD-1001 到哪了", user_id=alice, session_id="s2"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert state.route is Route.LOGISTICS_TOOL
    assert recording.calls == [
        ("get_logistics", {"order_id": "ORD-1001", "user_id": alice})
    ]
    data = state.tool_results[0]["data"]
    assert data["tracking_number"] == "SF10020099"
    assert data["status"] == "IN_TRANSIT"


# 16. Agent CREATE_TICKET -> MCP create_ticket


def test_agent_create_ticket_runs_through_mcp(workflow, session, recording):
    alice = _alice(session)
    message = "我要投诉物流异常"
    state, result = workflow.execute(
        "req-mcp-ticket", message, user_id=alice, session_id="s3"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert state.route is Route.TICKET_TOOL
    assert recording.calls == [
        (
            "create_ticket",
            {
                "reason": "COMPLAINT",
                "description": message,
                "user_id": alice,
            },
        )
    ]
    tool_result = state.tool_results[0]
    assert tool_result["tool_name"] == "create_ticket"
    assert tool_result["data"]["category"] == "COMPLAINT"
    row = session.scalar(select(Ticket).where(Ticket.description == message))
    assert row is not None
    assert row.user_id == alice


# 17. REFUND never routes through MCP and cannot bypass the Risk Gate


def test_agent_refund_never_uses_mcp_and_still_waits_for_human_approval(
    workflow, session, recording
):
    alice = _alice(session)
    state, result = workflow.execute(
        "req-mcp-refund", "帮我把 ORD-1001 退款", user_id=alice, session_id="s4"
    )
    assert recording.calls == []
    assert result.status is AgentResultStatus.WAITING_HUMAN_APPROVAL
    assert result.approval_id is not None
    assert [r.tool_name for r in state.tool_requests] == ["check_refund_eligibility", "create_refund"]

    # Human approves -> the ORIGINAL refund tool resumes on the internal executor.
    resumed_state, resumed = workflow.resume_after_approval(result.approval_id, approved=True)
    assert resumed.status is AgentResultStatus.SUCCESS
    assert recording.calls == []
    rows = session.scalars(select(Refund).where(Refund.order_id == 1001))
    assert len(list(rows)) == 1


def test_mcp_discovery_does_not_expose_refund_or_cancel(recording):
    names = {tool.name for tool in recording.list_tools()}
    assert "create_refund" not in names
    assert "cancel_order" not in names
    assert "get_order" in names


# 18. MCP Server tools go through the Service layer, never direct DB access.
# (Boundary is structural: server.py has no SQLAlchemy model usage; the
# client test asserts ticket persistence created through TicketService.)


# CANCEL keeps USER_CONFIRM + internal executor (MEDIUM risk rule).


def test_agent_cancel_requires_user_confirmation_and_stays_internal(
    workflow, session, recording
):
    alice = _alice(session)
    state, result = workflow.execute(
        "req-mcp-cancel", "帮我取消订单 ORD-1002", user_id=alice, session_id="s5"
    )
    assert recording.calls == []
    assert result.status is AgentResultStatus.WAITING_USER_CONFIRMATION
    assert session.get(Order, 1002).status.value == "PAID"

    _, confirmed = workflow.execute(
        "req-mcp-cancel", "帮我取消订单 ORD-1002", user_id=alice, session_id="s5",
        user_confirmed=True,
    )
    assert confirmed.status is AgentResultStatus.SUCCESS
    assert recording.calls == []
    assert session.get(Order, 1002).status.value == "CANCELLED"


# Adapter unit behaviour: delegation + discovery guard (no subprocess needed).


class _SpyInternalExecutor:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, int | None]] = []

    def execute(self, tool_name, arguments, context, *, requires_confirmation=False):
        self.calls.append((tool_name, dict(arguments or {}), context.user_id))
        return ToolResult(tool_name=tool_name, status=ToolResultStatus.SUCCESS)


class _EmptyClient:
    def list_tools(self):
        return []

    def call_tool(self, name, arguments):
        raise AssertionError("call_tool must not run when the tool is not discovered")


def test_adapter_delegates_non_mcp_tools_to_internal_executor():
    internal = _SpyInternalExecutor()
    adapter = MCPToolAdapter(internal_executor=internal, client=_EmptyClient())
    context = ToolExecutionContext(request_id="r1", user_id=7)
    result = adapter.execute("cancel_order", {"order_id": 1002}, context)
    assert result.status is ToolResultStatus.SUCCESS
    assert internal.calls == [("cancel_order", {"order_id": 1002}, 7)]


def test_adapter_undiscovered_mcp_tool_fails_without_calling_client():
    internal = _SpyInternalExecutor()
    adapter = MCPToolAdapter(internal_executor=internal, client=_EmptyClient())
    context = ToolExecutionContext(request_id="r2", user_id=7)
    result = adapter.execute("get_order", {"order_id": 1001}, context)
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "MCP_TOOL_NOT_FOUND"
    assert internal.calls == []
