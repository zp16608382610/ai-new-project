"""Ticket service: validates referenced entities and creates support tickets.

Phase 9D adds an optional ``case_id`` (the after-sales case row primary key):
when it is given the after-sales case must exist, so a ticket can never point
at a non-existent case. ``case_id`` stays optional because ordinary support
tickets have no case.
"""
from sqlalchemy.orm import Session

from app.db.enums import TicketPriority
from app.db.models import Ticket
from app.db.repository import (
    AfterSalesCaseRepository,
    OrderRepository,
    TicketRepository,
    UserRepository,
)
from app.schemas.ticket import TicketOut
from app.services.errors import NotFoundError


class TicketService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.orders = OrderRepository(session)
        self.tickets = TicketRepository(session)
        self.cases = AfterSalesCaseRepository(session)

    def create_ticket(
        self,
        user_id: int,
        category: str,
        description: str,
        priority: TicketPriority,
        order_id: int | None = None,
        case_id: int | None = None,
    ) -> TicketOut:
        user = self.users.get(user_id)
        if user is None:
            raise NotFoundError("User not found", code="USER_NOT_FOUND")

        if order_id is not None:
            order = self.orders.get(order_id)
            if order is None:
                raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")

        if case_id is not None and self.cases.get(case_id) is None:
            raise NotFoundError("After-sales case not found", code="CASE_NOT_FOUND")

        ticket = Ticket(
            user_id=user_id,
            order_id=order_id,
            case_id=case_id,
            category=category,
            priority=priority,
            description=description,
        )
        self.session.add(ticket)
        self.session.commit()
        return TicketOut.from_ticket(ticket)
