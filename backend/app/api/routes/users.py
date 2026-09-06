"""User endpoints (read-only order listing)."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.db.enums import OrderStatus
from app.db.session import get_db
from app.schemas.common import ErrorOut
from app.schemas.order import OrderSummaryOut
from app.services.order_service import OrderService

router = APIRouter(prefix="/users", tags=["users"])


def _service(db: Session = Depends(get_db)) -> OrderService:
    return OrderService(db)


@router.get(
    "/{user_id}/orders",
    response_model=list[OrderSummaryOut],
    responses={404: {"model": ErrorOut, "description": "User not found"}},
    summary="List user orders",
    description="Return the orders of a user; optionally filter by order status.",
)
def list_user_orders(
    user_id: int,
    status: OrderStatus | None = Query(default=None),
    service: OrderService = Depends(_service),
) -> list[OrderSummaryOut]:
    return service.get_user_orders(user_id, status=status)