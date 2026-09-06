"""Concrete tool handlers (Phase 4B).

Handlers are thin adapters over the existing Service layer:

    Tool handler -> OrderService / RefundService / TicketService
                 -> Repository -> Database

Rules that stay HERE (execution boundary, not business rules):
    - trusted user_id is required and comes from ToolExecutionContext;
    - an order that exists but belongs to another user is rejected
      (UNAUTHORIZED_ORDER_ACCESS) - existence is never conflated with access;
    - create_ticket verifies ownership of an optional referenced order.

Rules that stay in the Service layer (never copied into tools):
    refund eligibility, refund amounts, cancellation state machine, duplicate
    refund detection, ticket entity validation.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.db.enums import TicketPriority
from app.schemas.order import OrderOut
from app.services.order_service import OrderService
from app.services.refund_service import RefundService
from app.services.ticket_service import TicketService
from app.tools.base import (
    RiskLevel,
    ToolDefinition,
    ToolExecutionContext,
)
from app.tools.definitions import (
    CancelOrderInput,
    CancelOrderToolOutput,
    CheckRefundEligibilityInput,
    CreateRefundInput,
    CreateTicketInput,
    GetLogisticsInput,
    GetOrderInput,
    LogisticsToolOutput,
    OrderItemToolOutput,
    OrderToolOutput,
    RefundEligibilityToolOutput,
    RefundToolOutput,
    TicketToolOutput,
)
from app.tools.errors import UnauthorizedOrderAccessError
from app.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# shared authorization helper
# ---------------------------------------------------------------------------


def _require_user_id(context: ToolExecutionContext) -> int:
    if context.user_id is None:
        from app.tools.errors import ToolInputError

        raise ToolInputError(
            "A trusted user identity (session context) is required to execute this tool."
        )
    return context.user_id


def _load_authorized_order(
    orders: OrderService, order_id: int, user_id: int
) -> OrderOut:
    """Load an order and reject it when it is not owned by the trusted user.

    OrderService.get_order raises NotFoundError(ORDER_NOT_FOUND) when the
    order does not exist - the executor turns that into a NOT_FOUND result.
    Existence and permission therefore stay distinct outcomes.
    """
    order = orders.get_order(order_id)
    if order.user.id != user_id:
        raise UnauthorizedOrderAccessError(
            "You are not authorized to access this order."
        )
    return order


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def _get_order_handler(orders: OrderService):
    def handle(payload: GetOrderInput, context: ToolExecutionContext) -> OrderToolOutput:
        user_id = _require_user_id(context)
        order = _load_authorized_order(orders, payload.order_id, user_id)
        return OrderToolOutput(
            order_id=order.id,
            status=order.status,
            total_amount=order.total_amount,
            currency=order.currency,
            items=[
                OrderItemToolOutput(
                    product_name=item.product_name,
                    quantity=item.quantity,
                    unit_price=item.unit_price,
                )
                for item in order.items
            ],
            created_at=order.created_at,
        )

    return handle


def _get_logistics_handler(orders: OrderService):
    def handle(payload: GetLogisticsInput, context: ToolExecutionContext) -> LogisticsToolOutput:
        user_id = _require_user_id(context)
        _load_authorized_order(orders, payload.order_id, user_id)
        logistics = orders.get_logistics(payload.order_id)
        return LogisticsToolOutput(
            order_id=payload.order_id,
            carrier=logistics.carrier,
            tracking_number=logistics.tracking_number,
            status=logistics.status,
            estimated_delivery=logistics.estimated_delivery,
            updated_at=logistics.updated_at,
        )

    return handle


def _check_refund_eligibility_handler(refunds: RefundService, orders: OrderService):
    def handle(
        payload: CheckRefundEligibilityInput, context: ToolExecutionContext
    ) -> RefundEligibilityToolOutput:
        user_id = _require_user_id(context)
        _load_authorized_order(orders, payload.order_id, user_id)
        decision = refunds.check_eligibility(payload.order_id)
        return RefundEligibilityToolOutput(
            order_id=payload.order_id,
            eligible=decision.eligible,
            reason=decision.reason,
            refund_amount=decision.refund_amount,
        )

    return handle


def _create_refund_handler(refunds: RefundService, orders: OrderService):
    def handle(payload: CreateRefundInput, context: ToolExecutionContext) -> RefundToolOutput:
        user_id = _require_user_id(context)
        _load_authorized_order(orders, payload.order_id, user_id)
        # The service derives the authoritative amount; payload has no amount.
        refund = refunds.create_refund(payload.order_id, reason=payload.reason)
        return RefundToolOutput(
            id=refund.id,
            order_id=refund.order_id,
            amount=refund.amount,
            status=refund.status,
            reason=refund.reason,
            created_at=refund.created_at,
        )

    return handle


def _cancel_order_handler(orders: OrderService):
    def handle(payload: CancelOrderInput, context: ToolExecutionContext) -> CancelOrderToolOutput:
        user_id = _require_user_id(context)
        _load_authorized_order(orders, payload.order_id, user_id)
        response = orders.cancel_order(payload.order_id)
        return CancelOrderToolOutput(
            order_id=response.order_id,
            previous_status=response.previous_status,
            status=response.status,
            message=response.message,
        )

    return handle


def _create_ticket_handler(tickets: TicketService, orders: OrderService):
    def handle(payload: CreateTicketInput, context: ToolExecutionContext) -> TicketToolOutput:
        user_id = _require_user_id(context)
        if payload.order_id is not None:
            _load_authorized_order(orders, payload.order_id, user_id)
        ticket = tickets.create_ticket(
            user_id=user_id,
            order_id=payload.order_id,
            category=payload.reason,
            priority=TicketPriority.MEDIUM,
            description=payload.description,
        )
        return TicketToolOutput(
            id=ticket.id,
            order_id=ticket.order_id,
            category=ticket.category,
            priority=ticket.priority,
            status=ticket.status,
            description=ticket.description,
            created_at=ticket.created_at,
        )

    return handle


# ---------------------------------------------------------------------------
# registry wiring (one shared session per workflow/request)
# ---------------------------------------------------------------------------


def build_default_registry(session: Session) -> ToolRegistry:
    """Register the six Phase 4B business tools backed by the mock services."""
    orders = OrderService(session)
    refunds = RefundService(session)
    tickets = TicketService(session)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="get_order",
            description="Query one order owned by the requesting user.",
            input_schema=GetOrderInput,
            output_schema=OrderToolOutput,
            risk_level=RiskLevel.LOW,
            handler=_get_order_handler(orders),
        )
    )
    registry.register(
        ToolDefinition(
            name="get_logistics",
            description="Query the latest logistics record for an order owned by the requesting user.",
            input_schema=GetLogisticsInput,
            output_schema=LogisticsToolOutput,
            risk_level=RiskLevel.LOW,
            handler=_get_logistics_handler(orders),
        )
    )
    registry.register(
        ToolDefinition(
            name="check_refund_eligibility",
            description="Check whether an order owned by the requesting user is refundable.",
            input_schema=CheckRefundEligibilityInput,
            output_schema=RefundEligibilityToolOutput,
            risk_level=RiskLevel.LOW,
            handler=_check_refund_eligibility_handler(refunds, orders),
        )
    )
    registry.register(
        ToolDefinition(
            name="create_refund",
            description="Create a refund request for an eligible order (authoritative amount).",
            input_schema=CreateRefundInput,
            output_schema=RefundToolOutput,
            risk_level=RiskLevel.HIGH,
            handler=_create_refund_handler(refunds, orders),
        )
    )
    registry.register(
        ToolDefinition(
            name="cancel_order",
            description="Cancel an order when the current state allows it.",
            input_schema=CancelOrderInput,
            output_schema=CancelOrderToolOutput,
            risk_level=RiskLevel.HIGH,
            handler=_cancel_order_handler(orders),
        )
    )
    registry.register(
        ToolDefinition(
            name="create_ticket",
            description="Create a support/complaint ticket for the requesting user.",
            input_schema=CreateTicketInput,
            output_schema=TicketToolOutput,
            risk_level=RiskLevel.MEDIUM,
            handler=_create_ticket_handler(tickets, orders),
        )
    )
    return registry