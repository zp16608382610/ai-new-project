"""Approval API schemas (Phase 5 MVP)."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ApprovalOut(BaseModel):
    """Stable view of one approval request (never an ORM object)."""

    id: int
    request_id: str
    tool_name: str
    tool_arguments: dict[str, Any]
    user_id: int | None
    risk_level: str
    reason: str | None
    status: str
    created_at: datetime | None
    resolved_at: datetime | None
    resolved_by: str | None


class ApprovalResolveRequest(BaseModel):
    """Optional operator identity recorded when an approval is resolved."""

    resolved_by: str | None = Field(default=None, max_length=100)
