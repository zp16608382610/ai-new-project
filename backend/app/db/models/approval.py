"""approval_requests table (Phase 5 MVP).

Minimal human-approval record bound to the ORIGINAL ToolRequest:
id / request_id / tool_name / tool_arguments / user_id / risk_level / reason /
status (PENDING / APPROVED / REJECTED) / created_at / resolved_at / resolved_by.

tool_arguments is the frozen snapshot of the original planned call. Approving
resumes exactly this snapshot; no LLM re-generation and no re-planning happens
after approval (see docs/RISK_CONTROL.md).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.sqltypes import Enum as SAEnum

from app.db.base import Base
from app.db.enums import ApprovalStatus


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    tool_name: Mapped[str] = mapped_column(String(100), nullable=False)
    tool_arguments: Mapped[dict] = mapped_column(JSON, nullable=False)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), index=True, nullable=True
    )
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(
            ApprovalStatus,
            name="approval_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        default=ApprovalStatus.PENDING,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
