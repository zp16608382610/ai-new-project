"""Shared API response schemas."""
from decimal import Decimal

from pydantic import BaseModel, field_serializer

MONEY_FIELDS = ("total_amount", "unit_price", "amount", "refund_amount")


class MoneyModel(BaseModel):
    """Base model that serializes Decimal money fields as JSON numbers (float)."""

    @field_serializer(*MONEY_FIELDS, check_fields=False)
    def _serialize_decimal(self, value: Decimal) -> float:
        return float(value)


class ErrorOut(BaseModel):
    """Consistent API error envelope used by error responses."""

    status: str = "error"
    code: str
    detail: str