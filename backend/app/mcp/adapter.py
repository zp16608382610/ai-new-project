"""Agent-side MCP Tool Adapter (Phase 6 MVP).

The Agent keeps depending on one narrow tool-provider interface. The adapter
forwards the three MCP-exposed tools to the MCP Client / Server and delegates
everything else (refund / cancel / eligibility) to the existing internal Tool
Executor. Risk evaluation happens BEFORE the adapter, so MCP can never bypass
the Phase 5 Risk Gate.
"""
from __future__ import annotations

from typing import Any

from app.mcp.tools import MCP_TOOL_NAMES
from app.tools.base import ToolExecutionContext, ToolResult, ToolResultStatus


class MCPToolAdapter:
    """Tool provider: MCP tools via MCP, other tools via the internal executor."""

    def __init__(
        self,
        *,
        internal_executor,
        client,
        mcp_tool_names=MCP_TOOL_NAMES,
    ) -> None:
        self._internal = internal_executor
        self._client = client
        self._mcp_tool_names = frozenset(mcp_tool_names)
        self._discovered: dict[str, Any] | None = None

    @property
    def client(self):
        return self._client

    def execute(
        self,
        tool_name: str,
        arguments: dict | None,
        context: ToolExecutionContext,
        *,
        requires_confirmation: bool = False,
    ) -> ToolResult:
        """Execute through MCP (standardized tools) or the internal executor."""
        if tool_name not in self._mcp_tool_names:
            return self._internal.execute(
                tool_name,
                arguments,
                context,
                requires_confirmation=requires_confirmation,
            )
        return self._execute_mcp(tool_name, arguments, context)

    def _discover(self) -> dict[str, Any]:
        if self._discovered is None:
            tools = self._client.list_tools()
            self._discovered = {tool.name: tool for tool in tools}
        return self._discovered

    def _execute_mcp(
        self, tool_name: str, arguments: dict | None, context: ToolExecutionContext
    ) -> ToolResult:
        discovered = self._discover()
        if tool_name not in discovered:
            return ToolResult(
                tool_name=tool_name,
                status=ToolResultStatus.FAILED,
                error_code="MCP_TOOL_NOT_FOUND",
                error_message=(
                    f"MCP tool '{tool_name}' is not exposed by the MCP Server."
                ),
            )
        # The trusted context identity always wins over model-provided values.
        call_arguments = dict(arguments or {})
        call_arguments["user_id"] = context.user_id
        return self._client.call_tool(tool_name, call_arguments)
