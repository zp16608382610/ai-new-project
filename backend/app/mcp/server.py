"""Minimal MCP Server (Phase 6 MVP).

The server exposes exactly three Service-bound tools through the official MCP
Python SDK (mcp 2.x MCPServer). It runs over stdio locally:

    Agent -> MCP Adapter -> MCP Client -> MCP Server -> Service -> DB

The server never performs intent classification, risk evaluation, approvals or
business decisions. Tool handlers only open a scoped DB session, call the
existing Service layer, and translate domain results into a JSON envelope.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.db.session import create_db_engine, create_session_factory
from app.mcp.tools import (
    create_ticket_result,
    get_logistics_result,
    get_order_result,
)
from app.services.errors import BusinessError, NotFoundError
from app.tools.errors import (
    ERROR_INVALID_ARGUMENTS,
    ERROR_INTERNAL_TOOL_ERROR,
    ToolInputError,
    UnauthorizedOrderAccessError,
)

logger = logging.getLogger("app.mcp.server")

MCP_SERVER_NAME = "ecommerce-customer-service"


def _error_payload(status: str, code: str, message: str) -> dict:
    return {"ok": False, "status": status, "code": code, "message": message}


def _service_bound(
    session_factory: sessionmaker[Session], fn: Callable[[Session], Any]
) -> dict:
    """Run one Service-bound call inside a scoped session and normalize it."""
    session = session_factory()
    try:
        data = fn(session)
    except UnauthorizedOrderAccessError as exc:
        return _error_payload("FAILED", exc.code, exc.detail)
    except ToolInputError as exc:
        return _error_payload("VALIDATION_ERROR", exc.code, exc.detail)
    except NotFoundError as exc:
        return _error_payload("NOT_FOUND", exc.code or "NOT_FOUND", exc.detail)
    except BusinessError as exc:
        return _error_payload("BUSINESS_ERROR", exc.code or "BUSINESS_ERROR", exc.detail)
    except ValidationError as exc:
        return _error_payload("VALIDATION_ERROR", ERROR_INVALID_ARGUMENTS, str(exc))
    except Exception as exc:  # defensive boundary: never leak internals
        logger.exception("Unexpected MCP tool failure")
        return _error_payload("FAILED", ERROR_INTERNAL_TOOL_ERROR, "Internal server error.")
    finally:
        session.close()
    return {"ok": True, "data": data}


def create_mcp_server(
    session_factory: sessionmaker[Session],
) -> Any:
    """Build the MCP Server with its three Service-bound tools."""
    from mcp.server.mcpserver import MCPServer

    server = MCPServer(MCP_SERVER_NAME)

    @server.tool()
    def get_order(order_id: str | int, user_id: int | None = None) -> dict:
        """Query one order owned by the requesting user."""
        return _service_bound(
            session_factory,
            lambda session: get_order_result(session, order_id, user_id),
        )

    @server.tool()
    def get_logistics(order_id: str | int, user_id: int | None = None) -> dict:
        """Query the latest logistics record for an order."""
        return _service_bound(
            session_factory,
            lambda session: get_logistics_result(session, order_id, user_id),
        )

    @server.tool()
    def create_ticket(
        reason: str,
        description: str,
        user_id: int | None = None,
        order_id: str | int | None = None,
    ) -> dict:
        """Create a support/complaint ticket for the requesting user."""
        return _service_bound(
            session_factory,
            lambda session: create_ticket_result(
                session,
                user_id=user_id,
                reason=reason,
                description=description,
                order_id=order_id,
            ),
        )

    return server


def _session_factory_from_env() -> sessionmaker[Session]:
    """Session factory bound to DATABASE_URL (same config as the backend)."""
    settings = get_settings()
    engine = create_db_engine(settings.database_url)
    return create_session_factory(engine)


async def _async_main() -> None:
    server = create_mcp_server(_session_factory_from_env())
    await server.run_stdio_async()


if __name__ == "__main__":  # pragma: no cover - subprocess entrypoint
    asyncio.run(_async_main())
