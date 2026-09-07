"""Phase 6 MCP Server unit tests (registration + discoverable schemas).

These tests inspect the MCPServer without spawning a subprocess. list_tools is
the same capability a client sees through MCP discovery, so these assertions
prove what a standard MCP client would discover.
"""
from __future__ import annotations

import asyncio

import pytest

from app.mcp.server import MCP_SERVER_NAME, create_mcp_server
from app.mcp.tools import MCP_TOOL_NAMES
import mcp_helpers as helpers

EXPECTED_TOOLS = {"get_order", "get_logistics", "create_ticket"}
HIGH_RISK_TOOLS = {"create_refund", "cancel_order", "check_refund_eligibility"}


@pytest.fixture()
def server(tmp_path):
    _, factory, engine = helpers.build_file_db(tmp_path / "server.db")
    server_obj = create_mcp_server(factory)
    yield server_obj
    engine.dispose()


def _list_tools(server):
    return asyncio.run(server.list_tools())


def _by_name(tools):
    return {tool.name: tool for tool in tools}


def test_server_initializes_with_expected_name(server):
    assert server.name == MCP_SERVER_NAME
    tools = _by_name(_list_tools(server))
    assert len(tools) >= 3


def test_list_tools_returns_exactly_the_three_exposed_tools(server):
    names = {tool.name for tool in _list_tools(server)}
    assert names == EXPECTED_TOOLS


def test_every_tool_has_a_description(server):
    for tool in _list_tools(server):
        assert tool.description and tool.description.strip()


def test_mcp_tool_schemas_expose_business_fields_only(server):
    tools = _by_name(_list_tools(server))

    get_order_props = tools["get_order"].input_schema.get("properties", {})
    assert "order_id" in tools["get_order"].input_schema.get("required", [])
    order_id_prop = get_order_props["order_id"]
    allowed_types = (
        {order_id_prop["type"]}
        if "type" in order_id_prop
        else {variant.get("type") for variant in order_id_prop.get("anyOf", [])}
    )
    assert allowed_types <= {"string", "integer"}

    logistics = tools["get_logistics"].input_schema
    assert "order_id" in logistics.get("required", [])

    ticket = tools["create_ticket"].input_schema
    ticket_required = set(ticket.get("required", []))
    assert {"reason", "description"} <= ticket_required

    # No client may ever steer money amounts through MCP.
    for tool in _list_tools(server):
        properties = tool.input_schema.get("properties", {})
        assert "refund_amount" not in properties
        assert "amount" not in properties


def test_no_high_risk_tool_is_exposed_over_mcp(server):
    names = {tool.name for tool in _list_tools(server)}
    assert names.isdisjoint(HIGH_RISK_TOOLS)
    assert "refund" not in str(sorted(names))


def test_exposed_names_constant_matches_registered_tools(server):
    names = {tool.name for tool in _list_tools(server)}
    assert names == set(MCP_TOOL_NAMES)
