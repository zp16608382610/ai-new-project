"""Tool input / output schemas (Phase 4B).

Every tool has an explicit Pydantic input schema. Arguments produced by the
Agent or a future model are never trusted: they pass through this validation
layer before any handler runs.

Order references:
    The Agent layer plans order references as user-visible "ORD-1001" strings.
    Services work with integer primary keys. parse_order_ref() normalizes
    "ORD-1001" / "1001" / integer ids to a positive integer id and rejects
    anything else (empty, "ORD-", letters, zero, negative).

Money amounts:
    Tool input schemas NEVER accept an amount. refund_amount is decided by the
    Service layer from authoritative order data; extra client fields are
    ignored and therefore cannot influence the refund amount.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.enums import (
    LogisticsStatus,
    OrderStatus,
    RefundStatus,
    TicketPriority,
    TicketStatus,
)
from app.schemas.common import MoneyModel

# Default ticket category used by the Agent workflow when the user message does
# not carry a structured reason. 1..50 chars to fit Ticket.category.
DEFAULT_TICKET_CATEGORY = "COMPLAINT"

_ORDER_REF_PATTERN = re.compile(r"(?i)^(?:ORD|ORDER)[-_ ]?(\d+)$")


def parse_order_ref(value: Any) -> int:
    """Normalize an order reference (ORD-1001 / '1001' / int) to a positive int."""
    if isinstance(value, bool) or value is None:
        raise ValueError("order_id must be a positive integer or an ORD-<number> reference")
    if isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        match = _ORDER_REF_PATTERN.fullmatch(text)
        if match is not None:
            number = int(match.group(1))
        elif text.isdigit():
            number = int(text)
        else:
            raise ValueError("order_id must be a positive integer or an ORD-<number> reference")
    if number <= 0:
        raise ValueError("order_id must be a positive integer")
    return number


class _OrderRefMixin(BaseModel):
    """Shared order_id normalization for order-bound tools."""

    order_id: int = Field(description="Order id (integer or ORD-<number> reference)")

    @field_validator("order_id", mode="before")
    @classmethod
    def _normalize_order_id(cls, value: Any) -> int:
        return parse_order_ref(value)


class GetOrderInput(_OrderRefMixin):
    """Input schema for get_order (user_id is overridden by trusted context)."""

    model_config = ConfigDict(extra="ignore")

    user_id: int | None = Field(default=None, ge=1)


class GetLogisticsInput(_OrderRefMixin):
    """Input schema for get_logistics."""

    model_config = ConfigDict(extra="ignore")

    user_id: int | None = Field(default=None, ge=1)


class CheckRefundEligibilityInput(_OrderRefMixin):
    """Input schema for check_refund_eligibility (no amount accepted)."""

    model_config = ConfigDict(extra="ignore")

    user_id: int | None = Field(default=None, ge=1)


class CreateRefundInput(_OrderRefMixin):
    """Input schema for create_refund.

    reason is optional free text; amount/status are never accepted here - the
    Service layer derives the authoritative refund amount from the order.
    """

    model_config = ConfigDict(extra="ignore")

    user_id: int | None = Field(default=None, ge=1)
    reason: str | None = Field(default=None, max_length=2000)


class CancelOrderInput(_OrderRefMixin):
    """Input schema for cancel_order."""

    model_config = ConfigDict(extra="ignore")

    user_id: int | None = Field(default=None, ge=1)


class CreateTicketInput(BaseModel):
    """Input schema for create_ticket.

    reason is the short ticket category (1..50), description the detail
    (1..5000). order_id is optional but, when present, must belong to the
    trusted user.
    """

    model_config = ConfigDict(extra="ignore")

    user_id: int | None = Field(default=None, ge=1)
    order_id: int | None = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=50, description="Ticket category / short reason")
    description: str = Field(min_length=1, max_length=5000)

    @field_validator("order_id", mode="before")
    @classmethod
    def _normalize_optional_order_id(cls, value: Any) -> int | None:
        if value is None:
            return None
        return parse_order_ref(value)

    @field_validator("reason", "description")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped


# ---------------------------------------------------------------------------
# Output schemas: stable business payloads. No ORM objects, no internal DB
# fields, no secrets. Amounts serialize through MoneyModel to JSON floats.
# ---------------------------------------------------------------------------


class OrderItemToolOutput(MoneyModel):
    product_name: str
    quantity: int
    unit_price: Decimal


class OrderToolOutput(MoneyModel):
    order_id: int
    status: OrderStatus
    total_amount: Decimal
    currency: str
    items: list[OrderItemToolOutput]
    created_at: datetime


class LogisticsToolOutput(BaseModel):
    order_id: int
    carrier: str
    tracking_number: str
    status: LogisticsStatus
    estimated_delivery: date | None
    updated_at: datetime


class RefundEligibilityToolOutput(MoneyModel):
    order_id: int
    eligible: bool
    reason: str
    refund_amount: Decimal


class RefundToolOutput(MoneyModel):
    id: int
    order_id: int
    amount: Decimal
    status: RefundStatus
    reason: str | None
    created_at: datetime


class CancelOrderToolOutput(BaseModel):
    order_id: int
    previous_status: OrderStatus
    status: OrderStatus
    message: str


class TicketToolOutput(BaseModel):
    id: int
    order_id: int | None
    category: str
    priority: TicketPriority
    status: TicketStatus
    description: str
    created_at: datetime