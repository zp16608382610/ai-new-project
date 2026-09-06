"""Refund API schemas."""
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.db.enums import OrderStatus, RefundStatus
from app.db.models import Refund
from app.schemas.common import MoneyModel


class RefundEligibilityRequest(BaseModel):
    """Request to check whether an order is refundable."""

    order_id: int = Field(gt=0, description="Order id to check")


class RefundEligibilityResponse(MoneyModel):
    """Structured eligibility result produced by the Service layer."""

    eligible: bool
    reason: str
    refund_amount: Decimal


class RefundCreateRequest(BaseModel):
    """Create a refund request for an order. Amount is NOT client-provided."""

    order_id: int = Field(gt=0, description="Order id to refund")
    reason: str | None = Field(default=None, max_length=2000, description="Refund reason")


class RefundOut(MoneyModel):
    """Refund record returned by the API."""

    id: int
    order_id: int
    user_id: int
    amount: Decimal
    status: RefundStatus
    reason: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_refund(cls, refund: Refund) -> "RefundOut":
        return cls(
            id=refund.id,
            order_id=refund.order_id,
            user_id=refund.user_id,
            amount=refund.amount,
            status=refund.status,
            reason=refund.reason,
            created_at=refund.created_at,
            updated_at=refund.updated_at,
        )


class CancelOrderResponse(BaseModel):
    """Result of a successful order cancellation."""

    order_id: int
    previous_status: OrderStatus
    status: OrderStatus
    message: str