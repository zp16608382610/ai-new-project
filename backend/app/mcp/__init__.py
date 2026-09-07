"""MCP integration layer (Phase 6 MVP).

The MCP Server standardizes tool exposure; it never decides intent, risk or
business rules. The Agent still routes + reasons, the Risk Engine still gates,
and the Service layer still owns business rules (docs/MCP.md).

Components:
    tools.py    the three exposed tools + Service-bound call functions
    server.py   MCPServer ("ecommerce-customer-service") + stdio entrypoint
    client.py   minimal stdio MCP client that maps results into ToolResult
    adapter.py  Agent-side adapter: MCP tools via MCP, others via executor

Note: server.py is intentionally NOT imported here. `python -m app.mcp.server`
must stay a clean subprocess entrypoint, so importing the app.mcp package may
never pre-load the server module (runpy would otherwise warn / re-execute).
"""

from app.mcp.adapter import MCPToolAdapter
from app.mcp.client import MCPClient, MCPToolInfo
from app.mcp.tools import MCP_TOOL_NAMES

__all__ = [
    "MCPToolAdapter",
    "MCPClient",
    "MCPToolInfo",
    "MCP_TOOL_NAMES",
]
