"""Support ticket API schemas."""
from datetime import datetime

from pydantic import BaseModel, Field

from app.db.enums import TicketPriority, TicketStatus
from app.db.models import Ticket


class TicketCreateRequest(BaseModel):
    """Create a support ticket (user_id required; order_id optional)."""

    user_id: int = Field(gt=0)
    order_id: int | None = Field(default=None, gt=0)
    category: str = Field(min_length=1, max_length=50)
    priority: TicketPriority = TicketPriority.MEDIUM
    description: str = Field(min_length=1, max_length=5000)


class TicketOut(BaseModel):
    """Support ticket returned by the API."""

    id: int
    user_id: int
    order_id: int | None
    category: str
    priority: TicketPriority
    status: TicketStatus
    description: str
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_ticket(cls, ticket: Ticket) -> "TicketOut":
        return cls(
            id=ticket.id,
            user_id=ticket.user_id,
            order_id=ticket.order_id,
            category=ticket.category,
            priority=ticket.priority,
            status=ticket.status,
            description=ticket.description,
            created_at=ticket.created_at,
            updated_at=ticket.updated_at,
        )