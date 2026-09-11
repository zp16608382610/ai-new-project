"""After-sales case investigation (Phase 9C).

Flow for a case in ELIGIBILITY_CHECK:

    Order Investigation   (OrderService / repositories -> business facts)
      -> Policy Investigation   (existing RetrievalPipeline -> policy evidence)
      -> EligibilityEngine      (pure, deterministic rules)
      -> case status update     (PROCESSING / REJECTED / INFORMATION_COLLECTION)

Boundaries:
    - the agent layer never touches the database: AgentWorkflow only calls this
      service through the ``CaseInvestigatorLike`` protocol;
    - no refund / exchange / repair is executed here, and RefundService /
      Cancel / Risk Gate / HITL / Execute->Verify stay untouched;
    - policy text is only ever read through the existing RAG pipeline
      (``app.retrieval``); no second policy store is created;
    - an investigation failure is never reported as "not eligible".
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.after_sales.eligibility import (
    STATUS_ELIGIBILITY_CHECK,
    STATUS_INFORMATION_COLLECTION,
    BusinessFacts,
    CaseFacts,
    EligibilityEngine,
    EligibilityResult,
    PolicyFacts,
)
from app.after_sales.policy import build_policy_query, extract_policy_facts
from app.agent.after_sales import (
    AfterSalesCaseOutcome,
    AfterSalesInvestigationOutcome,
)
from app.db.enums import LogisticsStatus, OrderStatus
from app.db.repository import LogisticsRepository, OrderRepository, RefundRepository
from app.retrieval.pipeline import RetrievalPipeline
from app.services.after_sales_case_manager import outcome_from_view
from app.services.after_sales_service import AfterSalesService

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]


@dataclass(frozen=True)
class InvestigationOutcome:
    """Internal pair returned by one investigation run."""

    case: AfterSalesCaseOutcome
    eligibility: EligibilityResult
    order_step: JsonDict
    policy_step: JsonDict


class AfterSalesInvestigationService:
    """Order + policy investigation feeding the deterministic eligibility engine."""

    name = "after_sales_investigation"

    def __init__(
        self,
        session: Session,
        *,
        retrieval: RetrievalPipeline,
        engine: EligibilityEngine | None = None,
        reference_time: datetime | None = None,
    ) -> None:
        self.session = session
        self.retrieval = retrieval
        self.engine = engine or EligibilityEngine()
        self.cases = AfterSalesService(session)
        self.orders = OrderRepository(session)
        self.logistics = LogisticsRepository(session)
        self.refunds = RefundRepository(session)
        # Injectable clock: window-based rules must be reproducible in tests and
        # in the evaluation runner (the demo uses the real clock).
        self._reference_time = reference_time

    # -- agent-facing entry point ------------------------------------------

    def investigate(self, case: AfterSalesCaseOutcome) -> AfterSalesInvestigationOutcome:
        """Run order + policy investigation and update the case."""
        case_facts = CaseFacts(
            case_id=case.case_id,
            user_id=case.user_id,
            case_type=case.case_type,
            requested_action=case.requested_action,
            problem_description=case.problem_description,
            order_ref=case.order_ref,
            order_id=case.order_id,
        )
        business, order_step = self._order_investigation(case)
        policy, policy_step = self._policy_investigation(case)
        result = self.engine.evaluate(case=case_facts, business=business, policy=policy)
        result = replace(
            result, investigation={"order": order_step, "policy": policy_step}
        )
        updated = self._apply(result, case)
        return AfterSalesInvestigationOutcome(
            case=updated,
            eligibility=result.to_dict(),
            investigation={"order": order_step, "policy": policy_step},
        )

    # -- order investigation -----------------------------------------------

    def _order_investigation(
        self, case: AfterSalesCaseOutcome
    ) -> tuple[BusinessFacts, JsonDict]:
        ref = case.order_ref or (
            f"ORD-{case.order_id}" if case.order_id is not None else ""
        )
        if case.order_id is None:
            return (
                BusinessFacts(investigation_error="ORDER_NOT_FOUND"),
                {
                    "state": "failed",
                    "detail": f"未在业务系统中找到订单 {ref}",
                    "order_ref": ref,
                    "error": "ORDER_NOT_FOUND",
                },
            )

        order = self.orders.get_full(case.order_id)
        if order is None:
            return (
                BusinessFacts(order_id=case.order_id, investigation_error="ORDER_NOT_FOUND"),
                {
                    "state": "failed",
                    "detail": f"未在业务系统中找到订单 {ref}",
                    "order_ref": ref,
                    "error": "ORDER_NOT_FOUND",
                },
            )

        logistics = self.logistics.get_latest_by_order(order.id)
        delivered_at, delivered_source = self._delivery_reference(order, logistics)
        now = self._now()
        days = None if delivered_at is None else max(0, (now - delivered_at).days)
        items = list(order.items or ())
        items_returnable = bool(items) and all(item.product.returnable for item in items)
        active_refunds = len(self.refunds.list_active_by_order(order.id))

        facts = BusinessFacts(
            order_id=order.id,
            order_exists=True,
            owner_user_id=order.user_id,
            order_status=order.status.value,
            total_amount=str(order.total_amount),
            currency=order.currency,
            items_returnable=items_returnable,
            active_refund_count=active_refunds,
            delivery_reference_at=(
                delivered_at.isoformat() if delivered_at is not None else None
            ),
            delivery_reference_source=delivered_source,
            days_since_delivery=days,
            source="OrderService",
        )
        detail = f"{ref} · {order.status.value}"
        if days is not None:
            detail += f" · 签收参考 {days} 天前"
        return (
            facts,
            {
                "state": "success",
                "detail": detail,
                "order_ref": ref,
                "order_status": order.status.value,
                "days_since_delivery": days,
            },
        )

    @staticmethod
    def _delivery_reference(order, logistics):
        """Best available authoritative sign-off time (source is recorded).

        The mock business model has no ``delivered_at`` column, so the rule is:
        a DELIVERED logistics record wins; otherwise a DELIVERED order falls back
        to its own ``updated_at`` (the transition timestamp kept by the business
        system). No value is ever inferred or invented.
        """
        if logistics is not None and logistics.status == LogisticsStatus.DELIVERED:
            return _as_utc(logistics.updated_at), "logistics.updated_at"
        if order.status == OrderStatus.DELIVERED:
            return _as_utc(order.updated_at), "orders.updated_at"
        return None, None

    # -- policy investigation ----------------------------------------------

    def _policy_investigation(
        self, case: AfterSalesCaseOutcome
    ) -> tuple[PolicyFacts | None, JsonDict]:
        query = build_policy_query(case.case_type, case.requested_action)
        try:
            package = self.retrieval.run(query)
        except Exception as exc:  # a RAG failure must not break the chat flow
            logger.warning("policy retrieval failed: %s", type(exc).__name__)
            return (
                None,
                {
                    "state": "failed",
                    "detail": "政策检索失败,未能取得政策依据",
                    "query": query,
                    "citations": [],
                    "error": type(exc).__name__,
                },
            )
        policy = extract_policy_facts(
            package, action=case.requested_action, case_type=case.case_type
        )
        citations = list(policy.citations)
        detail = (
            f"检索到 {len(citations)} 条政策依据"
            if citations
            else "未检索到相关政策依据"
        )
        if policy.window_days is not None:
            detail += f" · 政策时效 {policy.window_days} 天"
        return (
            policy,
            {
                "state": "success" if citations else "failed",
                "detail": detail,
                "query": query,
                "citations": citations,
                "window_days": policy.window_days,
                "window_citation": policy.window_citation,
                "covers_action": policy.covers_action,
            },
        )

    # -- case update --------------------------------------------------------

    def _apply(
        self, result: EligibilityResult, case: AfterSalesCaseOutcome
    ) -> AfterSalesCaseOutcome:
        collected = dict(case.collected_information or {})
        collected["eligibility"] = result.to_dict()
        missing = (
            list(result.missing_information)
            if result.status == STATUS_INFORMATION_COLLECTION
            else []
        )
        view = self.cases.update_case(
            case.case_id,
            status=result.status,
            collected_information=collected,
            missing_information=missing,
            ai_summary=result.reason,
        )
        return outcome_from_view(view, created=False, order_ref=case.order_ref)

    def _now(self) -> datetime:
        return self._reference_time or datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    """SQLite returns naive datetimes; treat a missing tzinfo as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


__all__ = [
    "AfterSalesInvestigationService",
    "InvestigationOutcome",
    "STATUS_ELIGIBILITY_CHECK",
]
