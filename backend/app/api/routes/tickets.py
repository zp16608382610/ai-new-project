"""Support ticket endpoints."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.common import ErrorOut
from app.schemas.ticket import TicketCreateRequest, TicketOut
from app.services.ticket_service import TicketService

router = APIRouter(prefix="/tickets", tags=["tickets"])


def _service(db: Session = Depends(get_db)) -> TicketService:
    return TicketService(db)


@router.post(
    "",
    response_model=TicketOut,
    status_code=201,
    responses={404: {"model": ErrorOut, "description": "User or order not found"}},
    summary="Create support ticket",
    description="Create a support ticket; validates that the referenced user and order exist.",
)
def create_ticket(
    payload: TicketCreateRequest,
    service: TicketService = Depends(_service),
) -> TicketOut:
    return service.create_ticket(
        user_id=payload.user_id,
        order_id=payload.order_id,
        category=payload.category,
        priority=payload.priority,
        description=payload.description,
    )