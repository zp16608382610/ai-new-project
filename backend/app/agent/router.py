"""Workflow routing (Phase 4A).

Intent != Route is a hard design rule:

    Intent  = what the user wants            (state.Intent)
    Route   = which pipeline step runs next  (state.Route)

The router ONLY decides the next step; it never contains order / refund /
RAG business logic. Entity sufficiency is a routing concern: an order-bound
intent without an explicit order reference must route to CLARIFY instead of
guessing an order (see the "no guessing" rule and Decision 023).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.agent.entities import ExtractedEntities
from app.agent.state import Intent, Route

# Default intent -> route map (Intent and Route remain separate concepts:
# REFUND_INQUIRY routes to RAG while REFUND_REQUEST routes to REFUND_TOOL).
_INTENT_TO_ROUTE = {
    Intent.KNOWLEDGE_QA: Route.RAG,
    Intent.REFUND_INQUIRY: Route.RAG,
    Intent.ORDER_STATUS: Route.ORDER_TOOL,
    Intent.LOGISTICS_TRACKING: Route.LOGISTICS_TOOL,
    Intent.REFUND_REQUEST: Route.REFUND_TOOL,
    Intent.CANCEL_ORDER: Route.CANCEL_TOOL,
    Intent.CREATE_TICKET: Route.TICKET_TOOL,
    Intent.UNSUPPORTED: Route.ESCALATE,
    Intent.AMBIGUOUS: Route.CLARIFY,
}

# Routes that require one explicit order id in the message.
_ORDER_ID_ONLY_ROUTES = {
    Route.ORDER_TOOL,
    Route.REFUND_TOOL,
    Route.CANCEL_TOOL,
}


@dataclass(frozen=True)
class RouteDecision:
    """Router output: the next pipeline step plus a human-readable reason."""

    route: Route
    reason: str


class WorkflowRouter(Protocol):
    """Interface implemented by the rule router and future model routers."""

    def decide(
        self,
        intent: Intent,
        entities: ExtractedEntities | None,
        *,
        user_id: int | None = None,
        user_message: str = "",
    ) -> RouteDecision:
        """Map an intent (+ extracted entities) to the next pipeline step."""
        ...


class RuleBasedRouter:
    """Deterministic intent -> route mapper (no business logic inside)."""

    name = "rule_based"

    def decide(
        self,
        intent: Intent,
        entities: ExtractedEntities | None,
        *,
        user_id: int | None = None,
        user_message: str = "",
    ) -> RouteDecision:
        if intent is Intent.AMBIGUOUS:
            return RouteDecision(
                Route.CLARIFY, "AMBIGUOUS intent: ask the user to disambiguate."
            )
        if intent is Intent.UNSUPPORTED:
            return RouteDecision(
                Route.ESCALATE,
                "UNSUPPORTED intent: out of automated scope, escalate to a human.",
            )

        route = _INTENT_TO_ROUTE.get(intent)
        if route is None:
            return RouteDecision(Route.CLARIFY, f"Unknown intent {intent}; clarify.")

        if route in (Route.RAG, Route.ESCALATE, Route.CLARIFY):
            return RouteDecision(route, f"{intent.value} maps to {route.value}.")

        if route is Route.LOGISTICS_TOOL:
            # Logistics may run on an order reference OR a tracking number.
            has_reference = self._has_order_reference(entities) or bool(
                entities and entities.tracking_number
            )
            if not has_reference:
                return RouteDecision(
                    Route.CLARIFY,
                    "LOGISTICS_TRACKING requires an order id or a tracking number.",
                )
            return RouteDecision(
                Route.LOGISTICS_TOOL, "LOGISTICS_TRACKING -> LOGISTICS_TOOL."
            )

        if route in _ORDER_ID_ONLY_ROUTES:
            missing = self._missing_order_reference(intent, entities)
            if missing:
                return RouteDecision(Route.CLARIFY, missing)

        if route is Route.TICKET_TOOL:
            if user_id is None or not (user_message or "").strip():
                return RouteDecision(
                    Route.CLARIFY,
                    "CREATE_TICKET requires a known user identity and a description.",
                )
            return RouteDecision(Route.TICKET_TOOL, "CREATE_TICKET -> TICKET_TOOL.")

        return RouteDecision(route, f"{intent.value} -> {route.value}.")

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _has_order_reference(entities: ExtractedEntities | None) -> bool:
        return bool(entities and entities.order_id and not entities.has_multiple_order_ids)

    @classmethod
    def _missing_order_reference(
        cls, intent: Intent, entities: ExtractedEntities | None
    ) -> str | None:
        if cls._has_order_reference(entities):
            return None
        if entities and entities.has_multiple_order_ids:
            return (
                f"{intent.value} needs one explicit order, but multiple order "
                "references were found: ask the user which order."
            )
        return (
            f"{intent.value} requires an explicit order id; the message does "
            "not provide one - ask the user instead of guessing."
        )