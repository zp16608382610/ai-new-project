"""tickets table."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import Enum as SAEnum

from app.db.base import Base
from app.db.enums import TicketPriority, TicketStatus


class Ticket(Base):
    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True, index=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    priority: Mapped[TicketPriority] = mapped_column(
        SAEnum(
            TicketPriority,
            name="ticket_priority",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        default=TicketPriority.MEDIUM,
        nullable=False,
    )
    status: Mapped[TicketStatus] = mapped_column(
        SAEnum(
            TicketStatus,
            name="ticket_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        default=TicketStatus.OPEN,
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    user: Mapped[User] = relationship("User", back_populates="tickets")
    order: Mapped[Order | None] = relationship("Order")