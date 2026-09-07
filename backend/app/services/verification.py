"""Execute -> Verify (Phase 5 MVP).

After a write tool reports SUCCESS, the workflow re-queries the authoritative
business state through the Repository layer. A tool result alone is never
trusted: if the database does not show the expected state the run ends with
VERIFICATION_FAILED instead of reporting success.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.enums import OrderStatus, RefundStatus
from app.db.repository import OrderRepository, RefundRepository
from app.services.errors import VerificationFailedError


class BusinessVerifier:
    """Verifies written business state against the database (refund / cancel)."""

    def __init__(self, session: Session) -> None:
        self.orders = OrderRepository(session)
        self.refunds = RefundRepository(session)

    def verify(self, tool_name: str, data: dict) -> None:
        """Raise VerificationFailedError when authoritative state mismatches."""
        if tool_name == "create_refund":
            self._verify_refund(data)
        elif tool_name == "cancel_order":
            self._verify_cancel(data)
        # Other tools have no write-verification contract in the MVP.

    # ---- private ---------------------------------------------------------

    def _verify_refund(self, data: dict) -> None:
        refund_id = data.get("id")
        row = self.refunds.get(refund_id) if refund_id is not None else None
        if row is None:
            raise VerificationFailedError(
                "Refund record missing after successful execution"
            )
        if int(data.get("order_id", 0)) != row.order_id:
            raise VerificationFailedError(
                "Refund record belongs to a different order than the tool reported"
            )
        if row.status is not RefundStatus.PENDING:
            raise VerificationFailedError(
                f"Refund status is {row.status.value}, expected PENDING"
            )
        order = self.orders.get(row.order_id)
        authoritative = order.total_amount if order is not None else None
        reported = Decimal(str(data.get("amount", "0")))
        if authoritative is None or reported != authoritative:
            raise VerificationFailedError(
                "Refund amount does not match the authoritative order total"
            )

    def _verify_cancel(self, data: dict) -> None:
        order_id = data.get("order_id")
        order = self.orders.get(order_id) if order_id is not None else None
        if order is None:
            raise VerificationFailedError(
                "Order missing after successful cancellation"
            )
        if order.status is not OrderStatus.CANCELLED:
            raise VerificationFailedError(
                f"Order status is {order.status.value}, expected CANCELLED"
            )
