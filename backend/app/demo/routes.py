"""Demo-facing endpoints (Phase 7A).

Thin HTTP surface on top of the REAL AgentWorkflow / RiskEngine / ApprovalService.
These endpoints exist so the Chat / Console UI can drive the agent; they add no
business rules and no UI-side decision logic.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.demo.payloads import zh_label
from app.demo.service import (
    finalize_approval,
    get_run,
    list_session_runs,
    run_chat,
)
from app.demo.store import get_store
from app.db.session import get_db
from app.mcp.client import MCPClient
from app.services.approval_service import ApprovalService
from app.services.order_service import OrderService
from app.tools.definitions import parse_order_ref

router = APIRouter(prefix="/demo", tags=["demo"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    user_id: int = Field(default=1, ge=1)
    session_id: str | None = None
    request_id: str | None = None
    user_confirmed: bool | None = None


class ResolveRequest(BaseModel):
    resolved_by: str | None = Field(default="demo-operator", max_length=100)


def _order_ref(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text.upper() if text.upper().startswith("ORD") else f"ORD-{text}"


def _approval_item(session: Session, view) -> dict[str, Any]:
    """Approval row + live order context (for the Console queue/detail)."""
    order_context: dict[str, Any] | None = None
    expected_amount: str | None = None
    raw = (view.tool_arguments or {}).get("order_id")
    if raw is not None:
        try:
            order_id = parse_order_ref(raw)
            order = OrderService(session).get_order(order_id)
            order_context = {
                "order_ref": f"ORD-{order.id}",
                "status": order.status.value,
                "status_label": zh_label(order.status.value),
                "total_amount": str(order.total_amount),
                "currency": order.currency,
                "items_count": len(order.items),
            }
            expected_amount = str(order.total_amount)
        except Exception:
            order_context = None
    return {
        "id": view.id,
        "request_id": view.request_id,
        "user_id": view.user_id,
        "tool_name": view.tool_name,
        "action": view.tool_name,
        "order_ref": _order_ref((view.tool_arguments or {}).get("order_id")),
        "order": order_context,
        "expected_amount": expected_amount,
        "risk_level": view.risk_level,
        "reason": view.reason,
        "status": view.status,
        "created_at": view.created_at.isoformat() if view.created_at else None,
        "resolved_at": view.resolved_at.isoformat() if view.resolved_at else None,
        "resolved_by": view.resolved_by,
    }


@router.post("/chat", summary="Run one chat message through the agent")
def chat(payload: ChatRequest, db: Session = Depends(get_db)) -> dict[str, Any]:
    return run_chat(
        db,
        get_store(),
        message=payload.message,
        user_id=payload.user_id,
        session_id=payload.session_id,
        request_id=payload.request_id,
        user_confirmed=payload.user_confirmed,
    )


@router.get("/runs/{request_id}", summary="Get one demo agent run")
def run_detail(request_id: str) -> dict[str, Any]:
    run = get_run(get_store(), request_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    return run


@router.get("/sessions/{session_id}/runs", summary="List runs of one chat session")
def session_runs(session_id: str) -> list[dict[str, Any]]:
    return list_session_runs(get_store(), session_id)


@router.get("/approvals", summary="Console queue: pending + resolved (this process)")
def console_approvals(db: Session = Depends(get_db)) -> dict[str, Any]:
    service = ApprovalService(db)
    pending = [_approval_item(db, view) for view in service.list_pending()]
    store = get_store()
    resolved = [dict(item) for item in store.resolved()]
    return {"pending": pending, "resolved": resolved}


@router.get("/approvals/{approval_id}", summary="Approval detail for the Console")
def approval_detail(approval_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        view = ApprovalService(db).get(approval_id)
    except Exception as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    detail = _approval_item(db, view)
    store = get_store()
    resolution = store.find_by_approval(approval_id)
    if resolution is not None:
        detail["resolution"] = resolution.get("approval_resolution")
        detail["run"] = resolution
    return detail


@router.post("/approvals/{approval_id}/approve", summary="Approve + resume the agent")
def approve(approval_id: int, payload: ResolveRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _resolve(db, approval_id, approved=True, resolved_by=(payload.resolved_by if payload else "demo-operator"))


@router.post("/approvals/{approval_id}/reject", summary="Reject (no execution)")
def reject(approval_id: int, payload: ResolveRequest | None = None, db: Session = Depends(get_db)) -> dict[str, Any]:
    return _resolve(db, approval_id, approved=False, resolved_by=(payload.resolved_by if payload else "demo-operator"))


def _resolve(db: Session, approval_id: int, *, approved: bool, resolved_by: str) -> dict[str, Any]:
    run = finalize_approval(
        db,
        get_store(),
        approval_id,
        approved=approved,
        resolved_by=resolved_by,
    )
    try:
        view = ApprovalService(db).get(approval_id)
        approval = _approval_item(db, view)
    except Exception:
        approval = None
    return {"approval": approval, "run": run}
