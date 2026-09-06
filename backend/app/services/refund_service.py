"""Refund service: authoritative eligibility rules and refund request creation.

设计说明:
- 退款资格与金额判定是业务规则,由 Service 决定,不允许客户端自定义金额。
- 本阶段创建的是退款申请(PENDING),不执行资金操作;资金执行、风控与
  人工审批属于 Phase 7(PRD §5 S5 / §7 CRITICAL)。
- 全额退款金额 = 订单 authoritative total_amount(与明细 sum 一致,见 seed 断言)。
"""
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.enums import OrderStatus, RefundStatus
from app.db.models import Order
from app.db.repository import OrderRepository, RefundRepository
from app.schemas.refund import RefundEligibilityResponse, RefundOut
from app.services.errors import ConflictError, InvalidOperationError, NotFoundError


class RefundService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.orders = OrderRepository(session)
        self.refunds = RefundRepository(session)

    # ---- public API ---------------------------------------------------

    def check_eligibility(self, order_id: int) -> RefundEligibilityResponse:
        order = self.orders.get_full(order_id)
        if order is None:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")
        return self._decision(order)

    def create_refund(self, order_id: int, reason: str | None) -> RefundOut:
        order = self.orders.get_full(order_id)
        if order is None:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")

        if order.status == OrderStatus.REFUNDED or self.refunds.has_completed(order.id):
            raise ConflictError(
                "Order has already been refunded", code="ORDER_ALREADY_REFUNDED"
            )
        if self.refunds.list_active_by_order(order.id):
            raise ConflictError(
                "Order already has a pending refund request",
                code="DUPLICATE_REFUND",
            )

        decision = self._decision(order)
        if not decision.eligible:
            raise InvalidOperationError(
                decision.reason, code="REFUND_NOT_ELIGIBLE"
            )

        amount = order.total_amount  # authoritative amount, not client-supplied
        refund = self.refunds.create(
            order_id=order.id,
            user_id=order.user_id,
            amount=amount,
            status=RefundStatus.PENDING,
            reason=(reason or None),
        )
        self.session.commit()
        return RefundOut.from_refund(refund)

    # ---- private rules ------------------------------------------------

    def _decision(self, order: Order) -> RefundEligibilityResponse:
        """Eligibility decision based on authoritative business data."""
        zero = Decimal("0")
        if order.status == OrderStatus.REFUNDED or self.refunds.has_completed(order.id):
            return RefundEligibilityResponse(
                eligible=False, reason="Order has already been refunded", refund_amount=zero
            )
        if self.refunds.list_active_by_order(order.id):
            return RefundEligibilityResponse(
                eligible=False,
                reason="Order already has a pending refund request",
                refund_amount=zero,
            )
        if order.status == OrderStatus.CANCELLED:
            return RefundEligibilityResponse(
                eligible=False, reason="Cancelled orders cannot be refunded", refund_amount=zero
            )
        if order.status == OrderStatus.PENDING:
            return RefundEligibilityResponse(
                eligible=False, reason="Order has not been paid yet", refund_amount=zero
            )
        if order.status in (OrderStatus.PAID, OrderStatus.SHIPPED):
            return RefundEligibilityResponse(
                eligible=False,
                reason="Order has not been delivered yet",
                refund_amount=zero,
            )
        if order.status == OrderStatus.DELIVERED:
            if not order.items:
                return RefundEligibilityResponse(
                    eligible=False, reason="Order has no items", refund_amount=zero
                )
            if not all(item.product.returnable for item in order.items):
                return RefundEligibilityResponse(
                    eligible=False,
                    reason="Order contains non-returnable items",
                    refund_amount=zero,
                )
            return RefundEligibilityResponse(
                eligible=True,
                reason="Order is eligible for a full refund",
                refund_amount=order.total_amount,
            )
        return RefundEligibilityResponse(
            eligible=False, reason="Order is not refundable", refund_amount=zero
        )