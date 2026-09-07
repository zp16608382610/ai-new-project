"""Approval service (Phase 5 MVP).

Owns the approval request lifecycle (PENDING -> APPROVED / REJECTED). The
service is the single owner of the state machine:

    PENDING -> APPROVED   (approve)
    PENDING -> REJECTED   (reject)
    APPROVED / REJECTED cannot be resolved again.

The approval row is bound to the ORIGINAL ToolRequest: tool_name +
tool_arguments are persisted at creation time. Resuming executes this exact
snapshot (docs/RISK_CONTROL.md), never a re-planned or re-generated call.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.db.enums import ApprovalStatus
from app.db.models import ApprovalRequest
from app.db.repository import ApprovalRepository
from app.services.errors import ConflictError, NotFoundError


@dataclass(frozen=True)
class ApprovalView:
    """Stable, ORM-free representation of one approval request."""

    id: int
    request_id: str
    tool_name: str
    tool_arguments: dict[str, Any]
    user_id: int | None
    risk_level: str
    reason: str | None
    status: str
    created_at: datetime | None = None
    resolved_at: datetime | None = None
    resolved_by: str | None = None

    @classmethod
    def from_model(cls, row: ApprovalRequest) -> "ApprovalView":
        return cls(
            id=row.id,
            request_id=row.request_id,
            tool_name=row.tool_name,
            tool_arguments=dict(row.tool_arguments or {}),
            user_id=row.user_id,
            risk_level=row.risk_level,
            reason=row.reason,
            status=row.status.value,
            created_at=row.created_at,
            resolved_at=row.resolved_at,
            resolved_by=row.resolved_by,
        )


class ApprovalService:
    """PENDING approval requests + their resolve state machine."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.approvals = ApprovalRepository(session)

    def create(
        self,
        *,
        request_id: str,
        tool_name: str,
        tool_arguments: dict[str, Any],
        risk_level: str,
        reason: str,
        user_id: int | None = None,
    ) -> ApprovalView:
        """Persist a PENDING approval bound to the original ToolRequest."""
        row = self.approvals.create(
            request_id=request_id,
            tool_name=tool_name,
            tool_arguments=dict(tool_arguments or {}),
            user_id=user_id,
            risk_level=risk_level,
            reason=reason,
            status=ApprovalStatus.PENDING,
        )
        self.session.commit()
        return ApprovalView.from_model(row)

    def get(self, approval_id: int) -> ApprovalView:
        row = self.approvals.get(approval_id)
        if row is None:
            raise NotFoundError("Approval request not found", code="APPROVAL_NOT_FOUND")
        return ApprovalView.from_model(row)

    def list_pending(self, *, limit: int = 100) -> list[ApprovalView]:
        return [ApprovalView.from_model(row) for row in self.approvals.get_pending(limit=limit)]

    def approve(
        self, approval_id: int, resolved_by: str | None = None
    ) -> ApprovalView:
        return self._resolve(approval_id, ApprovalStatus.APPROVED, resolved_by)

    def reject(
        self, approval_id: int, resolved_by: str | None = None
    ) -> ApprovalView:
        return self._resolve(approval_id, ApprovalStatus.REJECTED, resolved_by)

    # ---- private ---------------------------------------------------------

    def _resolve(
        self, approval_id: int, new_status: ApprovalStatus, resolved_by: str | None
    ) -> ApprovalView:
        row = self.approvals.get(approval_id)
        if row is None:
            raise NotFoundError("Approval request not found", code="APPROVAL_NOT_FOUND")
        if row.status is not ApprovalStatus.PENDING:
            raise ConflictError(
                "Approval has already been resolved",
                code="APPROVAL_ALREADY_RESOLVED",
            )
        row.status = new_status
        row.resolved_at = datetime.now(timezone.utc)
        row.resolved_by = resolved_by
        self.session.commit()
        return ApprovalView.from_model(row)
