"""Order / logistics API schemas."""
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel

from app.db.enums import LogisticsStatus, OrderStatus
from app.db.models import Logistics, Order, OrderItem
from app.schemas.common import MoneyModel


class UserBrief(BaseModel):
    """Minimal user information exposed on order responses."""

    id: int
    name: str


class OrderItemOut(MoneyModel):
    """Order line item (snapshot from order_items)."""

    id: int
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal


class OrderOut(MoneyModel):
    """Order detail response: aggregate including buyer and items."""

    id: int
    user: UserBrief
    status: OrderStatus
    total_amount: Decimal
    currency: str
    items: list[OrderItemOut]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> "OrderOut":
        items = [
            OrderItemOut(
                id=item.id,
                product_id=item.product_id,
                product_name=item.product_name,
                quantity=item.quantity,
                unit_price=item.unit_price,
            )
            for item in order.items
        ]
        user = UserBrief(id=order.user.id, name=order.user.name)
        return cls(
            id=order.id,
            user=user,
            status=order.status,
            total_amount=order.total_amount,
            currency=order.currency,
            items=items,
            created_at=order.created_at,
            updated_at=order.updated_at,
        )


class OrderSummaryOut(MoneyModel):
    """Lightweight order summary for user order listings."""

    id: int
    status: OrderStatus
    total_amount: Decimal
    currency: str
    items_count: int
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_order(cls, order: Order) -> "OrderSummaryOut":
        return cls(
            id=order.id,
            status=order.status,
            total_amount=order.total_amount,
            currency=order.currency,
            items_count=len(order.items),
            created_at=order.created_at,
            updated_at=order.updated_at,
        )


class LogisticsOut(BaseModel):
    """Logistics record for an order (latest record)."""

    carrier: str
    tracking_number: str
    status: LogisticsStatus
    estimated_delivery: date | None
    updated_at: datetime

    @classmethod
    def from_logistics(cls, logistics: Logistics) -> "LogisticsOut":
        return cls(
            carrier=logistics.carrier,
            tracking_number=logistics.tracking_number,
            status=logistics.status,
            estimated_delivery=logistics.estimated_delivery,
            updated_at=logistics.updated_at,
        )