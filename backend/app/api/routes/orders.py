"""Order & logistics read + cancellation endpoints.

路由层职责:参数绑定、调用 Service、返回响应模型。不含业务规则、不直接访问数据库。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.enums import OrderStatus
from app.db.session import get_db
from app.schemas.common import ErrorOut
from app.schemas.order import LogisticsOut, OrderOut
from app.schemas.refund import CancelOrderResponse
from app.services.order_service import OrderService

ERRORS = {
    404: {"model": ErrorOut, "description": "Order / logistics record not found"},
    409: {"model": ErrorOut, "description": "Conflicting operation (already cancelled / active refund)"},
    422: {"model": ErrorOut, "description": "Business rule violation"},
}

order_router = APIRouter(prefix="/orders", tags=["orders"])
logistics_router = APIRouter(prefix="/orders", tags=["logistics"])


def _service(db: Session = Depends(get_db)) -> OrderService:
    return OrderService(db)


@order_router.get(
    "/{order_id}",
    response_model=OrderOut,
    responses=ERRORS,
    summary="Get order",
    description="Return order aggregate: buyer info, items, total amount, status and timestamps.",
)
def get_order(order_id: int, service: OrderService = Depends(_service)) -> OrderOut:
    return service.get_order(order_id)


@order_router.post(
    "/{order_id}/cancel",
    response_model=CancelOrderResponse,
    responses=ERRORS,
    summary="Cancel order",
    description="Cancel a PENDING / PAID / SHIPPED order when no active refund exists.",
)
def cancel_order(order_id: int, service: OrderService = Depends(_service)) -> CancelOrderResponse:
    return service.cancel_order(order_id)


@logistics_router.get(
    "/{order_id}/logistics",
    response_model=LogisticsOut,
    responses=ERRORS,
    summary="Get order logistics",
    description="Return the latest logistics record of an order.",
)
def get_logistics(order_id: int, service: OrderService = Depends(_service)) -> LogisticsOut:
    return service.get_logistics(order_id)