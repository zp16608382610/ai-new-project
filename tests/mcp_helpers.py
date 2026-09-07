"""Shared helpers for Phase 6 MCP tests.

Mirrors the Phase 4B / 5 pattern but uses a FILE-backed SQLite database so the
MCP Server (a separate stdio subprocess) and the test process can read and
write the same data through independent connections.
"""
from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import create_db_engine, create_session_factory
from phase5_helpers import seed_extended

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"


def build_file_db(path: Path):
    """Create + seed a file SQLite DB. Returns (database_url, factory, engine)."""
    url = "sqlite+pysqlite:///" + str(path).replace("\\", "/")
    engine = create_db_engine(url)
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    try:
        seed_extended(session)
    finally:
        session.close()
    return url, factory, engine


def make_mcp_client(database_url: str, *, backend_dir: Path = BACKEND_DIR):
    """MCPClient that spawns the local MCP Server module against one DB."""
    from app.mcp import MCPClient

    env = {**os.environ, "DATABASE_URL": database_url, "PYTHONUTF8": "1"}
    return MCPClient(args=["-m", "app.mcp.server"], cwd=str(backend_dir), env=env)


def make_mcp_workflow(session: Session, client, *, database_url: str):
    """AgentWorkflow wired with the MCP adapter over one shared file DB."""
    from app.agent.workflow import AgentWorkflow
    from app.mcp.adapter import MCPToolAdapter
    from app.risk import RiskEngine
    from app.services.approval_service import ApprovalService
    from app.services.verification import BusinessVerifier
    from app.tools.executor import ToolExecutor
    from app.tools.handlers import build_default_registry

    internal = ToolExecutor(build_default_registry(session))
    adapter = MCPToolAdapter(internal_executor=internal, client=client)
    return AgentWorkflow(
        tool_executor=adapter,
        risk_engine=RiskEngine(),
        approval_gateway=ApprovalService(session),
        verifier=BusinessVerifier(session),
    )


class RecordingClient:
    """Spy around a real client: records list/call usage, forwards calls."""

    def __init__(self, real) -> None:
        self._real = real
        self.tool_lists = 0
        self.calls: list[tuple[str, dict]] = []

    def list_tools(self):
        self.tool_lists += 1
        return self._real.list_tools()

    def call_tool(self, name: str, arguments: dict | None):
        self.calls.append((name, dict(arguments or {})))
        return self._real.call_tool(name, arguments)
