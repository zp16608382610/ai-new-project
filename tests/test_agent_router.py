"""Router tests (Phase 4A).

Intent and Route are separate concepts:
    KNOWLEDGE_QA / REFUND_INQUIRY -> RAG
    ORDER_STATUS / LOGISTICS_TRACKING / REFUND_REQUEST / CANCEL_ORDER /
    CREATE_TICKET              -> the matching *TOOL route
    UNSUPPORTED                -> ESCALATE
    AMBIGUOUS                  -> CLARIFY
Order-bound routes require an explicit, single order reference; otherwise
they route to CLARIFY (the agent never guesses an order).
"""
import pytest

from app.agent.entities import ExtractedEntities
from app.agent.router import RouteDecision, RuleBasedRouter
from app.agent.state import Intent, Route


@pytest.fixture()
def router() -> RuleBasedRouter:
    return RuleBasedRouter()


def _entities(order_id: str | None = None, tracking_number: str | None = None) -> ExtractedEntities:
    return ExtractedEntities(
        order_id=order_id,
        tracking_number=tracking_number,
        has_multiple_order_ids=False,
        has_multiple_tracking_numbers=False,
    )


def test_route_decision_schema(router):
    decision = router.decide(Intent.KNOWLEDGE_QA, None)
    assert isinstance(decision, RouteDecision)
    assert decision.route is Route.RAG
    assert isinstance(decision.reason, str) and decision.reason


def test_knowledge_and_refund_inquiry_route_to_rag(router):
    assert router.decide(Intent.KNOWLEDGE_QA, None).route is Route.RAG
    assert router.decide(Intent.REFUND_INQUIRY, None).route is Route.RAG


def test_order_status_route(router):
    assert router.decide(Intent.ORDER_STATUS, _entities("ORD-1001")).route is Route.ORDER_TOOL


def test_logistics_route_with_tracking_only(router):
    decision = router.decide(
        Intent.LOGISTICS_TRACKING, _entities(tracking_number="SF10020002")
    )
    assert decision.route is Route.LOGISTICS_TOOL


def test_refund_request_route_with_order(router):
    decision = router.decide(Intent.REFUND_REQUEST, _entities("ORD-1001"))
    assert decision.route is Route.REFUND_TOOL


def test_cancel_order_route_with_order(router):
    decision = router.decide(Intent.CANCEL_ORDER, _entities("ORD-1001"))
    assert decision.route is Route.CANCEL_TOOL


def test_create_ticket_route_with_user_context(router):
    decision = router.decide(
        Intent.CREATE_TICKET,
        None,
        user_id=1,
        user_message="我要投诉物流异常",
    )
    assert decision.route is Route.TICKET_TOOL


def test_unsupported_routes_to_escalate(router):
    assert router.decide(Intent.UNSUPPORTED, None).route is Route.ESCALATE


def test_ambiguous_routes_to_clarify(router):
    assert router.decide(Intent.AMBIGUOUS, None).route is Route.CLARIFY


@pytest.mark.parametrize(
    "intent",
    [Intent.ORDER_STATUS, Intent.REFUND_REQUEST, Intent.CANCEL_ORDER],
)
def test_order_bound_intent_without_order_clarifies(router, intent):
    decision = router.decide(intent, None)
    assert decision.route is Route.CLARIFY
    assert "order" in decision.reason.lower()


def test_logistics_without_any_reference_clarifies(router):
    decision = router.decide(Intent.LOGISTICS_TRACKING, _entities())
    assert decision.route is Route.CLARIFY


def test_multiple_order_references_clarify(router):
    entities = ExtractedEntities(
        order_id=None,
        tracking_number=None,
        has_multiple_order_ids=True,
        has_multiple_tracking_numbers=False,
    )
    decision = router.decide(Intent.REFUND_REQUEST, entities)
    assert decision.route is Route.CLARIFY


def test_create_ticket_without_user_identity_clarifies(router):
    decision = router.decide(Intent.CREATE_TICKET, None, user_id=None, user_message="我要投诉")
    assert decision.route is Route.CLARIFY


def test_router_is_deterministic(router):
    first = router.decide(Intent.REFUND_REQUEST, _entities("ORD-1001"))
    second = router.decide(Intent.REFUND_REQUEST, _entities("ORD-1001"))
    assert first == second