"""Phase 6 MCP Client tests over REAL stdio transport.

Every client test spawns the local MCP Server (python -m app.mcp.server)
against a freshly seeded SQLite file, connects through the official SDK and
asserts that results / errors come back as internal ToolResults.
"""
from __future__ import annotations

import pytest
from sqlalchemy import select

from app.db.models import Ticket, User
from app.mcp.client import MCPToolInfo, map_result_to_tool_result
from app.tools.base import ToolResult, ToolResultStatus
import mcp_helpers as helpers


@pytest.fixture()
def seeded(tmp_path):
    url, factory, engine = helpers.build_file_db(tmp_path / "mcp.db")
    yield url, factory
    engine.dispose()


@pytest.fixture()
def client(seeded):
    url, _ = seeded
    return helpers.make_mcp_client(url)


def _user_id(factory, email="alice@example.com") -> int:
    session = factory()
    try:
        user = session.scalar(select(User).where(User.email == email))
        assert user is not None
        return user.id
    finally:
        session.close()


def test_client_list_tools_discovers_three_tools(client):
    tools = client.list_tools()
    assert isinstance(tools, list)
    assert all(isinstance(tool, MCPToolInfo) for tool in tools)
    names = {tool.name for tool in tools}
    assert names == {"get_order", "get_logistics", "create_ticket"}
    for tool in tools:
        assert tool.description
        assert isinstance(tool.input_schema, dict)


def test_client_call_tool_returns_internal_tool_result(client, seeded):
    url, factory = seeded
    result = client.call_tool(
        "get_order", {"order_id": "ORD-1001", "user_id": _user_id(factory)}
    )
    assert isinstance(result, ToolResult)
    assert result.status is ToolResultStatus.SUCCESS
    assert result.tool_name == "get_order"
    assert result.success is True
    assert result.data["order_id"] == 1001
    assert result.data["status"] == "DELIVERED"


def test_client_call_tool_get_logistics(client, seeded):
    url, factory = seeded
    result = client.call_tool(
        "get_logistics", {"order_id": "ORD-1001", "user_id": _user_id(factory)}
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["tracking_number"] == "SF10020099"
    assert result.data["status"] == "IN_TRANSIT"


def test_client_call_tool_create_ticket_persists_through_service(client, seeded):
    url, factory = seeded
    alice = _user_id(factory)
    description = "mcp create ticket e2e"
    result = client.call_tool(
        "create_ticket",
        {
            "user_id": alice,
            "order_id": "ORD-1002",
            "reason": "COMPLAINT",
            "description": description,
        },
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["category"] == "COMPLAINT"
    assert result.data["order_id"] == 1002

    session = factory()
    try:
        row = session.scalar(select(Ticket).where(Ticket.description == description))
        assert row is not None
        assert row.user_id == alice
        assert row.order_id == 1002
        assert row.category == "COMPLAINT"
    finally:
        session.close()


def test_client_unknown_tool_maps_to_internal_error(client, seeded):
    url, factory = seeded
    result = client.call_tool(
        "create_refund", {"order_id": "ORD-1001", "user_id": _user_id(factory)}
    )
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "MCP_TOOL_NOT_FOUND"
    assert "unknown tool" in (result.error_message or "").lower()


def test_client_invalid_order_reference_maps_to_validation_error(client, seeded):
    url, factory = seeded
    result = client.call_tool(
        "get_order", {"order_id": "not-an-order", "user_id": _user_id(factory)}
    )
    assert result.status is ToolResultStatus.VALIDATION_ERROR
    assert result.error_code == "INVALID_ARGUMENTS"


def test_client_service_not_found_maps_to_not_found_tool_result(client, seeded):
    url, factory = seeded
    result = client.call_tool(
        "get_order", {"order_id": "ORD-99999", "user_id": _user_id(factory)}
    )
    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.error_code == "ORDER_NOT_FOUND"


def test_client_cross_user_order_access_is_rejected(client, seeded):
    url, factory = seeded
    alice = _user_id(factory, email="alice@example.com")
    result = client.call_tool(
        "get_order", {"order_id": "ORD-2001", "user_id": alice}
    )
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "UNAUTHORIZED_ORDER_ACCESS"


def test_malformed_payload_maps_to_malformed_result():
    result = map_result_to_tool_result(
        "get_order", is_error=False, text="this is not json"
    )
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "MCP_MALFORMED_RESULT"


def test_error_envelope_maps_to_domain_status():
    import json

    payload = json.dumps(
        {
            "ok": False,
            "status": "NOT_FOUND",
            "code": "ORDER_NOT_FOUND",
            "message": "Order not found",
        }
    )
    result = map_result_to_tool_result("get_order", is_error=False, text=payload)
    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.error_code == "ORDER_NOT_FOUND"
