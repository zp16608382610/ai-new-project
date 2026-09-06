"""logistics table."""
from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import Enum as SAEnum

from app.db.base import Base
from app.db.enums import LogisticsStatus


class Logistics(Base):
    __tablename__ = "logistics"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False, index=True)
    carrier: Mapped[str] = mapped_column(String(100), nullable=False)
    tracking_number: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    status: Mapped[LogisticsStatus] = mapped_column(
        SAEnum(
            LogisticsStatus,
            name="logistics_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        default=LogisticsStatus.PENDING,
        nullable=False,
    )
    estimated_delivery: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    order: Mapped[Order] = relationship("Order", back_populates="logistics")