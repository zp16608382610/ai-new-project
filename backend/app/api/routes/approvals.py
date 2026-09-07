"""Approval endpoints (Phase 5 MVP).

Minimal human-in-the-loop surface:

    GET  /api/v1/approvals                     -> pending approvals
    POST /api/v1/approvals/{id}/approve        -> PENDING -> APPROVED
    POST /api/v1/approvals/{id}/reject         -> PENDING -> REJECTED

Resolving an already resolved approval returns 409 APPROVAL_ALREADY_RESOLVED.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.approval import ApprovalOut, ApprovalResolveRequest
from app.schemas.common import ErrorOut
from app.services.approval_service import ApprovalService

router = APIRouter(prefix="/approvals", tags=["approvals"])

ERRORS = {
    404: {"model": ErrorOut, "description": "Approval request not found"},
    409: {"model": ErrorOut, "description": "Approval already resolved"},
}


def _service(db: Session = Depends(get_db)) -> ApprovalService:
    return ApprovalService(db)


@router.get(
    "",
    response_model=list[ApprovalOut],
    summary="List pending approvals",
    description="Return approval requests that still wait for a human decision.",
)
def list_pending(service: ApprovalService = Depends(_service)) -> list[ApprovalOut]:
    return _to_out_list(service.list_pending())


@router.post(
    "/{approval_id}/approve",
    response_model=ApprovalOut,
    responses=ERRORS,
    summary="Approve an approval request",
    description="Transition PENDING -> APPROVED. Approved requests cannot be resolved again.",
)
def approve(
    approval_id: int,
    payload: ApprovalResolveRequest | None = None,
    service: ApprovalService = Depends(_service),
) -> ApprovalOut:
    resolved_by = payload.resolved_by if payload is not None else None
    return _to_out(service.approve(approval_id, resolved_by=resolved_by))


@router.post(
    "/{approval_id}/reject",
    response_model=ApprovalOut,
    responses=ERRORS,
    summary="Reject an approval request",
    description="Transition PENDING -> REJECTED. Rejected requests cannot be resolved again.",
)
def reject(
    approval_id: int,
    payload: ApprovalResolveRequest | None = None,
    service: ApprovalService = Depends(_service),
) -> ApprovalOut:
    resolved_by = payload.resolved_by if payload is not None else None
    return _to_out(service.reject(approval_id, resolved_by=resolved_by))


def _to_out(view) -> ApprovalOut:
    return ApprovalOut(
        id=view.id,
        request_id=view.request_id,
        tool_name=view.tool_name,
        tool_arguments=view.tool_arguments,
        user_id=view.user_id,
        risk_level=view.risk_level,
        reason=view.reason,
        status=view.status,
        created_at=view.created_at,
        resolved_at=view.resolved_at,
        resolved_by=view.resolved_by,
    )


def _to_out_list(views) -> list[ApprovalOut]:
    return [_to_out(view) for view in views]
