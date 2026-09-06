"""Phase 4B tool tests: Registry + input validation + authorization + the six
business tools (get_order / get_logistics / check_refund_eligibility /
create_refund / cancel_order / create_ticket).

Covers the Phase 4B spec items 1-38:
    1-5   Registry (register / duplicate / get / unknown / list)
    6-9   Input validation (missing / type / empty / invalid)
    10-14 Authorization (own order, cross-user order/logistics/refund/cancel)
    15-16 Order
    17-18 Logistics
    19-26 Refund eligibility matrix
    27-30 Create refund (incl. authoritative amount)
    31-34 Cancellation
    35-38 Ticket

All tool calls go through ToolExecutor so every path is the same validated
boundary the Agent uses.
"""
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.base import Base
from app.db.enums import OrderStatus, RefundStatus
from app.db.models import Order, OrderItem, Refund, Ticket, User
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory
from app.tools.base import (
    ToolDefinition,
    ToolExecutionContext,
    ToolResultStatus,
)
from app.tools.errors import ToolRegistryError
from app.tools.executor import ToolExecutor
from app.tools.handlers import build_default_registry
from app.tools.registry import ToolRegistry


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


def _ctx(user_id: int, request_id: str = "req-tools") -> ToolExecutionContext:
    return ToolExecutionContext(request_id=request_id, session_id="sess-1", user_id=user_id)


def _load_order(session: Session, email: str, status: OrderStatus) -> Order:
    stmt = (
        select(Order)
        .join(Order.user)
        .where(User.email == email, Order.status == status)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
    )
    order = session.scalars(stmt).first()
    assert order is not None, f"seed order missing: {email} {status}"
    return order


def _count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _refunds_of(session: Session, order_id: int) -> list[Refund]:
    return list(
        session.scalars(
            select(Refund).where(Refund.order_id == order_id).order_by(Refund.id)
        )
    )


from pydantic import BaseModel


class _EmptyArgs(BaseModel):
    """Dummy input schema for ad-hoc registered test tools."""

    pass


# ======================================================================
# Registry (items 1-5)
# ======================================================================


def _stub_tool(name: str = "stub_tool") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description="stub",
        input_schema=_EmptyArgs,
        output_schema=_EmptyArgs,
        handler=lambda payload, context: {"ok": True},
    )


def test_registry_register():
    registry = ToolRegistry()
    tool = _stub_tool()
    registry.register(tool)
    assert registry.has("stub_tool")
    assert registry.get("stub_tool") is tool


def test_registry_duplicate_register_raises():
    registry = ToolRegistry()
    registry.register(_stub_tool())
    with pytest.raises(ToolRegistryError):
        registry.register(_stub_tool())


def test_registry_get_existing(executor):
    tool = executor.registry.get("get_order")
    assert tool is not None
    assert tool.name == "get_order"
    assert tool.input_schema is not None


def test_registry_unknown_tool_returns_none():
    registry = ToolRegistry()
    assert registry.get("does_not_exist") is None
    assert registry.has("does_not_exist") is False


def test_registry_list_tools(executor):
    names = [tool.name for tool in executor.registry.list()]
    assert names == [
        "cancel_order",
        "check_refund_eligibility",
        "create_refund",
        "create_ticket",
        "get_logistics",
        "get_order",
    ]


# ======================================================================
# Input validation (items 6-9)
# ======================================================================


def test_validation_missing_required_field(executor):
    result = executor.execute("get_order", {}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.VALIDATION_ERROR
    assert result.error_code == "INVALID_ARGUMENTS"
    assert "order_id" in (result.error_message or "")


def test_validation_invalid_type(executor):
    result = executor.execute("get_order", {"order_id": ["x"]}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.VALIDATION_ERROR
    assert result.error_code == "INVALID_ARGUMENTS"


def test_validation_empty_order_id(executor):
    result = executor.execute("get_order", {"order_id": ""}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.VALIDATION_ERROR
    assert result.error_code == "INVALID_ARGUMENTS"


@pytest.mark.parametrize("order_id", [0, -5, "ORD-abc", "abc", "ORD-"])
def test_validation_invalid_arguments(executor, order_id):
    result = executor.execute("get_order", {"order_id": order_id}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.VALIDATION_ERROR
    assert result.error_code == "INVALID_ARGUMENTS"


# ======================================================================
# Authorization (items 10-14)
# ======================================================================


def test_authorization_own_order_access(executor, seeded):
    result = executor.execute(
        "get_order", {"order_id": 1}, _ctx(user_id=1)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["order_id"] == 1


def test_authorization_cross_user_order_rejected(executor):
    # order 5 belongs to Bob (user 2); Alice (user 1) must be denied.
    result = executor.execute("get_order", {"order_id": 5}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "UNAUTHORIZED_ORDER_ACCESS"


def test_authorization_cross_user_logistics_rejected(executor):
    # order 6 belongs to Bob and has logistics; Alice cannot read it.
    result = executor.execute("get_logistics", {"order_id": 6}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "UNAUTHORIZED_ORDER_ACCESS"


def test_authorization_cross_user_refund_rejected(executor):
    # order 10 (Carol) is refundable, but Alice may not check it.
    result = executor.execute(
        "check_refund_eligibility", {"order_id": 10}, _ctx(user_id=1)
    )
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "UNAUTHORIZED_ORDER_ACCESS"


def test_authorization_cross_user_cancellation_rejected(executor):
    # order 5 (Bob, PAID) is cancellable, but not by Alice.
    result = executor.execute("cancel_order", {"order_id": 5}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.FAILED
    assert result.error_code == "UNAUTHORIZED_ORDER_ACCESS"


# ======================================================================
# get_order (items 15-16)
# ======================================================================


def test_get_order_success(executor, seeded):
    order = _load_order(seeded, "alice@example.com", OrderStatus.PENDING)
    result = executor.execute("get_order", {"order_id": order.id}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.SUCCESS
    assert result.error_code is None
    data = result.data
    assert data["order_id"] == order.id
    assert data["status"] == order.status.value
    assert Decimal(str(data["total_amount"])) == order.total_amount
    assert data["currency"] == "CNY"
    assert len(data["items"]) == len(order.items)
    # No internal / user / sensitive fields leak into the tool payload.
    assert "user_id" not in data
    assert "user" not in data


def test_get_order_not_found(executor):
    result = executor.execute("get_order", {"order_id": 999999}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.error_code == "ORDER_NOT_FOUND"
    assert result.data == {}


# ======================================================================
# get_logistics (items 17-18)
# ======================================================================


def test_get_logistics_success(executor, seeded):
    order = _load_order(seeded, "alice@example.com", OrderStatus.SHIPPED)
    result = executor.execute(
        "get_logistics", {"order_id": order.id}, _ctx(user_id=1)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["order_id"] == order.id
    assert result.data["tracking_number"] == "YT10020002"
    assert result.data["carrier"] == "YTO Express"
    assert result.data["status"] == "OUT_FOR_DELIVERY"


def test_get_logistics_not_found(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.DELIVERED)  # o10: no logistics
    result = executor.execute(
        "get_logistics", {"order_id": order.id}, _ctx(user_id=3)
    )
    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.error_code == "LOGISTICS_NOT_FOUND"


# ======================================================================
# check_refund_eligibility (items 19-26)
# ======================================================================


def test_refund_eligibility_success(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.DELIVERED)  # o10 fully returnable
    result = executor.execute(
        "check_refund_eligibility", {"order_id": order.id}, _ctx(user_id=3)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["eligible"] is True
    assert result.data["order_id"] == order.id
    assert Decimal(str(result.data["refund_amount"])) == order.total_amount


def test_refund_eligibility_failure_reason(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.CANCELLED)
    result = executor.execute(
        "check_refund_eligibility", {"order_id": order.id}, _ctx(user_id=3)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["eligible"] is False
    assert result.data["refund_amount"] == 0.0


def test_refund_eligibility_non_refundable_product(executor, seeded):
    order = _load_order(seeded, "bob@example.com", OrderStatus.DELIVERED)  # o7 watch
    result = executor.execute(
        "check_refund_eligibility", {"order_id": order.id}, _ctx(user_id=2)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["eligible"] is False
    assert "non-returnable" in result.data["reason"].lower()


def test_refund_eligibility_already_refunded(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.REFUNDED)  # o9
    result = executor.execute(
        "check_refund_eligibility", {"order_id": order.id}, _ctx(user_id=3)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["eligible"] is False
    assert "already been refunded" in result.data["reason"].lower()


def test_refund_eligibility_in_flight_refund(executor, seeded):
    order = _load_order(seeded, "alice@example.com", OrderStatus.DELIVERED)  # o4 pending refund
    result = executor.execute(
        "check_refund_eligibility", {"order_id": order.id}, _ctx(user_id=1)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["eligible"] is False
    assert "pending refund request" in result.data["reason"].lower()


def test_refund_eligibility_cancelled_order(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.CANCELLED)
    result = executor.execute(
        "check_refund_eligibility", {"order_id": order.id}, _ctx(user_id=3)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["eligible"] is False
    assert "cancelled" in result.data["reason"].lower()


def test_refund_eligibility_unpaid_order(executor, seeded):
    order = _load_order(seeded, "alice@example.com", OrderStatus.PENDING)
    result = executor.execute(
        "check_refund_eligibility", {"order_id": order.id}, _ctx(user_id=1)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["eligible"] is False
    assert "not been paid" in result.data["reason"].lower()


def test_refund_eligibility_undelivered_order(executor, seeded):
    order = _load_order(seeded, "alice@example.com", OrderStatus.SHIPPED)
    result = executor.execute(
        "check_refund_eligibility", {"order_id": order.id}, _ctx(user_id=1)
    )
    assert result.status is ToolResultStatus.SUCCESS
    assert result.data["eligible"] is False
    assert "not been delivered" in result.data["reason"].lower()


# ======================================================================
# create_refund (items 27-30)
# ======================================================================


def test_create_refund_success(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.DELIVERED)  # o10
    before = _count(seeded, Refund)
    result = executor.execute(
        "create_refund", {"order_id": order.id, "reason": "change_of_mind"}, _ctx(user_id=3)
    )
    assert result.status is ToolResultStatus.SUCCESS
    data = result.data
    assert data["order_id"] == order.id
    assert data["status"] == "PENDING"
    assert Decimal(str(data["amount"])) == order.total_amount
    assert _count(seeded, Refund) == before + 1


def test_create_refund_duplicate_rejected(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.DELIVERED)
    first = executor.execute(
        "create_refund", {"order_id": order.id, "reason": "a"}, _ctx(user_id=3)
    )
    assert first.status is ToolResultStatus.SUCCESS
    second = executor.execute(
        "create_refund", {"order_id": order.id, "reason": "b"}, _ctx(user_id=3)
    )
    assert second.status is ToolResultStatus.BUSINESS_ERROR
    assert second.error_code == "DUPLICATE_REFUND"
    assert len(_refunds_of(seeded, order.id)) == 1


def test_create_refund_amount_cannot_be_client_controlled(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.DELIVERED)
    result = executor.execute(
        "create_refund",
        {
            "order_id": order.id,
            "reason": "x",
            "amount": 0.01,
            "refund_amount": 0.01,
            "status": "APPROVED",
        },
        _ctx(user_id=3),
    )
    assert result.status is ToolResultStatus.SUCCESS
    data = result.data
    # Client-supplied fields never change the amount or the lifecycle state.
    assert Decimal(str(data["amount"])) == order.total_amount
    assert Decimal(str(data["amount"])) != Decimal("0.01")
    assert data["status"] == "PENDING"


def test_create_refund_uses_authoritative_order_amount(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.DELIVERED)
    result = executor.execute("create_refund", {"order_id": order.id}, _ctx(user_id=3))
    assert result.status is ToolResultStatus.SUCCESS
    assert Decimal(str(result.data["amount"])) == order.total_amount


# ======================================================================
# cancel_order (items 31-34)
# ======================================================================


def test_cancel_order_valid(executor, seeded):
    order = _load_order(seeded, "alice@example.com", OrderStatus.PENDING)  # o1
    result = executor.execute("cancel_order", {"order_id": order.id}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.SUCCESS
    data = result.data
    assert data["order_id"] == order.id
    assert data["previous_status"] == "PENDING"
    assert data["status"] == "CANCELLED"
    assert seeded.get(Order, order.id).status == OrderStatus.CANCELLED


def test_cancel_order_delivered_rejected(executor, seeded):
    order = _load_order(seeded, "alice@example.com", OrderStatus.DELIVERED)  # o4
    result = executor.execute("cancel_order", {"order_id": order.id}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.BUSINESS_ERROR
    assert result.error_code == "ORDER_NOT_CANCELLABLE"


def test_cancel_order_refunded_rejected(executor, seeded):
    order = _load_order(seeded, "carol@example.com", OrderStatus.REFUNDED)  # o9
    result = executor.execute("cancel_order", {"order_id": order.id}, _ctx(user_id=3))
    assert result.status is ToolResultStatus.BUSINESS_ERROR
    assert result.error_code == "ORDER_NOT_CANCELLABLE"


def test_cancel_order_blocked_by_active_refund(executor, seeded):
    """Active refund on a theoretically cancellable order blocks cancellation."""
    order = _load_order(seeded, "alice@example.com", OrderStatus.PAID)  # o2
    seeded.add(
        Refund(
            order_id=order.id,
            user_id=order.user_id,
            amount=order.total_amount,
            status=RefundStatus.PENDING,
            reason="in_flight",
        )
    )
    seeded.commit()
    result = executor.execute("cancel_order", {"order_id": order.id}, _ctx(user_id=1))
    assert result.status is ToolResultStatus.BUSINESS_ERROR
    assert result.error_code == "ORDER_HAS_ACTIVE_REFUND"
    assert seeded.get(Order, order.id).status == OrderStatus.PAID


# ======================================================================
# create_ticket (items 35-38)
# ======================================================================


def test_create_ticket_success(executor, seeded):
    before = _count(seeded, Ticket)
    result = executor.execute(
        "create_ticket",
        {"order_id": 2, "reason": "PRODUCT_QUALITY", "description": "外壳有划痕，要求处理"},
        _ctx(user_id=1),
    )
    assert result.status is ToolResultStatus.SUCCESS
    data = result.data
    assert data["order_id"] == 2
    assert data["category"] == "PRODUCT_QUALITY"
    assert data["status"] == "OPEN"
    assert _count(seeded, Ticket) == before + 1
    row = seeded.scalars(select(Ticket).where(Ticket.description == "外壳有划痕，要求处理")).first()
    assert row is not None
    assert row.user_id == 1


def test_create_ticket_invalid_order(executor):
    result = executor.execute(
        "create_ticket",
        {"order_id": 999999, "reason": "REFUND", "description": "订单不存在"},
        _ctx(user_id=1),
    )
    assert result.status is ToolResultStatus.NOT_FOUND
    assert result.error_code == "ORDER_NOT_FOUND"


def test_create_ticket_empty_reason(executor):
    result = executor.execute(
        "create_ticket", {"reason": "", "description": "有内容"}, _ctx(user_id=1)
    )
    assert result.status is ToolResultStatus.VALIDATION_ERROR
    assert result.error_code == "INVALID_ARGUMENTS"


def test_create_ticket_empty_description(executor):
    result = executor.execute(
        "create_ticket", {"reason": "REFUND", "description": "   "}, _ctx(user_id=1)
    )
    assert result.status is ToolResultStatus.VALIDATION_ERROR
    assert result.error_code == "INVALID_ARGUMENTS"