"""Agent workflow tests (Phase 4A).

Covers the full workflow: RAG branch against the existing retrieval pipeline,
business tool branch planning (never executing), clarification / escalation,
deterministic output, Response State serialization, and the "agent layer does
not touch the database" boundary.

Scenarios from the Phase 4A spec:
    A "为什么退款需要满足条件？"  -> REFUND_INQUIRY -> RAG -> ContextPackage
    B "帮我查订单 ORD-1001 到哪了" -> LOGISTICS_TRACKING -> LOGISTICS_TOOL
    C "我要退款"                  -> REFUND_REQUEST -> CLARIFY (no order)
    D "帮我把 ORD-1001 退款"      -> REFUND_REQUEST -> REFUND_TOOL (no exec)
"""
import ast
from pathlib import Path

import pytest

from app.agent.entities import DeterministicEntityExtractor, ExtractedEntities
from app.agent.state import (
    AgentResult,
    AgentResultStatus,
    AgentState,
    Intent,
    Route,
    ToolRequest,
    ToolRequestStatus,
    WorkflowStage,
)
from app.agent.workflow import AgentWorkflow
from app.db.base import Base
from app.db.enums import KnowledgeCategory
from app.db.session import create_db_engine, create_session_factory
from app.knowledge.seed import seed_knowledge
from app.retrieval.context import ContextItem, ContextPackage
from app.retrieval.pipeline import RetrievalPipeline

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
AGENT_DIR = BACKEND_DIR / "app" / "agent"


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


@pytest.fixture()
def seeded(db_session):
    seed_knowledge(db_session)
    return db_session


def _make_package(query: str = "为什么退款需要满足条件？") -> ContextPackage:
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


class StubRetrievalRunner:
    """Records queries and returns a fixed typed ContextPackage."""

    def __init__(self, package: ContextPackage) -> None:
        self.package = package
        self.queries: list[str] = []

    def run(self, query: str) -> ContextPackage:
        self.queries.append(query)
        return self.package


@pytest.fixture()
def stub_runner() -> StubRetrievalRunner:
    return StubRetrievalRunner(_make_package())


def _tool_requests(result: AgentResult) -> tuple[ToolRequest, ...]:
    return result.tool_requests


# --- Scenario A: RAG branch ----------------------------------------------


def test_scenario_a_refund_inquiry_rag_with_package(stub_runner):
    workflow = AgentWorkflow(retrieval=stub_runner)
    state, result = workflow.execute("req-a", "为什么退款需要满足条件？", user_id=1)

    assert result.status is AgentResultStatus.SUCCESS
    assert result.intent is Intent.REFUND_INQUIRY
    assert result.route is Route.RAG
    assert stub_runner.queries == ["为什么退款需要满足条件？"]
    # structured ContextPackage is preserved, never collapsed to a string
    assert state.retrieved_context is stub_runner.package
    assert isinstance(state.retrieved_context, ContextPackage)
    assert result.citations == tuple(
        item.citation for item in state.retrieved_context.items
    )
    assert state.status is WorkflowStage.END


def test_rag_branch_invokes_existing_retrieval_pipeline(seeded):
    """RAG branch runs through the real Phase 3C pipeline entry point."""
    pipeline = RetrievalPipeline(seeded)
    workflow = AgentWorkflow(retrieval=pipeline)
    state, result = workflow.execute("req-a-db", "为什么退款需要满足条件？", user_id=1)

    assert result.status is AgentResultStatus.SUCCESS
    assert result.route is Route.RAG
    assert state.retrieved_context is not None
    assert isinstance(state.retrieved_context, ContextPackage)
    assert result.citations == tuple(
        item.citation for item in state.retrieved_context.items
    )


def test_knowledge_question_rag_branch(stub_runner):
    workflow = AgentWorkflow(retrieval=stub_runner)
    result = workflow.run("req-k", "客服电话是多少？", user_id=1)
    assert result.status is AgentResultStatus.SUCCESS
    assert result.intent is Intent.KNOWLEDGE_QA
    assert result.route is Route.RAG


def test_rag_without_runner_is_an_error():
    workflow = AgentWorkflow()  # no retrieval runner configured
    result = workflow.run("req-e", "为什么退款需要满足条件？", user_id=1)
    assert result.status is AgentResultStatus.ERROR
    assert result.route is Route.RAG
    assert result.error and "retrieval" in result.error


# --- Scenario B / D: business tool branch --------------------------------


def test_scenario_b_logistics_tool_request():
    workflow = AgentWorkflow()
    result = workflow.run("req-b", "帮我查订单 ORD-1001 到哪了", user_id=1)

    assert result.status is AgentResultStatus.TOOL_REQUESTED
    assert result.intent is Intent.LOGISTICS_TRACKING
    assert result.route is Route.LOGISTICS_TOOL
    (request,) = _tool_requests(result)
    assert request.tool_name == "get_logistics"
    assert request.arguments == {"order_id": "ORD-1001"}
    assert request.status is ToolRequestStatus.PENDING


def test_scenario_d_refund_request_plans_eligibility_check():
    workflow = AgentWorkflow()
    result = workflow.run("req-d", "帮我把 ORD-1001 退款", user_id=1)

    assert result.status is AgentResultStatus.TOOL_REQUESTED
    assert result.intent is Intent.REFUND_REQUEST
    assert result.route is Route.REFUND_TOOL
    (request,) = _tool_requests(result)
    # Phase 4A plans the eligibility check first; it never executes anything.
    assert request.tool_name == "check_refund_eligibility"
    assert request.arguments == {"order_id": "ORD-1001"}
    assert request.status is ToolRequestStatus.PENDING


def test_business_route_never_executes_tools():
    workflow = AgentWorkflow()
    state, result = workflow.execute("req-d2", "帮我把 ORD-1001 退款", user_id=1)

    assert result.status is AgentResultStatus.TOOL_REQUESTED
    assert state.tool_requests == result.tool_requests
    assert state.tool_results == ()  # nothing was executed
    assert result.tool_requests[0].status is ToolRequestStatus.PENDING


def test_cancel_order_requires_confirmation():
    workflow = AgentWorkflow()
    result = workflow.run("req-x", "帮我取消订单 ORD-1001", user_id=1)
    assert result.route is Route.CANCEL_TOOL
    (request,) = _tool_requests(result)
    assert request.tool_name == "cancel_order"
    assert request.requires_confirmation is True
    assert request.arguments == {"order_id": "ORD-1001"}


def test_create_ticket_uses_user_context():
    workflow = AgentWorkflow()
    result = workflow.run(
        "req-t", "我要投诉物流异常", user_id=7, session_id="sess-1"
    )
    assert result.route is Route.TICKET_TOOL
    (request,) = _tool_requests(result)
    assert request.tool_name == "create_ticket"
    assert request.arguments["user_id"] == 7
    assert request.arguments["description"] == "我要投诉物流异常"


# --- Scenario C / clarification / escalation ------------------------------


def test_scenario_c_refund_request_without_order_clarifies():
    workflow = AgentWorkflow()
    result = workflow.run("req-c", "我要退款", user_id=1)

    assert result.status is AgentResultStatus.NEEDS_CLARIFICATION
    assert result.intent is Intent.REFUND_REQUEST
    assert result.route is Route.CLARIFY
    assert result.needs_clarification is True
    assert result.tool_requests == ()


def test_ambiguous_message_clarifies(stub_runner):
    workflow = AgentWorkflow(retrieval=stub_runner)
    result = workflow.run("req-am", "我要退款，也想投诉", user_id=1)
    assert result.status is AgentResultStatus.NEEDS_CLARIFICATION
    assert result.route is Route.CLARIFY
    assert stub_runner.queries == []  # no RAG was forced


def test_empty_message_clarifies():
    workflow = AgentWorkflow()
    result = workflow.run("req-empty", "   ", user_id=1)
    assert result.status is AgentResultStatus.NEEDS_CLARIFICATION
    assert result.needs_clarification is True


def test_unsupported_message_escalates():
    workflow = AgentWorkflow()
    result = workflow.run("req-u", "帮我写一份 Python 教程", user_id=1)
    assert result.status is AgentResultStatus.ESCALATION_REQUIRED
    assert result.intent is Intent.UNSUPPORTED
    assert result.route is Route.ESCALATE
    assert result.escalation_required is True


def test_explicit_human_handoff_escalates():
    workflow = AgentWorkflow()
    result = workflow.run("req-h", "帮我转人工客服", user_id=1)
    assert result.status is AgentResultStatus.ESCALATION_REQUIRED
    assert result.route is Route.ESCALATE
    assert result.escalation_required is True


# --- determinism / serialization / state ----------------------------------


def test_workflow_output_is_deterministic(stub_runner):
    workflow = AgentWorkflow(retrieval=stub_runner)
    first = workflow.run("req-det", "为什么退款需要满足条件？").to_dict()
    second = workflow.run("req-det", "为什么退款需要满足条件？").to_dict()
    assert first == second


def test_agent_state_serialization_roundtrip():
    entities = DeterministicEntityExtractor().extract("帮我把 ORD-1001 退款")
    state = AgentState(
        request_id="req-1",
        session_id="sess-1",
        user_id=1,
        user_message="帮我把 ORD-1001 退款",
        intent=Intent.REFUND_REQUEST,
        intent_confidence=0.91,
        route=Route.REFUND_TOOL,
        entities=entities,
        tool_requests=(
            ToolRequest(
                tool_name="check_refund_eligibility",
                arguments={"order_id": "ORD-1001"},
                reason="check eligibility first",
            ),
        ),
        status=WorkflowStage.END,
    )
    data = state.to_dict()
    restored = AgentState.from_dict(data)
    assert restored.to_dict() == data
    assert restored.entities == entities
    assert restored.tool_requests[0].tool_name == "check_refund_eligibility"


def test_agent_result_serialization_and_validation():
    result = AgentResult(
        status=AgentResultStatus.NEEDS_CLARIFICATION,
        intent=Intent.REFUND_REQUEST,
        route=Route.CLARIFY,
        needs_clarification=True,
    )
    data = result.to_dict()
    assert data["status"] == "needs_clarification"
    assert AgentResult.from_dict(data) == result
    with pytest.raises(ValueError):
        AgentResult.from_dict({"status": "not-a-status"})


def test_workflow_uses_domain_state_not_framework_state(stub_runner):
    """AgentWorkflow returns our own AgentState/AgentResult, not a graph state."""
    workflow = AgentWorkflow(retrieval=stub_runner)
    state, result = workflow.execute("req-s", "为什么退款需要满足条件？", user_id=1)
    assert isinstance(state, AgentState)
    assert isinstance(result, AgentResult)
    assert state.status is WorkflowStage.END


def test_agent_layer_has_no_database_imports():
    """The agent package never imports SQLAlchemy or app.db / app.services.

    The RAG branch only calls the injected RetrievalRunner interface; the
    business branch only plans ToolRequests.
    """
    forbidden_roots = ("sqlalchemy", "app.db", "app.services", "app.api")
    offenders: list[str] = []
    for path in sorted(AGENT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            for module in imported:
                root = module.split(".", 1)[0]
                if any(module.startswith(prefix) for prefix in forbidden_roots):
                    offenders.append(f"{path.name}: imports {module}")
    assert offenders == []