"""After-sales case orchestration for the agent (Phase 9B).

The agent detects an after-sales handling request (``app.agent.after_sales``);
this manager turns it into persisted case state:

    detect signal -> load the session's active case (if any)
                  -> create or update the AfterSalesCase
                  -> compute collected / missing information
                  -> move to ELIGIBILITY_CHECK when nothing is missing

It NEVER executes a refund / exchange / repair: it only records case state.

Boundaries (enforced by construction):
    - uses ``AfterSalesService`` only - no RefundService, no RiskEngine, no LLM,
      no MCP. Deciding what a case *means* is out of scope for Phase 9B.
    - ``order_id`` (an orders FK) is written only when the referenced order
      really exists in the business system; an unresolved reference such as
      "ORD-1004" stays a string in ``collected_information`` and is never
      promoted to a business fact.
    - the sentence the user sees is built by the demo layer from
      ``missing_information``; this module stays presentation-free.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.agent.after_sales import (
    ACTION_UNKNOWN,
    CASE_TYPE_OTHER,
    MISSING_ORDER_ID,
    MISSING_PROBLEM_DESCRIPTION,
    MISSING_REQUESTED_ACTION,
    STATUS_ELIGIBILITY_CHECK,
    STATUS_INFORMATION_COLLECTION,
    AfterSalesCaseDetector,
    AfterSalesCaseOutcome,
    AfterSalesSignal,
    DeterministicAfterSalesCaseDetector,
)
from app.agent.entities import ExtractedEntities
from app.db.enums import AfterSalesCaseStatus
from app.db.repository import OrderRepository
from app.risk.types import RiskLevel
from app.services.after_sales_service import AfterSalesCaseView, AfterSalesService
from app.services.errors import NotFoundError

logger = logging.getLogger(__name__)

# Only these statuses may be continued by a later turn of the same session. A
# COMPLETED / REJECTED case is history: the next request starts a new case.
_ACTIVE_STATUSES = frozenset(
    {
        AfterSalesCaseStatus.INFORMATION_COLLECTION.value,
        AfterSalesCaseStatus.ELIGIBILITY_CHECK.value,
    }
)

# "ORD-1004", "ord 1004", "1004" -> the numeric part of a business order ref.
_ORDER_REF_RE = re.compile(r"(?i)^(?:ORD-?)?(\d+)$")


class AfterSalesCaseManager:
    """Create / update AfterSalesCases for the agent (Phase 9B)."""

    name = "after_sales_case_manager"

    def __init__(
        self,
        session: Session,
        *,
        detector: AfterSalesCaseDetector | None = None,
    ) -> None:
        self.session = session
        self.service = AfterSalesService(session)
        self.orders = OrderRepository(session)
        self.detector = detector or DeterministicAfterSalesCaseDetector()

    # -- agent-facing entry point ------------------------------------------

    def handle(
        self,
        *,
        user_id: int | None,
        session_id: str | None,
        user_message: str,
        entities: ExtractedEntities | None = None,
        active_case_id: str | None = None,
    ) -> AfterSalesCaseOutcome | None:
        """Upsert the case for one message, or None when it is not a case.

        A message continues an existing case when the user supplies new case
        information (an order reference or a requested action); otherwise the
        detector decides whether it starts one.
        """
        if user_id is None:
            return None
        signal = self.detector.detect(user_message)
        order_ref = self._order_ref(entities)
        active = self._load_active_case(active_case_id, user_id=user_id)
        if not signal.is_case_request and not (
            active is not None and (order_ref or signal.requested_action)
        ):
            return None
        if active is None:
            return self._create_case(
                user_id=user_id,
                signal=signal,
                order_ref=order_ref,
                user_message=user_message,
            )
        return self._update_case(
            active, signal=signal, order_ref=order_ref, user_message=user_message
        )

    def is_case_request(self, user_message: str) -> bool:
        """Whether the message is an after-sales case request (Phase 9F gate).

        The workflow uses this for its business-priority routing: a refund
        request that ALSO reports a product problem must enter the after-sales
        Case chain instead of the legacy one-shot refund flow. This is the very
        detector ``handle`` uses, so the routing decision and the case creation
        can never disagree.
        """
        return bool(self.detector.detect(user_message).is_case_request)

    # -- create / update ----------------------------------------------------

    def _create_case(
        self,
        *,
        user_id: int,
        signal: AfterSalesSignal,
        order_ref: str | None,
        user_message: str,
    ) -> AfterSalesCaseOutcome:
        problem_reported = bool(signal.problem_description)
        problem_description = signal.problem_description or user_message.strip()
        action = signal.requested_action or ACTION_UNKNOWN
        case_type = signal.case_type or CASE_TYPE_OTHER
        missing = self._missing_information(
            order_ref=order_ref,
            requested_action=action,
            problem_reported=problem_reported,
        )
        view = self.service.create_case(
            user_id=user_id,
            order_id=self._resolve_order_id(order_ref),
            case_type=case_type,
            requested_action=action,
            problem_description=problem_description,
            status=self._status_for(missing),
            risk_level=RiskLevel.LOW,
            collected_information=self._collected_information(
                order_ref=order_ref,
                requested_action=signal.requested_action,
                case_type=signal.case_type,
                problem_reported=problem_reported,
            ),
            missing_information=missing,
        )
        return self._outcome(view, created=True, order_ref=order_ref)

    def _update_case(
        self,
        view: AfterSalesCaseView,
        *,
        signal: AfterSalesSignal,
        order_ref: str | None,
        user_message: str,
    ) -> AfterSalesCaseOutcome:
        existing = dict(view.collected_information or {})
        problem_reported = bool(existing.get("problem_reported")) or bool(
            signal.problem_description
        )
        problem_description = signal.problem_description or view.problem_description
        case_type = signal.case_type or view.case_type
        action = signal.requested_action or view.requested_action
        effective_order_ref = order_ref or existing.get("order_ref")
        missing = self._missing_information(
            order_ref=effective_order_ref,
            requested_action=action,
            problem_reported=problem_reported,
        )
        resolved_order_id = self._resolve_order_id(order_ref)
        updated = self.service.update_case(
            view.case_id,
            problem_description=problem_description,
            case_type=case_type,
            requested_action=action,
            order_id=resolved_order_id,
            status=self._status_for(missing),
            collected_information=self._collected_information(
                order_ref=effective_order_ref,
                requested_action=None if action == ACTION_UNKNOWN else action,
                case_type=None if case_type == CASE_TYPE_OTHER else case_type,
                problem_reported=problem_reported,
            ),
            missing_information=missing,
        )
        return self._outcome(updated, created=False, order_ref=effective_order_ref)

    # -- helpers ------------------------------------------------------------

    def _load_active_case(
        self, case_id: str | None, *, user_id: int
    ) -> AfterSalesCaseView | None:
        if not case_id:
            return None
        try:
            view = self.service.get_case(case_id)
        except NotFoundError:
            return None
        if view.user_id != user_id or view.status not in _ACTIVE_STATUSES:
            return None
        return view

    def _resolve_order_id(self, order_ref: str | None) -> int | None:
        """Map a user-given reference onto a real order, or None (no guessing)."""
        if not order_ref:
            return None
        match = _ORDER_REF_RE.match(str(order_ref).strip())
        if match is None:
            return None
        candidate = int(match.group(1))
        return candidate if self.orders.get(candidate) is not None else None

    @staticmethod
    def _order_ref(entities: ExtractedEntities | None) -> str | None:
        if entities is None or entities.has_multiple_order_ids:
            return None
        return entities.order_id

    @staticmethod
    def _collected_information(
        *,
        order_ref: str | None,
        requested_action: str | None,
        case_type: str | None,
        problem_reported: bool,
    ) -> dict[str, Any]:
        collected: dict[str, Any] = {"problem_reported": problem_reported}
        if order_ref:
            collected["order_ref"] = order_ref
        if requested_action:
            collected["requested_action"] = requested_action
        if case_type:
            collected["case_type"] = case_type
        return collected

    @staticmethod
    def _missing_information(
        *,
        order_ref: str | None,
        requested_action: str | None,
        problem_reported: bool,
    ) -> list[str]:
        missing: list[str] = []
        if not order_ref:
            missing.append(MISSING_ORDER_ID)
        if requested_action in (None, ACTION_UNKNOWN):
            missing.append(MISSING_REQUESTED_ACTION)
        if not problem_reported:
            missing.append(MISSING_PROBLEM_DESCRIPTION)
        return missing

    @staticmethod
    def _status_for(missing: list[str]) -> str:
        return STATUS_ELIGIBILITY_CHECK if not missing else STATUS_INFORMATION_COLLECTION

    @staticmethod
    def _outcome(
        view: AfterSalesCaseView, *, created: bool, order_ref: str | None
    ) -> AfterSalesCaseOutcome:
        return outcome_from_view(view, created=created, order_ref=order_ref)


def outcome_from_view(
    view: AfterSalesCaseView, *, created: bool, order_ref: str | None
) -> AfterSalesCaseOutcome:
    """Project one persisted case row onto the agent-layer outcome.

    Module-level so the Phase 9C investigation service reports exactly the same
    case shape to the workflow as the case-management step does.
    """
    collected = dict(view.collected_information or {})
    return AfterSalesCaseOutcome(
        case_id=view.case_id,
        user_id=view.user_id,
        case_type=view.case_type,
        requested_action=view.requested_action,
        problem_description=view.problem_description,
        status=view.status,
        risk_level=view.risk_level,
        missing_information=tuple(view.missing_information or []),
        collected_information=collected,
        order_id=view.order_id,
        order_ref=order_ref or collected.get("order_ref"),
        created=created,
    )


__all__ = ["AfterSalesCaseManager", "outcome_from_view"]
