"""Phase 4B ToolExecutor tests (spec items 39-44).

The executor is the single execution boundary:
    39  executes a registered tool
    40  unknown tool -> UNKNOWN_TOOL
    41  validation error normalization
    42  business error normalization (no leaked internals)
    43  unexpected exception normalization (safe message + diagnostic)
    44  user context cannot be overridden by tool arguments
"""
from decimal import Decimal

import pytest
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.enums import OrderStatus
from app.db.models import Order, Ticket
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory
from app.tools.base import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionContext,
    ToolResultStatus,
)
from app.tools.executor import ToolExecutor
from app.tools.handlers import build_default_registry
from app.tools.registry import ToolRegistry


class _EmptyInput(BaseModel):
    pass


@pytest.fixture()
def db_session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def seeded(db_session) -> Session:
    assert seed_dev_data(db_session) is True
    db_session.commit()
    return db_session


@pytest.fixture()
def executor(seeded) -> ToolExecutor:
    return ToolExecutor(build_default_registry(seeded))


def _ctx(user_id: int) -> ToolExecutionContext:
    return ToolExecutionContext(request_id="req-exec", session_id="sess-1", user_id=user_id)


def test_executor_executes_registered_tool(executor):
    result = executor.execute("get_order", {"order_id": "ORD-0001"}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.SUCCESS
    assert result.tool_name == "get_order"
    assert result.data["order_id"] == 1
    assert result.metadata["tool_name"] == "get_order"
    assert result.metadata["success"] is True
    assert "execution_id" in result.metadata
    assert "duration_ms" in result.metadata


def test_executor_unknown_tool_returns_unknown_tool(executor):
    result = executor.execute("delete_everything", {}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "UNKNOWN_TOOL"
    assert "delete_everything" in (result.error_message or "")
    assert result.data == {}


def test_executor_validation_error_normalized(executor):
    result = executor.execute(
        "get_logistics", {"order_id": "ORD-not-a-number"}, _ctx(user_id=1)
    )
    assert result.status is ToolResultStatus.VALIDATION_ERROR
    assert result.error_code == "INVALID_ARGUMENTS"
    assert "order_id" in (result.error_message or "")


def test_executor_business_error_normalized(executor, seeded):
    # Delivered order (id 4, Alice) cannot be cancelled.
    result = executor.execute("cancel_order", {"order_id": 4}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.BUSINESS_ERROR
    assert result.error_code == "ORDER_NOT_CANCELLABLE"
    message = (result.error_message or "").lower()
    assert "cancelled" in message or "cancel" in message
    assert "sqlalchemy" not in message
    assert "traceback" not in message


def test_executor_unexpected_exception_normalized(executor):
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="boom_tool",
            description="always fails",
            input_schema=_EmptyInput,
            output_schema=_EmptyInput,
            risk_level=RiskLevel.HIGH,
            handler=lambda payload, context: (_ for _ in ()).throw(RuntimeError("boom-secret")),
        )
    )
    boom = ToolExecutor(registry)
    result = boom.execute("boom_tool", {}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "INTERNAL_TOOL_ERROR"
    # Safe user-facing message must not leak the exception detail.
    assert "boom-secret" not in (result.error_message or "")
    assert "traceback" not in (result.error_message or "").lower()
    # Diagnostics stay in metadata for observability.
    assert "boom-secret" in result.metadata.get("diagnostic", "")


def test_executor_user_context_cannot_be_overridden_by_arguments(executor, seeded):
    # order 5 belongs to Bob (user 2). Alice (user 1) asks the tool to run as
    # Bob via arguments; the trusted context must win -> unauthorized.
    forged = executor.execute(
        "get_order", {"order_id": 5, "user_id": 2}, _ctx(user_id=1)
    )
    assert forged.status is ToolResultStatus.FAILED
    assert forged.error_code == "UNAUTHORIZED_ORDER_ACCESS"

    # The same order is reachable when the trusted context actually is Bob,
    # even though the arguments still claim Alice -> context wins the other way.
    owner = executor.execute(
        "get_order", {"order_id": 5, "user_id": 1}, _ctx(user_id=2)
    )
    assert owner.status is ToolResultStatus.SUCCESS
    assert owner.data["order_id"] == 5


def test_executor_context_user_used_for_ticket_creation(executor, seeded):
    # Arguments claim user 2, but the trusted context is user 3 -> the ticket
    # must be recorded under user 3 (the ticket handler ignores argument user_id).
    result = executor.execute(
        "create_ticket",
        {"user_id": 2, "reason": "ACCOUNT", "description": "账户问题"},
        _ctx(user_id=3),
    )
    assert result.status is ToolResultStatus.SUCCESS
    row = seeded.scalar(
        select(Ticket).where(Ticket.description == "账户问题")
    )
    assert row is not None
    assert row.user_id == 3