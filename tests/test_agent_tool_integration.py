"""Phase 4B Agent <-> Tool integration tests (spec items 45-52 + e2e).

Every scenario runs the real chain:

    User request -> Intent -> Route -> ToolRequest -> ToolExecutor
                 -> Service -> Repository -> DB -> ToolResult -> AgentResult

Only ORDER_TOOL / LOGISTICS_TOOL / REFUND_TOOL / CANCEL_TOOL / TICKET_TOOL
execute; CLARIFY / ESCALATE / RAG never run a tool.

The fixture extends the mock seed with orders whose integer ids match the
user-visible ORD- references used by the Agent examples:
    ORD-1001 = Alice, DELIVERED + fully returnable (eligible for refund)
    ORD-1002 = Alice, PAID (cancellable)
    ORD-2001 = Bob,    PAID (cross-user target for Alice)
"""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.state import AgentResultStatus, Intent, Route
from app.agent.workflow import AgentWorkflow
from app.db.base import Base
from app.db.enums import KnowledgeCategory, LogisticsStatus, OrderStatus, RefundStatus
from app.db.models import Logistics, Order, OrderItem, Product, Refund, Ticket, User
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory
from app.retrieval.context import ContextItem, ContextPackage
from app.tools.executor import ToolExecutor
from app.tools.handlers import build_default_registry

UTC = timezone.utc


@pytest.fixture()
def db_session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _add_order(
    session: Session,
    order_id: int,
    user: User,
    status: OrderStatus,
    product: Product,
    quantity: int,
    created_at: datetime,
) -> Order:
    order = Order(
        id=order_id,
        user=user,
        status=status,
        total_amount=product.price * quantity,
        currency="CNY",
        created_at=created_at,
        updated_at=created_at,
    )
    order.items.append(
        OrderItem(
            product=product,
            product_name=product.name,
            quantity=quantity,
            unit_price=product.price,
        )
    )
    session.add(order)
    return order


@pytest.fixture()
def extended_seeded(db_session) -> Session:
    assert seed_dev_data(db_session) is True

    alice = db_session.scalar(select(User).where(User.email == "alice@example.com"))
    bob = db_session.scalar(select(User).where(User.email == "bob@example.com"))
    assert alice is not None and bob is not None
    products = {product.name: product for product in db_session.scalars(select(Product))}

    o1001 = _add_order(
        db_session, 1001, alice, OrderStatus.DELIVERED,
        products["Wireless Earbuds Pro"], 1, datetime(2026, 8, 20, 10, 0, tzinfo=UTC),
    )
    _add_order(
        db_session, 1002, alice, OrderStatus.PAID,
        products["Cotton T-Shirt"], 1, datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
    )
    _add_order(
        db_session, 2001, bob, OrderStatus.PAID,
        products["LED Desk Lamp"], 1, datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
    )
    db_session.add(
        Logistics(
            order=o1001,
            carrier="SF Express",
            tracking_number="SF10020099",
            status=LogisticsStatus.IN_TRANSIT,
            estimated_delivery=date(2026, 8, 26),
            updated_at=datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
        )
    )
    db_session.commit()
    return db_session


@pytest.fixture()
def workflow(extended_seeded) -> AgentWorkflow:
    executor = ToolExecutor(build_default_registry(extended_seeded))
    return AgentWorkflow(tool_executor=executor)


class StubRetrievalRunner:
    """Records queries and returns a fixed typed ContextPackage (RAG branch)."""

    def __init__(self, package: ContextPackage) -> None:
        self.package = package
        self.queries: list[str] = []

    def run(self, query: str) -> ContextPackage:
        self.queries.append(query)
        return self.package


def _package(query: str = "为什么退款需要满足条件？") -> ContextPackage:
    item = ContextItem(
        chunk_id=1,
        document_id=1,
        source_id="internal/mock/policy",
        title="退款政策",
        category=KnowledgeCategory.REFUND,
        version="2.0.0",
        status="ACTIVE",
        section="退款条件",
        language="zh-CN",
        content="退款需要满足订单已签收且商品可退货等条件。",
        relevance_score=0.9,
        retrieval_methods=("bm25", "dense"),
    )
    return ContextPackage(
        query=query,
        items=(item,),
        total_items=1,
        truncated=False,
        token_budget=2000,
        estimated_tokens=30,
    )


def _refunds_of(session: Session, order_id: int) -> list[Refund]:
    return list(
        session.scalars(select(Refund).where(Refund.order_id == order_id).order_by(Refund.id))
    )


# 45. ORDER_STATUS -> get_order -> ToolResult


def test_agent_order_status_executes_get_order(workflow):
    state, result = workflow.execute(
        "req-45", "查一下订单状态 ORD-1001", user_id=1, session_id="sess-45"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert result.intent is Intent.ORDER_STATUS
    assert result.route is Route.ORDER_TOOL
    assert len(state.tool_requests) == 1
    assert state.tool_requests[0].tool_name == "get_order"
    assert len(state.tool_results) == 1
    tool_result = state.tool_results[0]
    assert tool_result["tool_name"] == "get_order"
    assert tool_result["status"] == "SUCCESS"
    assert tool_result["data"]["order_id"] == 1001
    assert tool_result["data"]["status"] == "DELIVERED"


# 46. LOGISTICS_TRACKING -> get_logistics -> ToolResult


def test_agent_logistics_executes_get_logistics(workflow):
    state, result = workflow.execute(
        "req-46", "帮我查订单 ORD-1001 到哪了", user_id=1, session_id="sess-46"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert result.intent is Intent.LOGISTICS_TRACKING
    assert result.route is Route.LOGISTICS_TOOL
    tool_result = state.tool_results[0]
    assert tool_result["tool_name"] == "get_logistics"
    assert tool_result["status"] == "SUCCESS"
    assert tool_result["data"]["tracking_number"] == "SF10020099"
    assert tool_result["data"]["order_id"] == 1001


# 47. REFUND_REQUEST -> eligibility -> conditional create_refund


def test_agent_refund_request_conditional_refund(extended_seeded, workflow):
    state, result = workflow.execute(
        "req-47", "帮我把 ORD-1001 退款", user_id=1, session_id="sess-47"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert result.intent is Intent.REFUND_REQUEST
    assert result.route is Route.REFUND_TOOL

    tool_names = [request.tool_name for request in state.tool_requests]
    assert tool_names == ["check_refund_eligibility", "create_refund"]
    assert all(request.status.value == "EXECUTED" for request in state.tool_requests)

    assert [item["tool_name"] for item in state.tool_results] == tool_names
    assert all(item["status"] == "SUCCESS" for item in state.tool_results)
    eligibility = state.tool_results[0]["data"]
    assert eligibility["eligible"] is True
    refund = state.tool_results[1]["data"]
    assert refund["status"] == "PENDING"
    assert Decimal(str(refund["amount"])) == Decimal("1299.00")

    # DB persistence: the refund request really reached the database.
    rows = _refunds_of(extended_seeded, 1001)
    assert len(rows) == 1
    assert rows[0].user_id == 1
    assert rows[0].status == RefundStatus.PENDING
    assert rows[0].amount == Decimal("1299.00")


def test_agent_refund_request_ineligible_never_creates_refund(extended_seeded, workflow):
    # ORD-0008 (Carol, CANCELLED): eligibility stops the refund before any write.
    state, result = workflow.execute(
        "req-47b", "帮我把 ORD-0008 退款", user_id=3, session_id="sess-47b"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert [r.tool_name for r in state.tool_requests] == ["check_refund_eligibility"]
    assert state.tool_results[0]["data"]["eligible"] is False
    assert _refunds_of(extended_seeded, 8) == []


def test_agent_refund_request_cross_user_rejected(extended_seeded, workflow):
    # ORD-2001 belongs to Bob; Alice's refund request must not reach services.
    state, result = workflow.execute(
        "req-47c", "帮我把 ORD-2001 退款", user_id=1, session_id="sess-47c"
    )
    assert result.status is AgentResultStatus.ERROR
    assert state.tool_results[0]["status"] == "FAILED"
    assert state.tool_results[0]["error_code"] == "UNAUTHORIZED_ORDER_ACCESS"
    assert [r.tool_name for r in state.tool_requests] == ["check_refund_eligibility"]
    assert _refunds_of(extended_seeded, 2001) == []


# 48. CANCEL_ORDER -> cancel_order


def test_agent_cancel_order_executes_cancel_order(extended_seeded, workflow):
    state, result = workflow.execute(
        "req-48", "帮我取消订单 ORD-1002", user_id=1, session_id="sess-48"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert result.intent is Intent.CANCEL_ORDER
    assert result.route is Route.CANCEL_TOOL
    assert state.tool_requests[0].tool_name == "cancel_order"
    assert state.tool_requests[0].requires_confirmation is True
    assert state.tool_results[0]["status"] == "SUCCESS"
    assert state.tool_results[0]["data"]["status"] == "CANCELLED"
    # Confirmation / risk metadata is preserved on the execution.
    assert state.tool_results[0]["metadata"]["requires_confirmation"] is True
    assert extended_seeded.get(Order, 1002).status == OrderStatus.CANCELLED


# 49. CREATE_TICKET -> create_ticket


def test_agent_create_ticket_executes_create_ticket(extended_seeded, workflow):
    state, result = workflow.execute(
        "req-49", "我要投诉物流太慢", user_id=1, session_id="sess-49"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert result.intent is Intent.CREATE_TICKET
    assert result.route is Route.TICKET_TOOL
    tool_result = state.tool_results[0]
    assert tool_result["tool_name"] == "create_ticket"
    assert tool_result["status"] == "SUCCESS"
    assert tool_result["data"]["order_id"] is None
    assert tool_result["data"]["category"] == "COMPLAINT"
    row = extended_seeded.scalar(
        select(Ticket).where(Ticket.description == "我要投诉物流太慢")
    )
    assert row is not None
    assert row.user_id == 1
    assert row.category == "COMPLAINT"


# 50 / 51 / 52. CLARIFY / ESCALATE / RAG -> no tool execution


def test_agent_clarify_never_executes_tool(workflow):
    state, result = workflow.execute("req-50", "我要退款", user_id=1, session_id="sess-50")
    assert result.status is AgentResultStatus.NEEDS_CLARIFICATION
    assert result.route is Route.CLARIFY
    assert state.tool_results == ()
    assert state.tool_requests == ()


def test_agent_escalate_never_executes_tool(workflow):
    state, result = workflow.execute(
        "req-51", "帮我写一份 Python 教程", user_id=1, session_id="sess-51"
    )
    assert result.status is AgentResultStatus.ESCALATION_REQUIRED
    assert result.route is Route.ESCALATE
    assert state.tool_results == ()
    assert state.tool_requests == ()


def test_agent_rag_never_executes_tool(extended_seeded):
    runner = StubRetrievalRunner(_package())
    executor = ToolExecutor(build_default_registry(extended_seeded))
    workflow = AgentWorkflow(retrieval=runner, tool_executor=executor)
    state, result = workflow.execute(
        "req-52", "为什么退款需要满足条件？", user_id=1, session_id="sess-52"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert result.route is Route.RAG
    assert runner.queries == ["为什么退款需要满足条件？"]
    assert state.tool_results == ()
    assert state.tool_requests == ()


# End-to-end: User request -> Intent -> Route -> ToolRequest -> ToolExecutor
#              -> Service -> Repository -> DB -> ToolResult -> AgentResult


def test_end_to_end_user_request_to_database_to_agent_result(extended_seeded, workflow):
    state, result = workflow.execute(
        "req-e2e", "帮我把 ORD-1001 退款", user_id=1, session_id="sess-e2e"
    )
    assert result.status is AgentResultStatus.SUCCESS
    assert state.route is Route.REFUND_TOOL
    assert len(state.tool_results) == 2
    assert state.tool_results[-1]["tool_name"] == "create_refund"

    rows = _refunds_of(extended_seeded, 1001)
    assert len(rows) == 1
    refund = rows[0]
    assert refund.order_id == 1001
    assert refund.user_id == 1
    assert refund.amount == Decimal("1299.00")
    assert refund.status == RefundStatus.PENDING

    # ToolRequest planning -> execution lifecycle is observable on the state.
    assert state.tool_requests[0].status.value == "EXECUTED"
    assert state.tool_requests[1].status.value == "EXECUTED"