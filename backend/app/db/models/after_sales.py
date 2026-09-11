"""after_sales_cases table (Phase 9A).

One row = one complete after-sales handling case: what the customer reported,
what they asked for, what the AI already collected, what is still missing and
where the case currently stands.

Boundary (Phase 9A): this is a DATA model only. It does not decide refund
eligibility, does not compute amounts, does not call the Agent / Risk /
Approval layers and does not execute any business operation. Later Phase 9
steps read and write cases through AfterSalesService.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import Enum as SAEnum

from app.db.base import Base
from app.db.enums import (
    AfterSalesCaseStatus,
    AfterSalesCaseType,
    AfterSalesRequestedAction,
)
from app.risk.types import RiskLevel


class AfterSalesCase(Base):
    __tablename__ = "after_sales_cases"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Business-facing identifier, stable outside this database (CASE-XXXXXXXX).
    case_id: Mapped[str] = mapped_column(
        String(64), nullable=False, unique=True, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    # Nullable on purpose: a case may start before the order is identified;
    # "order_id" then belongs to missing_information until the user supplies it.
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id"), nullable=True, index=True
    )
    case_type: Mapped[AfterSalesCaseType] = mapped_column(
        SAEnum(
            AfterSalesCaseType,
            name="after_sales_case_type",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=30,
        ),
        default=AfterSalesCaseType.OTHER,
        nullable=False,
    )
    requested_action: Mapped[AfterSalesRequestedAction] = mapped_column(
        SAEnum(
            AfterSalesRequestedAction,
            name="after_sales_requested_action",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        default=AfterSalesRequestedAction.UNKNOWN,
        nullable=False,
    )
    problem_description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[AfterSalesCaseStatus] = mapped_column(
        SAEnum(
            AfterSalesCaseStatus,
            name="after_sales_case_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=30,
        ),
        default=AfterSalesCaseStatus.INFORMATION_COLLECTION,
        nullable=False,
        index=True,
    )
    # Reuses the Phase 5 risk vocabulary (app.risk.types.RiskLevel). Stored as a
    # String(20) exactly like approval_requests.risk_level so the persistence
    # layer does not define a second, conflicting risk enum.
    risk_level: Mapped[str] = mapped_column(
        String(20), default=RiskLevel.LOW.value, nullable=False
    )
    # Free-form structured facts already collected from the user
    # (e.g. {"order_id": "ORD-1001", "damaged": true}).
    collected_information: Mapped[dict[str, Any] | None] = mapped_column(
        JSON, nullable=True
    )
    # Field names still required before the case can continue
    # (e.g. ["order_id", "purchase_proof"]).
    missing_information: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    # AI-written case summary, used when the case is handed to a human.
    ai_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship("User")
    order: Mapped[Order | None] = relationship("Order")
