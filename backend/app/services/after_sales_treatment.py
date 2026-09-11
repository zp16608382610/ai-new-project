"""After-sales treatment planning + ticket registration (Phase 9D).

    EligibilityResult (9C, deterministic)
      -> TreatmentPlan            (pure rules, app.after_sales.treatment)
      -> Ticket creation          (existing TicketService -> Repository -> DB)
      -> case.collected_information["treatment_plan"] + ticket block
      -> case stays PROCESSING (execution belongs to Phase 9E)

Boundaries (see docs/DECISIONS.md Decision 047):
    - NO business action is executed here: no create_refund, no cancel_order,
      no exchange, no repair. The plan only says what the next phase must do.
    - the ticket is created through the existing TicketService (never by
      writing the DB directly), and only for a case whose deterministic
      eligibility is True.
    - ticket creation is idempotent per case: a retried workflow run reuses the
      existing ticket instead of creating a second one.
    - a creation failure is reported as a failure (never as "ticket created")
      and never fabricates a ticket id.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from app.after_sales.eligibility import CaseFacts
from app.after_sales.treatment import (
    EligibilitySummary,
    TreatmentPlan,
    TreatmentPlanner,
    build_ticket_description,
)
from app.agent.after_sales import (
    AfterSalesCaseOutcome,
    AfterSalesTreatmentOutcome,
)
from app.db.enums import TicketPriority
from app.db.repository import TicketRepository
from app.services.after_sales_case_manager import outcome_from_view
from app.services.after_sales_service import AfterSalesService
from app.services.ticket_service import TicketService

logger = logging.getLogger(__name__)


class AfterSalesTreatmentService:
    """Deterministic treatment plan + idempotent after-sales ticket registration."""

    name = "after_sales_treatment"

    def __init__(
        self,
        session: Session,
        *,
        planner: TreatmentPlanner | None = None,
        ticket_service: TicketService | None = None,
    ) -> None:
        self.session = session
        self.cases = AfterSalesService(session)
        self.tickets = TicketRepository(session)
        self.ticket_service = ticket_service or TicketService(session)
        self.planner = planner or TreatmentPlanner()

    # -- agent-facing entry point ------------------------------------------

    def plan_and_register(
        self, case: AfterSalesCaseOutcome, eligibility: dict[str, Any]
    ) -> AfterSalesTreatmentOutcome:
        """Plan the standard treatment and create/reuse the after-sales ticket."""
        summary = EligibilitySummary.from_dict(eligibility)
        facts = _case_facts(case)
        plan = self.planner.plan(case=facts, eligibility=summary)
        if not plan.executable:
            # Nothing to act on: no ticket is created for a rejected or
            # inconclusive case, and no action is invented for an unclear
            # request. The case keeps whatever state 9C decided.
            return AfterSalesTreatmentOutcome(
                case=case,
                treatment=plan.to_dict(),
                ticket=None,
                ticket_created=False,
            )

        view = self.cases.get_case(case.case_id)
        existing = self.tickets.list_by_case(view.id)
        if existing:
            # Idempotency: a retried run of the same case reuses its ticket.
            block = _ticket_block(existing[0], created=False, count=len(existing))
            plan_dict = self._persist_plan(case.case_id, plan, block)
            return self._outcome(case, plan_dict, block, ticket_created=False)

        try:
            created = self.ticket_service.create_ticket(
                user_id=case.user_id,
                category=plan.ticket_category,
                description=build_ticket_description(
                    case=facts, eligibility=summary, plan=plan
                ),
                priority=TicketPriority.MEDIUM,
                order_id=case.order_id,
                case_id=view.id,
            )
        except Exception as exc:  # operational failure, never a silent success
            logger.warning(
                "after-sales ticket creation failed: %s", type(exc).__name__
            )
            error = f"{type(exc).__name__}: {exc}"
            plan_dict = self._persist_plan(case.case_id, plan, None, error=error)
            return self._outcome(
                case, plan_dict, None, ticket_created=False, error=error
            )

        count = len(self.tickets.list_by_case(view.id))
        block = _ticket_block(created, created=True, count=count)
        plan_dict = self._persist_plan(case.case_id, plan, block)
        return self._outcome(case, plan_dict, block, ticket_created=True)

    # -- persistence -------------------------------------------------------

    def _persist_plan(
        self,
        case_id: str,
        plan: TreatmentPlan,
        ticket_block: dict[str, Any] | None,
        *,
        error: str | None = None,
    ) -> dict[str, Any]:
        """Store the plan on the case (no dedicated table, no second model)."""
        view = self.cases.get_case(case_id)
        collected = dict(view.collected_information or {})
        data = dict(plan.to_dict())
        data["ticket"] = dict(ticket_block) if ticket_block else None
        data["ticket_registered"] = ticket_block is not None
        if error is not None:
            data["ticket_error"] = error
        collected["treatment_plan"] = data
        self.cases.update_case(case_id, collected_information=collected)
        return data

    def _outcome(
        self,
        case: AfterSalesCaseOutcome,
        treatment: dict[str, Any],
        ticket: dict[str, Any] | None,
        *,
        ticket_created: bool,
        error: str | None = None,
    ) -> AfterSalesTreatmentOutcome:
        updated = self.cases.get_case(case.case_id)
        return AfterSalesTreatmentOutcome(
            case=outcome_from_view(updated, created=False, order_ref=case.order_ref),
            treatment=treatment,
            ticket=ticket,
            ticket_created=ticket_created,
            error=error,
        )


def _case_facts(case: AfterSalesCaseOutcome) -> CaseFacts:
    """Project the agent-layer outcome onto the pure planner's input."""
    return CaseFacts(
        case_id=case.case_id,
        user_id=case.user_id,
        case_type=case.case_type,
        requested_action=case.requested_action,
        problem_description=case.problem_description,
        order_ref=case.order_ref,
        order_id=case.order_id,
    )


def _ticket_block(ticket: Any, *, created: bool, count: int) -> dict[str, Any]:
    """Serializable ticket block for the API/trace (never a raw ORM object)."""
    ticket_id = ticket.id
    return {
        "id": ticket_id,
        "ref": f"TICKET-{ticket_id}",
        "case_id": ticket.case_id,
        "category": str(ticket.category),
        "priority": str(_enum_value(ticket.priority)),
        "status": str(_enum_value(ticket.status)),
        "created": bool(created),
        "count": int(count),
    }


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


__all__ = ["AfterSalesTreatmentService"]
