"""MCP-exposed tool set (Phase 6 MVP).

Only three read-safe / traceable tools are exposed through MCP:

    get_order        query authoritative order data      (Service)
    get_logistics    query dynamic logistics information (Service)
    create_ticket    register a support / complaint      (Service)

High-risk operations (create_refund / cancel_order) are deliberately NOT
exposed through MCP. They keep using the internal Tool Executor behind the
Phase 5 Risk Gate + Human Approval so MCP can never bypass risk control.

Boundary rules:
    - Every call function runs:  MCP tool -> Service -> Repository -> DB.
    - None of these functions opens a raw SQLAlchemy session itself; the
      server passes one per call and closes it (session scope lives there).
    - user_id is the trusted identity supplied by the Agent-side adapter /
      transport boundary. Order lookups still verify ownership so a wrong
      identity can never read another user's order.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.enums import TicketPriority
from app.services.order_service import OrderService
from app.services.ticket_service import TicketService
from app.tools.definitions import (
    LogisticsToolOutput,
    OrderItemToolOutput,
    OrderToolOutput,
    TicketToolOutput,
    parse_order_ref,
)
from app.tools.errors import ToolInputError, UnauthorizedOrderAccessError

MCP_TOOL_NAMES = ("get_order", "get_logistics", "create_ticket")


def _require_user_id(user_id: int | None) -> int:
    if user_id is None:
        raise ToolInputError(
            "A trusted user identity is required to execute this MCP tool."
        )
    return user_id


def _load_authorized_order(
    orders: OrderService, order_id: int, user_id: int
):
    """Load an order and reject it when it belongs to a different user."""
    order = orders.get_order(order_id)
    if order.user.id != user_id:
        raise UnauthorizedOrderAccessError(
            "You are not authorized to access this order."
        )
    return order


def _parse_order_id(value) -> int:
    try:
        return parse_order_ref(value)
    except ValueError as exc:
        raise ToolInputError(str(exc)) from exc


def get_order_result(session: Session, order_id, user_id: int | None) -> dict:
    """Service-bound get_order returning the internal OrderToolOutput data."""
    trusted = _require_user_id(user_id)
    parsed = _parse_order_id(order_id)
    order = _load_authorized_order(OrderService(session), parsed, trusted)
    return OrderToolOutput(
        order_id=order.id,
        status=order.status,
        total_amount=Decimal(str(order.total_amount)),
        currency=order.currency,
        items=[
            OrderItemToolOutput(
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=Decimal(str(item.unit_price)),
            )
            for item in order.items
        ],
        created_at=order.created_at,
    ).model_dump(mode="json")


def get_logistics_result(session: Session, order_id, user_id: int | None) -> dict:
    """Service-bound get_logistics returning the internal output data."""
    trusted = _require_user_id(user_id)
    parsed = _parse_order_id(order_id)
    orders = OrderService(session)
    _load_authorized_order(orders, parsed, trusted)
    logistics = orders.get_logistics(parsed)
    return LogisticsToolOutput(
        order_id=parsed,
        carrier=logistics.carrier,
        tracking_number=logistics.tracking_number,
        status=logistics.status,
        estimated_delivery=logistics.estimated_delivery,
        updated_at=logistics.updated_at,
    ).model_dump(mode="json")


def create_ticket_result(
    session: Session,
    *,
    user_id: int | None,
    reason: str,
    description: str,
    order_id=None,
) -> dict:
    """Service-bound create_ticket returning the internal output data."""
    trusted = _require_user_id(user_id)
    parsed_order_id = _parse_order_id(order_id) if order_id is not None else None
    tickets = TicketService(session)
    if parsed_order_id is not None:
        _load_authorized_order(OrderService(session), parsed_order_id, trusted)
    ticket = tickets.create_ticket(
        user_id=trusted,
        order_id=parsed_order_id,
        category=reason,
        priority=TicketPriority.MEDIUM,
        description=description,
    )
    return TicketToolOutput(
        id=ticket.id,
        order_id=ticket.order_id,
        category=ticket.category,
        priority=ticket.priority,
        status=ticket.status,
        description=ticket.description,
        created_at=ticket.created_at,
    ).model_dump(mode="json")
