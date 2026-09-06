"""Refund endpoints: eligibility check and refund request creation.

金额由 Service 从订单权威数据推导,客户端不允许指定金额。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.common import ErrorOut
from app.schemas.refund import (
    RefundCreateRequest,
    RefundEligibilityRequest,
    RefundEligibilityResponse,
    RefundOut,
)
from app.services.refund_service import RefundService

router = APIRouter(prefix="/refunds", tags=["refunds"])

ERRORS = {
    404: {"model": ErrorOut, "description": "Order not found"},
    409: {"model": ErrorOut, "description": "Duplicate / already refunded"},
    422: {"model": ErrorOut, "description": "Refund not eligible"},
}


def _service(db: Session = Depends(get_db)) -> RefundService:
    return RefundService(db)


@router.post(
    "/check-eligibility",
    response_model=RefundEligibilityResponse,
    responses=ERRORS,
    summary="Check refund eligibility",
    description="Service-layer eligibility decision; the client never decides refund rules.",
)
def check_eligibility(
    payload: RefundEligibilityRequest,
    service: RefundService = Depends(_service),
) -> RefundEligibilityResponse:
    return service.check_eligibility(payload.order_id)


@router.post(
    "",
    response_model=RefundOut,
    status_code=201,
    responses=ERRORS,
    summary="Create refund request",
    description=(
        "Create a PENDING refund request. Amount is derived from the authoritative "
        "order total; duplicate or ineligible requests are rejected."
    ),
)
def create_refund(
    payload: RefundCreateRequest,
    service: RefundService = Depends(_service),
) -> RefundOut:
    return service.create_refund(payload.order_id, reason=payload.reason)