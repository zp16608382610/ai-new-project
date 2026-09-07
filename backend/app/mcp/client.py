"""Minimal MCP Client (Phase 6 MVP).

The Agent never depends on the MCP SDK directly. MCPClient is a small stdio
adapter that:

    1. spawns the local MCP Server over stdio
    2. lists the discovered tools (name / description / input schema)
    3. calls one tool
    4. normalizes the result into the internal ToolResult vocabulary

MCP errors (unknown tool, invalid arguments, server error, malformed result)
are converted here into internal ToolResults - raw MCP SDK exceptions never
reach the Agent layer.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator

from app.tools.base import ToolResult, ToolResultStatus

logger = logging.getLogger("app.mcp.client")

# Stable MCP-layer error codes (kept separate from internal tool codes so an
# observability consumer can tell MCP transport problems apart).
MCP_ERROR_TOOL_NOT_FOUND = "MCP_TOOL_NOT_FOUND"
MCP_ERROR_INVALID_ARGUMENTS = "MCP_INVALID_ARGUMENTS"
MCP_ERROR_SERVER = "MCP_SERVER_ERROR"
MCP_ERROR_MALFORMED_RESULT = "MCP_MALFORMED_RESULT"

DEFAULT_SERVER_MODULE = "app.mcp.server"


@dataclass(frozen=True)
class MCPToolInfo:
    """A tool discovered through MCP list_tools."""

    name: str
    description: str
    input_schema: dict[str, Any]


def _extract_text(result) -> str:
    """Join the text content of an MCP CallToolResult (never a traceback)."""
    parts: list[str] = []
    for item in result.content or []:
        if getattr(item, "type", None) == "text":
            parts.append(getattr(item, "text", "") or "")
    return "\n".join(parts)


def _classify_error_text(text: str) -> tuple[ToolResultStatus, str]:
    lowered = (text or "").lower()
    if "unknown tool" in lowered:
        return ToolResultStatus.FAILED, MCP_ERROR_TOOL_NOT_FOUND
    if "validation error" in lowered or "rejected arguments" in lowered:
        return ToolResultStatus.VALIDATION_ERROR, MCP_ERROR_INVALID_ARGUMENTS
    return ToolResultStatus.FAILED, MCP_ERROR_SERVER


def map_result_to_tool_result(
    tool_name: str, *, is_error: bool, text: str
) -> ToolResult:
    """Convert one MCP call result (text payload) into an internal ToolResult."""
    payload: Any = None
    try:
        payload = json.loads(text or "")
    except (json.JSONDecodeError, TypeError):
        payload = None

    if isinstance(payload, dict) and "ok" in payload:
        if payload["ok"]:
            data = payload.get("data")
            return ToolResult(
                tool_name=tool_name,
                status=ToolResultStatus.SUCCESS,
                data=dict(data) if isinstance(data, dict) else {},
            )
        status_value = str(payload.get("status") or ToolResultStatus.FAILED.value)
        try:
            status = ToolResultStatus(status_value)
        except ValueError:
            status = ToolResultStatus.FAILED
        return ToolResult(
            tool_name=tool_name,
            status=status,
            error_code=str(payload.get("code") or MCP_ERROR_SERVER),
            error_message=str(payload.get("message") or "MCP tool failed."),
        )

    if is_error:
        status, code = _classify_error_text(text)
        return ToolResult(
            tool_name=tool_name,
            status=status,
            error_code=code,
            error_message=(text or "").strip()[:500] or "MCP tool call failed.",
        )

    return ToolResult(
        tool_name=tool_name,
        status=ToolResultStatus.FAILED,
        error_code=MCP_ERROR_MALFORMED_RESULT,
        error_message="MCP tool returned an unexpected payload.",
    )


@asynccontextmanager
async def _stdio_session(server_params) -> AsyncIterator[Any]:
    """Spawn the stdio MCP Server and open one initialized ClientSession."""
    from mcp import ClientSession
    from mcp.client.stdio import stdio_client

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


class MCPClient:
    """Sync stdio facade over the official MCP ClientSession.

    One method call = one short-lived stdio connection, which keeps the MVP
    adapter free of background event loops (see docs/MCP.md limitations).
    """

    def __init__(
        self,
        *,
        command: str | None = None,
        args: list[str] | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = command or sys.executable
        self._args = list(args) if args is not None else ["-m", DEFAULT_SERVER_MODULE]
        self._cwd = cwd
        self._env = dict(env) if env is not None else dict(os.environ)

    def _params(self):
        from mcp import StdioServerParameters

        return StdioServerParameters(
            command=self._command,
            args=self._args,
            env=dict(self._env),
            cwd=self._cwd,
        )

    def list_tools(self) -> list[MCPToolInfo]:
        """Discover tools through MCP list_tools (name/description/schema)."""
        params = self._params()

        async def _run() -> list[MCPToolInfo]:
            async with _stdio_session(params) as session:
                result = await session.list_tools()
                return [
                    MCPToolInfo(
                        name=tool.name,
                        description=tool.description or "",
                        input_schema=dict(tool.input_schema or {}),
                    )
                    for tool in result.tools
                ]

        return asyncio.run(_run())

    def call_tool(self, name: str, arguments: dict | None) -> ToolResult:
        """Call one MCP tool and normalize the result into a ToolResult."""
        params = self._params()

        async def _run() -> ToolResult:
            async with _stdio_session(params) as session:
                result = await session.call_tool(name, dict(arguments or {}))
                text = _extract_text(result)
                return map_result_to_tool_result(
                    name,
                    is_error=bool(getattr(result, "is_error", False)),
                    text=text,
                )

        try:
            return asyncio.run(_run())
        except Exception as exc:  # transport / handshake failures
            logger.exception("MCP stdio connection failed: tool=%s", name)
            return ToolResult(
                tool_name=name,
                status=ToolResultStatus.FAILED,
                error_code=MCP_ERROR_SERVER,
                error_message=(
                    f"MCP connection failed: {type(exc).__name__}. "
                    "See server logs for details."
                ),
            )
