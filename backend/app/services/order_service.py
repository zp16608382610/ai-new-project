"""Order service: read models, logistics lookup and cancellation state machine.

调用链:HTTP API → OrderService → OrderRepository / LogisticsRepository / RefundRepository → Database。
路由层不包含业务规则;规则与状态流转集中在此处。
"""
from sqlalchemy.orm import Session

from app.db.enums import OrderStatus
from app.db.repository import (
    LogisticsRepository,
    OrderRepository,
    RefundRepository,
    UserRepository,
)
from app.schemas.order import LogisticsOut, OrderOut, OrderSummaryOut
from app.schemas.refund import CancelOrderResponse
from app.services.errors import ConflictError, InvalidOperationError, NotFoundError


class OrderService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.orders = OrderRepository(session)
        self.logistics = LogisticsRepository(session)
        self.refunds = RefundRepository(session)

    def get_order(self, order_id: int) -> OrderOut:
        order = self.orders.get_full(order_id)
        if order is None:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")
        return OrderOut.from_order(order)

    def get_logistics(self, order_id: int) -> LogisticsOut:
        order = self.orders.get(order_id)
        if order is None:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")
        latest = self.logistics.get_latest_by_order(order_id)
        if latest is None:
            raise NotFoundError(
                "No logistics record found for this order", code="LOGISTICS_NOT_FOUND"
            )
        return LogisticsOut.from_logistics(latest)

    def get_user_orders(
        self, user_id: int, status: OrderStatus | None = None
    ) -> list[OrderSummaryOut]:
        user = self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found", code="USER_NOT_FOUND")
        orders = self.orders.list_by_user(user_id, status=status)
        return [OrderSummaryOut.from_order(order) for order in orders]

    def cancel_order(self, order_id: int) -> CancelOrderResponse:
        """Cancel an order when the current state allows it."""
        order = self.orders.get_full(order_id)
        if order is None:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")

        if order.status == OrderStatus.CANCELLED:
            raise ConflictError(
                "Order has already been cancelled", code="ORDER_ALREADY_CANCELLED"
            )
        if order.status in (OrderStatus.DELIVERED, OrderStatus.REFUNDED):
            raise InvalidOperationError(
                "Delivered or refunded orders cannot be cancelled",
                code="ORDER_NOT_CANCELLABLE",
            )
        if self.refunds.list_active_by_order(order.id):
            raise ConflictError(
                "Order has a pending refund request; resolve it before cancelling",
                code="ORDER_HAS_ACTIVE_REFUND",
            )

        # Only PENDING / PAID / SHIPPED reach this point.
        previous_status = order.status
        order.status = OrderStatus.CANCELLED
        self.session.commit()
        return CancelOrderResponse(
            order_id=order.id,
            previous_status=previous_status,
            status=OrderStatus.CANCELLED,
            message="Order cancelled",
        )