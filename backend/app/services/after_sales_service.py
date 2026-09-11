"""After-sales case service (Phase 9A).

Owns the after-sales CASE DATA only:

    create_case / get_case / update_case / list_cases

Deliberately NOT implemented here (later Phase 9 steps):

    - refund eligibility or any monetary decision  -> Phase 9B / 9D
    - exchange / repair execution                  -> Phase 9B / 9D
    - risk classification / human approval         -> reuse Phase 5 layers
    - LLM intent understanding or summarisation    -> Phase 9C

The service never talks to the Agent / LLM / Risk Engine. It only stores what
those layers decide, once they start writing cases.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.enums import (
    AfterSalesCaseStatus,
    AfterSalesCaseType,
    AfterSalesRequestedAction,
)
from app.db.models import AfterSalesCase
from app.db.repository import AfterSalesCaseRepository, OrderRepository, UserRepository
from app.risk.types import RiskLevel
from app.services.errors import ConflictError, InvalidOperationError, NotFoundError


def _coerce(enum_type, value, field: str):
    """Normalise a str/enum value to its enum member, or fail loudly."""
    try:
        return enum_type(value)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in enum_type)
        raise InvalidOperationError(
            f"Invalid {field}: {value!r} (allowed: {allowed})",
            code=f"INVALID_{field.upper()}",
        ) from exc


def _risk_level(value) -> str:
    """Reuse the Phase 5 risk vocabulary and store its plain string value."""
    try:
        return RiskLevel(value).value
    except ValueError as exc:
        allowed = ", ".join(member.value for member in RiskLevel)
        raise InvalidOperationError(
            f"Invalid risk_level: {value!r} (allowed: {allowed})",
            code="INVALID_RISK_LEVEL",
        ) from exc


def _dict_or_none(value, field: str) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise InvalidOperationError(
            f"{field} must be a mapping", code=f"INVALID_{field.upper()}"
        )
    return dict(value)


def _str_list_or_none(value, field: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or any(
        not isinstance(item, str) for item in value
    ):
        raise InvalidOperationError(
            f"{field} must be a list of strings", code=f"INVALID_{field.upper()}"
        )
    return [str(item) for item in value]


@dataclass(frozen=True)
class AfterSalesCaseView:
    """Stable, ORM-free representation of one after-sales case."""

    id: int
    case_id: str
    user_id: int
    order_id: int | None
    case_type: str
    requested_action: str
    problem_description: str
    status: str
    risk_level: str
    collected_information: dict[str, Any] | None
    missing_information: list[str] | None
    ai_summary: str | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_model(cls, row: AfterSalesCase) -> "AfterSalesCaseView":
        return cls(
            id=row.id,
            case_id=row.case_id,
            user_id=row.user_id,
            order_id=row.order_id,
            case_type=row.case_type.value,
            requested_action=row.requested_action.value,
            problem_description=row.problem_description,
            status=row.status.value,
            risk_level=row.risk_level,
            collected_information=(
                dict(row.collected_information)
                if row.collected_information is not None
                else None
            ),
            missing_information=(
                list(row.missing_information)
                if row.missing_information is not None
                else None
            ),
            ai_summary=row.ai_summary,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class AfterSalesService:
    """Minimal CRUD over after-sales cases. No AI, no refund, no risk."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.orders = OrderRepository(session)
        self.cases = AfterSalesCaseRepository(session)

    def create_case(
        self,
        *,
        user_id: int,
        problem_description: str,
        case_type: AfterSalesCaseType | str = AfterSalesCaseType.OTHER,
        requested_action: AfterSalesRequestedAction
        | str = AfterSalesRequestedAction.UNKNOWN,
        order_id: int | None = None,
        status: AfterSalesCaseStatus | str = AfterSalesCaseStatus.INFORMATION_COLLECTION,
        risk_level: RiskLevel | str = RiskLevel.LOW,
        collected_information: dict[str, Any] | None = None,
        missing_information: list[str] | None = None,
        ai_summary: str | None = None,
        case_id: str | None = None,
    ) -> AfterSalesCaseView:
        """Create one case. ``case_id`` is generated when not supplied."""
        if self.users.get(user_id) is None:
            raise NotFoundError("User not found", code="USER_NOT_FOUND")
        if order_id is not None and self.orders.get(order_id) is None:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")

        resolved_case_id = case_id or self._generate_case_id()
        if self.cases.get_by_case_id(resolved_case_id) is not None:
            raise ConflictError(
                "After-sales case already exists", code="CASE_ALREADY_EXISTS"
            )

        row = self.cases.create(
            case_id=resolved_case_id,
            user_id=user_id,
            order_id=order_id,
            case_type=_coerce(AfterSalesCaseType, case_type, "case_type"),
            requested_action=_coerce(
                AfterSalesRequestedAction, requested_action, "requested_action"
            ),
            problem_description=problem_description,
            status=_coerce(AfterSalesCaseStatus, status, "status"),
            risk_level=_risk_level(risk_level),
            collected_information=_dict_or_none(
                collected_information, "collected_information"
            ),
            missing_information=_str_list_or_none(
                missing_information, "missing_information"
            ),
            ai_summary=ai_summary,
        )
        self.session.commit()
        return AfterSalesCaseView.from_model(row)

    def get_case(self, case_id: str) -> AfterSalesCaseView:
        return AfterSalesCaseView.from_model(self._require(case_id))

    def update_case(
        self,
        case_id: str,
        *,
        problem_description: str | None = None,
        case_type: AfterSalesCaseType | str | None = None,
        requested_action: AfterSalesRequestedAction | str | None = None,
        order_id: int | None = None,
        status: AfterSalesCaseStatus | str | None = None,
        risk_level: RiskLevel | str | None = None,
        collected_information: dict[str, Any] | None = None,
        missing_information: list[str] | None = None,
        ai_summary: str | None = None,
    ) -> AfterSalesCaseView:
        """Partial update: only the keyword arguments passed are changed."""
        row = self._require(case_id)

        if order_id is not None and self.orders.get(order_id) is None:
            raise NotFoundError("Order not found", code="ORDER_NOT_FOUND")

        if problem_description is not None:
            row.problem_description = problem_description
        if case_type is not None:
            row.case_type = _coerce(AfterSalesCaseType, case_type, "case_type")
        if requested_action is not None:
            row.requested_action = _coerce(
                AfterSalesRequestedAction, requested_action, "requested_action"
            )
        if order_id is not None:
            row.order_id = order_id
        if status is not None:
            row.status = _coerce(AfterSalesCaseStatus, status, "status")
        if risk_level is not None:
            row.risk_level = _risk_level(risk_level)
        if collected_information is not None:
            row.collected_information = _dict_or_none(
                collected_information, "collected_information"
            )
        if missing_information is not None:
            row.missing_information = _str_list_or_none(
                missing_information, "missing_information"
            )
        if ai_summary is not None:
            row.ai_summary = ai_summary

        self.session.commit()
        return AfterSalesCaseView.from_model(row)

    def list_cases(
        self,
        *,
        user_id: int | None = None,
        status: AfterSalesCaseStatus | str | None = None,
        case_type: AfterSalesCaseType | str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AfterSalesCaseView]:
        rows = self.cases.list_cases(
            user_id=user_id,
            status=(
                _coerce(AfterSalesCaseStatus, status, "status")
                if status is not None
                else None
            ),
            case_type=(
                _coerce(AfterSalesCaseType, case_type, "case_type")
                if case_type is not None
                else None
            ),
            limit=limit,
            offset=offset,
        )
        return [AfterSalesCaseView.from_model(row) for row in rows]

    # ---- private ---------------------------------------------------------

    def _require(self, case_id: str) -> AfterSalesCase:
        row = self.cases.get_by_case_id(case_id)
        if row is None:
            raise NotFoundError("After-sales case not found", code="CASE_NOT_FOUND")
        return row

    @staticmethod
    def _generate_case_id() -> str:
        return f"CASE-{uuid4().hex[:12].upper()}"
