"""After-sales execution + verification recorder (Phase 9E).

Phase 9E lets an eligible, treatment-planned after-sales case really EXECUTE the
one business action this project can execute end to end - a refund - through the
EXISTING safe chain, and then VERIFY the business state before completing:

    TreatmentPlan(action=REFUND, eligible=true)
      -> AgentWorkflow Risk Gate (reuses app.risk.RiskEngine / RiskPolicy)
      -> Human Approval when the gate says so (reuses ApprovalService)
      -> Tool Executor (reuses app.tools.ToolExecutor)
      -> RefundService.create_refund (reuses the ONLY refund implementation)
      -> RefundRepository
      -> BusinessVerifier re-reads the refund row
      -> this service records the outcome on the case

This module owns exactly one thing: writing the execution / verification result
back onto the AfterSalesCase. It never executes business logic itself, never
touches RefundService, and never decides a risk level.

Hard invariant (docs/DECISIONS.md Decision 048):
    a case may only become COMPLETED when the business state was re-read and
    verified; a tool response alone can never complete a case.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agent.after_sales import (
    STATUS_COMPLETED,
    AfterSalesExecutionOutcome,
    AfterSalesCaseOutcome,
)
from app.db.enums import RefundStatus
from app.db.repository import RefundRepository
from app.services.after_sales_case_manager import outcome_from_view
from app.services.after_sales_service import AfterSalesService
from app.services.errors import InvalidOperationError

# Case statuses a successful/failed execution is allowed to write.
_ALLOWED_STATUSES = frozenset(
    {"PROCESSING", "PENDING_HUMAN", "COMPLETED", "REJECTED"}
)


class AfterSalesExecutionService:
    """Records Execute -> Verify results on an after-sales case."""

    name = "after_sales_execution"

    def __init__(self, session: Session) -> None:
        self.session = session
        self.cases = AfterSalesService(session)
        self.refunds = RefundRepository(session)

    # -- agent-facing entry point ------------------------------------------

    def record_execution(
        self,
        case_id: str,
        *,
        execution: dict[str, Any],
        status: str,
        verification: dict[str, Any] | None = None,
        refund: dict[str, Any] | None = None,
        error: str | None = None,
        requires_human_review: bool = False,
    ) -> AfterSalesExecutionOutcome:
        """Persist one execution / verification result on the case.

        A COMPLETED status is only accepted together with a passing verification
        AND an existing refund row (re-read here, not taken from a tool
        response). Everything else is recorded as a non-completed outcome.
        """
        if status not in _ALLOWED_STATUSES:
            raise InvalidOperationError(
                f"Unsupported after-sales execution status '{status}'",
                code="INVALID_EXECUTION_STATUS",
            )
        view = self.cases.get_case(case_id)

        refund_block: dict[str, Any] | None = None
        if status == STATUS_COMPLETED:
            if not (isinstance(verification, dict) and verification.get("passed") is True):
                raise InvalidOperationError(
                    "A case can only be COMPLETED after verification passed",
                    code="EXECUTION_NOT_VERIFIED",
                )
            refund_block = self._read_back_refund(refund)
            if refund_block is None:
                raise InvalidOperationError(
                    "Refund record missing after execution; refusing to complete",
                    code="EXECUTION_REFUND_MISSING",
                )

        collected = dict(view.collected_information or {})
        collected["execution"] = dict(execution)
        if verification is not None:
            collected["verification"] = dict(verification)
        if refund_block is not None:
            collected["refund"] = refund_block
        if error:
            collected["execution_error"] = error
        collected["requires_human_review"] = bool(requires_human_review)

        updated = self.cases.update_case(
            case_id, status=status, collected_information=collected
        )
        return AfterSalesExecutionOutcome(
            case=outcome_from_view(
                updated, created=False, order_ref=collected.get("order_ref")
            ),
            execution=dict(execution),
            verification=dict(verification) if verification is not None else None,
            refund=refund_block,
            error=error,
        )

    # -- internals ----------------------------------------------------------

    def _read_back_refund(self, refund: dict[str, Any] | None) -> dict[str, Any] | None:
        """Re-read the authoritative refund row (never trust a tool response)."""
        if not isinstance(refund, dict):
            return None
        refund_id = refund.get("id")
        if refund_id is None:
            return None
        row = self.refunds.get(int(refund_id))
        if row is None:
            return None
        return {
            "id": row.id,
            "ref": f"REFUND-{row.id}",
            "order_id": row.order_id,
            "user_id": row.user_id,
            "amount": str(row.amount),
            "currency": "CNY",
            "status": _enum_value(row.status),
            "reason": row.reason,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


__all__ = ["AfterSalesExecutionService"]