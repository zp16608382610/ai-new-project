"""Phase 9B: after-sales case <-> agent workflow tests.

Spec coverage:
    1. an after-sales request creates a case
    2. the same active case is not created twice
    3. order id extraction
    4. requested action extraction
    5. problem_description persistence
    6. case-type mapping (QUALITY_ISSUE / LOGISTICS_DISPUTE / OTHER)
    7. missing order id -> INFORMATION_COLLECTION
    8. missing requested action -> INFORMATION_COLLECTION
    9. complete information -> ELIGIBILITY_CHECK (no eligibility executed)
   10. several turns update ONE case
   11. non-after-sales requests never create a case
   12. malformed / failing LLM output never produces a dangerous action
   13. a broken case service never breaks the chat flow

Strategy: in-memory SQLite + dev seed + demo orders (ORD-1001..1003, ORD-2001)
driving the REAL AgentWorkflow with the REAL AfterSalesCaseManager. Phase 9B
never executes a refund / exchange / repair, so those assertions are about the
absence of business writes, not about case state alone.
"""
from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.agent.state import AgentResultStatus, AgentState, Intent, Route
from app.agent.workflow import AgentWorkflow
from app.db.base import Base
from app.db.models import AfterSalesCase, Refund
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory
from app.demo.demo_seed import seed_demo_orders
from app.llm.nlu import LLMIntentExtractor
from app.services.after_sales_case_manager import AfterSalesCaseManager
from app.services.after_sales_service import AfterSalesService


@pytest.fixture()
def db_session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = create_session_factory(engine)()
    assert seed_dev_data(session) is True
    seed_demo_orders(session)
    session.commit()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def workflow(db_session) -> AgentWorkflow:
    return AgentWorkflow(case_manager=AfterSalesCaseManager(db_session))


def _cases(db_session) -> AfterSalesService:
    return AfterSalesService(db_session)


def _refund_count(db_session) -> int:
    """Refund rows in the database (the dev seed already has some)."""
    return int(db_session.scalar(select(func.count()).select_from(Refund)) or 0)


# ---- 1 / 2. create + no duplicate ----------------------------------------


def test_after_sales_request_creates_case(workflow, db_session):
    result = workflow.run(
        "req-1", "我的耳机坏了，帮我处理一下。", user_id=1, session_id="s1"
    )

    assert result.intent is Intent.AFTER_SALES_REQUEST
    assert result.route is Route.AFTER_SALES_CASE
    assert result.status is AgentResultStatus.NEEDS_CLARIFICATION
    assert result.needs_clarification is True
    case = result.after_sales_case
    assert case is not None
    assert case["case_id"].startswith("CASE-")
    assert case["created"] is True
    assert case["user_id"] == 1
    assert case["case_type"] == "QUALITY_ISSUE"
    assert case["requested_action"] == "UNKNOWN"
    assert case["problem_description"] == "我的耳机坏了"
    assert case["status"] == "INFORMATION_COLLECTION"
    assert case["risk_level"] == "LOW"
    assert case["missing_information"] == ["order_id", "requested_action"]
    assert case["collected_information"]["problem_reported"] is True
    assert [row.case_id for row in _cases(db_session).list_cases(user_id=1)] == [
        case["case_id"]
    ]


def test_same_active_case_is_not_created_twice(workflow, db_session):
    first = workflow.run(
        "req-1", "我的耳机坏了，帮我处理一下。", user_id=1, session_id="s1"
    )
    case_id = first.after_sales_case["case_id"]

    second = workflow.run(
        "req-2",
        "订单是 ORD-1003，我想换货。",
        user_id=1,
        session_id="s1",
        active_case_id=case_id,
    )

    assert second.after_sales_case["case_id"] == case_id
    assert second.after_sales_case["created"] is False
    assert len(_cases(db_session).list_cases(user_id=1)) == 1
    assert db_session.scalar(select(func.count()).select_from(AfterSalesCase)) == 1


def test_finished_case_is_not_reused(workflow, db_session):
    first = workflow.run(
        "req-1", "我的耳机坏了，帮我处理一下。", user_id=1, session_id="s1"
    )
    case_id = first.after_sales_case["case_id"]
    _cases(db_session).update_case(case_id, status="COMPLETED")

    second = workflow.run(
        "req-2",
        "耳机又坏了，帮我处理一下。",
        user_id=1,
        session_id="s1",
        active_case_id=case_id,
    )

    assert second.after_sales_case["case_id"] != case_id
    assert second.after_sales_case["created"] is True
    assert len(_cases(db_session).list_cases(user_id=1)) == 2


# ---- 3 / 4 / 5 / 6. extraction -------------------------------------------


def test_order_id_is_extracted_from_a_follow_up(workflow):
    first = workflow.run("req-1", "我的耳机坏了", user_id=1, session_id="s1")
    second = workflow.run(
        "req-2",
        "订单是 ORD-1003，我想换货。",
        user_id=1,
        session_id="s1",
        active_case_id=first.after_sales_case["case_id"],
    )

    case = second.after_sales_case
    assert case["order_ref"] == "ORD-1003"
    assert case["order_id"] == 1003  # resolved against the business system
    assert case["collected_information"]["order_ref"] == "ORD-1003"


def test_unknown_order_reference_is_never_promoted_to_a_business_fact(workflow):
    first = workflow.run("req-1", "我的耳机坏了", user_id=1, session_id="s1")
    result = workflow.run(
        "req-2",
        "订单是 ORD-1004，我想换货。",
        user_id=1,
        session_id="s1",
        active_case_id=first.after_sales_case["case_id"],
    )

    case = result.after_sales_case
    assert case["order_ref"] == "ORD-1004"  # what the user actually said
    assert case["order_id"] is None  # that order does not exist -> no FK write
    assert case["status"] == "ELIGIBILITY_CHECK"
    assert case["missing_information"] == []


@pytest.mark.parametrize(
    "message,expected",
    [
        ("耳机坏了，我想换货 ORD-1001", "EXCHANGE"),
        ("耳机坏了，我要维修 ORD-1001", "REPAIR"),
        ("耳机坏了想退款", "REFUND"),
        ("耳机坏了，帮我处理一下", "UNKNOWN"),
    ],
)
def test_requested_action_is_extracted(workflow, message, expected):
    result = workflow.run("req-1", message, user_id=1, session_id="s1")
    assert result.after_sales_case["requested_action"] == expected


def test_problem_description_keeps_the_reported_clause(workflow):
    result = workflow.run(
        "req-1", "我的耳机坏了，帮我处理一下。", user_id=1, session_id="s1"
    )
    assert result.after_sales_case["problem_description"] == "我的耳机坏了"


@pytest.mark.parametrize(
    "message,expected",
    [
        ("这个耳机质量有问题，帮我处理", "QUALITY_ISSUE"),
        ("耳机坏了", "QUALITY_ISSUE"),
        ("少发了一件，帮我处理", "LOGISTICS_DISPUTE"),
        ("我想换货 ORD-1001", "OTHER"),
    ],
)
def test_case_type_mapping(workflow, message, expected):
    result = workflow.run("req-1", message, user_id=1, session_id="s1")
    assert result.after_sales_case["case_type"] == expected


# ---- 7 / 8 / 9. information collection rules ------------------------------


def test_missing_order_id_keeps_information_collection(workflow):
    result = workflow.run("req-1", "耳机坏了，我想换货", user_id=1, session_id="s1")
    case = result.after_sales_case
    assert case["requested_action"] == "EXCHANGE"
    assert case["status"] == "INFORMATION_COLLECTION"
    assert case["missing_information"] == ["order_id"]
    assert result.status is AgentResultStatus.NEEDS_CLARIFICATION


def test_missing_requested_action_keeps_information_collection(workflow):
    result = workflow.run(
        "req-1", "耳机坏了，订单是 ORD-1001", user_id=1, session_id="s1"
    )
    case = result.after_sales_case
    assert case["requested_action"] == "UNKNOWN"
    assert case["status"] == "INFORMATION_COLLECTION"
    assert case["missing_information"] == ["requested_action"]
    assert result.status is AgentResultStatus.NEEDS_CLARIFICATION


def test_missing_problem_description_keeps_information_collection(workflow):
    result = workflow.run("req-1", "我想换货 ORD-1001", user_id=1, session_id="s1")
    case = result.after_sales_case
    assert case["status"] == "INFORMATION_COLLECTION"
    assert case["missing_information"] == ["problem_description"]


def test_complete_information_moves_to_eligibility_check_without_executing(
    workflow, db_session
):
    refunds_before = _refund_count(db_session)
    result = workflow.run(
        "req-1", "耳机坏了，订单是 ORD-1001，我想换货", user_id=1, session_id="s1"
    )

    case = result.after_sales_case
    assert case["status"] == "ELIGIBILITY_CHECK"
    assert case["missing_information"] == []
    assert result.status is AgentResultStatus.SUCCESS
    assert result.tool_requests == ()
    assert _refund_count(db_session) == refunds_before


# ---- 10. multi-turn ------------------------------------------------------


def test_two_turns_update_one_case(workflow, db_session):
    first = workflow.run(
        "req-1", "我的耳机坏了，帮我处理一下。", user_id=1, session_id="s1"
    )
    case_id = first.after_sales_case["case_id"]
    assert first.after_sales_case["missing_information"] == [
        "order_id",
        "requested_action",
    ]

    second = workflow.run(
        "req-2",
        "订单是 ORD-1004，我想换货。",
        user_id=1,
        session_id="s1",
        active_case_id=case_id,
    )

    case = second.after_sales_case
    assert case["case_id"] == case_id
    assert case["order_ref"] == "ORD-1004"
    assert case["requested_action"] == "EXCHANGE"
    assert case["status"] == "ELIGIBILITY_CHECK"
    assert case["missing_information"] == []
    assert case["problem_description"] == "我的耳机坏了"  # kept from turn 1
    assert [row.case_id for row in _cases(db_session).list_cases(user_id=1)] == [case_id]


def test_case_is_scoped_to_the_user(workflow):
    first = workflow.run(
        "req-1", "我的耳机坏了，帮我处理一下。", user_id=1, session_id="s1"
    )

    # Bob must not continue Alice's case even if the id is passed in.
    other = workflow.run(
        "req-2",
        "耳机坏了想换货 ORD-2001",
        user_id=2,
        session_id="s2",
        active_case_id=first.after_sales_case["case_id"],
    )
    assert other.after_sales_case["case_id"] != first.after_sales_case["case_id"]
    assert other.after_sales_case["user_id"] == 2


# ---- 11. non after-sales requests ----------------------------------------


@pytest.mark.parametrize(
    "message,expected_route",
    [
        ("帮我写一份 Python 教程", Route.ESCALATE),
        ("帮我转人工客服", Route.ESCALATE),
        ("帮我查订单 ORD-1001", Route.ORDER_TOOL),
        ("帮我退款 ORD-1003", Route.REFUND_TOOL),
        ("帮我取消订单 ORD-1002", Route.CANCEL_TOOL),
        ("退款需要满足什么条件？", Route.RAG),
    ],
)
def test_non_after_sales_requests_never_create_a_case(
    workflow, db_session, message, expected_route
):
    result = workflow.run("req-x", message, user_id=1, session_id="s1")

    assert result.route is expected_route
    assert result.after_sales_case is None
    assert db_session.scalar(select(func.count()).select_from(AfterSalesCase)) == 0


# ---- 12. LLM safety ------------------------------------------------------


class _GarbageProvider:
    name = "stub-garbage"

    def generate(self, messages, **kwargs) -> str:
        return "I am definitely not the requested JSON object"


class _ExplodingProvider:
    name = "stub-exploding"

    def generate(self, messages, **kwargs) -> str:
        raise RuntimeError("provider down")


@pytest.mark.parametrize("provider_factory", [_GarbageProvider, _ExplodingProvider])
def test_malformed_llm_output_never_produces_a_dangerous_action(
    db_session, provider_factory
):
    refunds_before = _refund_count(db_session)
    workflow = AgentWorkflow(
        case_manager=AfterSalesCaseManager(db_session),
        llm_intent=LLMIntentExtractor(provider_factory()),
    )

    state, result = workflow.execute(
        "req-llm", "耳机坏了想退款", user_id=1, session_id="s1"
    )

    # The deterministic fallback still understands the case ...
    assert result.after_sales_case["requested_action"] == "REFUND"
    assert result.status is AgentResultStatus.NEEDS_CLARIFICATION
    # ... but nothing was executed: a malformed proposal never plans a tool.
    assert state.tool_requests == ()
    assert result.tool_requests == ()
    assert _refund_count(db_session) == refunds_before


# ---- 13. case service failure --------------------------------------------


class _BrokenCaseManager:
    name = "broken-case-manager"

    def handle(self, **kwargs):
        raise RuntimeError("case service unavailable")


def test_case_service_failure_does_not_break_the_chat_flow(db_session):
    workflow = AgentWorkflow(case_manager=_BrokenCaseManager())

    state, result = workflow.execute(
        "req-broken", "我的耳机坏了，帮我处理一下。", user_id=1, session_id="s1"
    )

    assert result.status is AgentResultStatus.ESCALATION_REQUIRED
    assert result.route is Route.ESCALATE
    assert result.after_sales_case is None
    assert state.error is None


# ---- serialization contract ---------------------------------------------


def test_case_survives_agent_state_and_result_round_trip(workflow):
    state, result = workflow.execute(
        "req-1", "我的耳机坏了，帮我处理一下。", user_id=1, session_id="s1"
    )

    assert state.to_dict()["after_sales_case"] == result.after_sales_case
    restored = AgentState.from_dict(state.to_dict())
    assert restored.after_sales_case == result.after_sales_case
