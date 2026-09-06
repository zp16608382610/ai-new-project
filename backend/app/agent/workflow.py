"""Framework-agnostic agent workflow (Phase 4A + 4B).

Explicit state flow (orchestration only - no business logic in nodes):

    START -> UNDERSTAND -> CLASSIFY_INTENT -> ROUTE
        -> RAG | BUSINESS_TOOL_REQUEST -> TOOL_EXECUTION
             | CLARIFY | ESCALATE
        -> FINALIZE -> END

Design decisions:
    - LangGraph is deliberately NOT a dependency. AgentState is our own domain
      state and the workflow is a small deterministic state machine. A later
      LangGraph adapter can map domain state onto LangGraph state without
      rewriting business code (docs/DECISIONS.md Decision 024).
    - The RAG branch only calls the existing RetrievalPipeline entry point
      through the RetrievalRunner interface and preserves the structured
      ContextPackage inside AgentState (never a bare string).
    - The Business Tool branch plans a typed ToolRequest. When no ToolExecutor
      is injected the branch keeps Phase 4A planning-only behaviour
      (TOOL_REQUESTED); when one is injected (Phase 4B) the request is
      executed Agent -> Tool -> Service -> Repository -> Database and the
      normalized ToolResult is stored in AgentState.tool_results.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from app.agent.entities import DeterministicEntityExtractor, EntityExtractor, ExtractedEntities
from app.agent.intent import (
    DeterministicIntentClassifier,
    IntentClassifier,
)
from app.agent.router import RuleBasedRouter, WorkflowRouter
from app.agent.state import (
    AgentResult,
    AgentResultStatus,
    AgentState,
    Route,
    ToolRequest,
    ToolRequestStatus,
    WorkflowStage,
)

if TYPE_CHECKING:
    from app.retrieval.context import ContextPackage

from app.tools.base import ToolExecutionContext, ToolResult, ToolResultStatus

class RetrievalRunner(Protocol):
    """Existing RAG pipeline entry point used by the RAG branch.

    RetrievalPipeline.run(query) structurally satisfies this Protocol; the
    agent layer depends on the interface only and never opens a session.
    """

    def run(self, query: str) -> "ContextPackage":
        """Run retrieval -> rerank -> context assembly for a query."""
        ...


@dataclass(frozen=True)
class ToolSpec:
    """Tool name + confirmation policy used to plan ToolRequests."""

    tool_name: str
    requires_confirmation: bool
    reason: str


# Business tool interface: which tool a route plans (executed in 4B).
_ROUTE_TOOL_SPEC = {
    Route.ORDER_TOOL: ToolSpec(
        "get_order",
        False,
        "ORDER_STATUS intent: query order detail through the business tool.",
    ),
    Route.LOGISTICS_TOOL: ToolSpec(
        "get_logistics",
        False,
        "LOGISTICS_TRACKING intent: query logistics through the business tool.",
    ),
    Route.REFUND_TOOL: ToolSpec(
        "check_refund_eligibility",
        False,
        "REFUND_REQUEST intent: first check refund eligibility; a refund "
        "execution would require risk control / approval in later phases.",
    ),
    Route.CANCEL_TOOL: ToolSpec(
        "cancel_order",
        True,
        "CANCEL_ORDER intent: cancelling an order is high-risk and needs "
        "confirmation / risk control before execution.",
    ),
    Route.TICKET_TOOL: ToolSpec(
        "create_ticket",
        False,
        "CREATE_TICKET intent: register a complaint ticket.",
    ),
}


class ToolExecutorLike(Protocol):
    """Injected Phase 4B executor (app.tools.ToolExecutor satisfies it).

    The workflow depends on this narrow interface only, so the agent layer
    never reaches Services / Repository / SQLAlchemy directly.
    """

    def execute(
        self,
        tool_name: str,
        arguments: dict | None,
        context: ToolExecutionContext,
        *,
        requires_confirmation: bool = False,
    ) -> ToolResult:
        """Execute one registered tool and return a normalized ToolResult."""
        ...


class AgentWorkflow:
    """Deterministic, framework-agnostic workflow orchestrator.

    Dependencies are injected so tests can swap the classifier / extractor /
    router, the RAG runner (real RetrievalPipeline or a stub) and the Phase 4B
    ToolExecutor (see ToolExecutorLike).
    """

    def __init__(
        self,
        *,
        classifier: IntentClassifier | None = None,
        entity_extractor: EntityExtractor | None = None,
        router: WorkflowRouter | None = None,
        retrieval: RetrievalRunner | None = None,
        tool_executor: ToolExecutorLike | None = None,
    ) -> None:
        self._classifier = classifier or DeterministicIntentClassifier()
        self._entity_extractor = entity_extractor or DeterministicEntityExtractor()
        self._router = router or RuleBasedRouter()
        self._retrieval = retrieval
        self._tool_executor = tool_executor

    @property
    def classifier(self) -> IntentClassifier:
        return self._classifier

    @property
    def entity_extractor(self) -> EntityExtractor:
        return self._entity_extractor

    @property
    def router(self) -> WorkflowRouter:
        return self._router

    @property
    def retrieval(self) -> RetrievalRunner | None:
        return self._retrieval

    @property
    def tool_executor(self) -> ToolExecutorLike | None:
        return self._tool_executor

    def run(
        self,
        request_id: str,
        user_message: str,
        *,
        user_id: int | None = None,
        session_id: str | None = None,
    ) -> AgentResult:
        """Run the workflow and return the Response State (AgentResult)."""
        _, result = self.execute(
            request_id,
            user_message,
            user_id=user_id,
            session_id=session_id,
        )
        return result

    def execute(
        self,
        request_id: str,
        user_message: str,
        *,
        user_id: int | None = None,
        session_id: str | None = None,
    ) -> tuple[AgentState, AgentResult]:
        """Run the workflow and return (AgentState, AgentResult).

        AgentState keeps the full structured working state (entities, route,
        retrieved_context as ContextPackage, tool_requests) for later
        observability / evaluation phases.
        """
        state = AgentState(
            request_id=request_id,
            user_message=user_message,
            session_id=session_id,
            user_id=user_id,
            status=WorkflowStage.START,
        )
        try:
            result = self._execute_inner(state)
        except Exception as exc:  # defensive error boundary; never fabricate success
            state.error = f"{type(exc).__name__}: {exc}"
            state.status = WorkflowStage.END
            result = AgentResult(
                status=AgentResultStatus.ERROR,
                intent=state.intent,
                route=state.route,
                error=state.error,
            )
        state.status = WorkflowStage.FINALIZE
        state.response = result.response
        state.status = WorkflowStage.END
        return state, result

    # -- internals ----------------------------------------------------------

    def _execute_inner(self, state: AgentState) -> AgentResult:
        state.status = WorkflowStage.UNDERSTAND
        entities = self._entity_extractor.extract(state.user_message)
        state.entities = entities

        state.status = WorkflowStage.CLASSIFY_INTENT
        intent_result = self._classifier.classify(state.user_message)
        state.intent = intent_result.intent
        state.intent_confidence = intent_result.confidence

        state.status = WorkflowStage.ROUTE
        decision = self._router.decide(
            intent_result.intent,
            entities,
            user_id=state.user_id,
            user_message=state.user_message,
        )
        state.route = decision.route

        if decision.route is Route.RAG:
            return self._run_rag(state)
        if decision.route in _ROUTE_TOOL_SPEC:
            return self._run_tool_branch(state)
        if decision.route is Route.CLARIFY:
            state.status = WorkflowStage.CLARIFY
            return AgentResult(
                status=AgentResultStatus.NEEDS_CLARIFICATION,
                intent=state.intent,
                route=Route.CLARIFY,
                needs_clarification=True,
            )
        if decision.route is Route.ESCALATE:
            state.status = WorkflowStage.ESCALATE
            return AgentResult(
                status=AgentResultStatus.ESCALATION_REQUIRED,
                intent=state.intent,
                route=Route.ESCALATE,
                escalation_required=True,
            )
        raise RuntimeError(f"Unhandled route: {decision.route}")

    def _run_rag(self, state: AgentState) -> AgentResult:
        state.status = WorkflowStage.RAG
        if self._retrieval is None:
            return AgentResult(
                status=AgentResultStatus.ERROR,
                intent=state.intent,
                route=Route.RAG,
                error="RAG route selected but no retrieval runner is configured.",
            )
        package = self._retrieval.run(state.user_message)
        state.retrieved_context = package
        citations = tuple(item.citation for item in package.items)
        return AgentResult(
            status=AgentResultStatus.SUCCESS,
            intent=state.intent,
            route=Route.RAG,
            citations=citations,
        )

    def _run_tool_branch(self, state: AgentState) -> AgentResult:
        """Plan (and, when an executor is injected, execute) business tools.

        Phase 4A compatibility: without a ToolExecutor the branch only plans
        and returns TOOL_REQUESTED exactly as before. Phase 4B: every planned
        tool runs through the injected executor and ToolResults land in
        AgentState.tool_results.
        """
        state.status = WorkflowStage.BUSINESS_TOOL_REQUEST
        spec = _ROUTE_TOOL_SPEC[state.route]
        request = _plan_tool_request(state, spec)

        if self._tool_executor is None:
            state.tool_requests = (request,)
            # tool_results stays empty: planning-only runs never execute tools.
            return AgentResult(
                status=AgentResultStatus.TOOL_REQUESTED,
                intent=state.intent,
                route=state.route,
                tool_requests=(request,),
            )
        return self._execute_tool_plan(state, [request])

    # -- Phase 4B: tool execution ------------------------------------------

    def _execute_tool_plan(
        self, state: AgentState, requests: list[ToolRequest]
    ) -> AgentResult:
        """Run the planned tool requests in order and finalize.

        REFUND_REQUEST runs check_refund_eligibility first; create_refund is
        only appended when the eligibility ToolResult says eligible=True, so
        an ineligible / denied order never reaches the refund service.
        """
        plan = list(requests)
        executed: list[ToolRequest] = []
        results: list[ToolResult] = []
        while plan:
            request = plan.pop(0)
            state.status = WorkflowStage.TOOL_EXECUTION
            result = self._execute_one_tool(state, request)
            request = replace(
                request,
                status=ToolRequestStatus.EXECUTED if result.success else ToolRequestStatus.FAILED,
            )
            executed.append(request)
            results.append(result)
            state.tool_requests = tuple(executed)
            state.tool_results = tuple(item.to_dict() for item in results)

            if (
                result.status is ToolResultStatus.SUCCESS
                and request.tool_name == "check_refund_eligibility"
                and bool(result.data.get("eligible"))
            ):
                plan.append(_plan_refund_execution_request(state))

        last = results[-1]
        if last.status is not ToolResultStatus.SUCCESS:
            return AgentResult(
                status=AgentResultStatus.ERROR,
                intent=state.intent,
                route=state.route,
                tool_requests=tuple(executed),
                error=last.error_message or f"Tool {last.tool_name} failed: {last.status.value}",
            )
        return AgentResult(
            status=AgentResultStatus.SUCCESS,
            intent=state.intent,
            route=state.route,
            tool_requests=tuple(executed),
        )

    def _execute_one_tool(self, state: AgentState, request: ToolRequest) -> ToolResult:
        """Execute one planned request with the trusted request context."""
        context = ToolExecutionContext(
            request_id=state.request_id,
            session_id=state.session_id,
            user_id=state.user_id,
        )
        assert self._tool_executor is not None
        return self._tool_executor.execute(
            request.tool_name,
            request.arguments,
            context,
            requires_confirmation=request.requires_confirmation,
        )


def _plan_tool_request(state: AgentState, spec: ToolSpec) -> ToolRequest:
    """Map route + extracted entities + user context onto a ToolRequest.

    Arguments are entity/session derived only; nothing is guessed. The
    Phase 4B executor validates and normalizes them against business rules.
    """
    entities: ExtractedEntities | None = state.entities
    order_id = entities.order_id if entities else None

    if state.route is Route.ORDER_TOOL:
        arguments: dict = {"order_id": order_id}
    elif state.route is Route.LOGISTICS_TOOL:
        arguments = {"order_id": order_id}
        if entities and entities.tracking_number:
            arguments["tracking_number"] = entities.tracking_number
    elif state.route is Route.REFUND_TOOL:
        arguments = {"order_id": order_id}
    elif state.route is Route.CANCEL_TOOL:
        arguments = {"order_id": order_id}
    elif state.route is Route.TICKET_TOOL:
        # reason is the short ticket category consumed by create_ticket; a
        # deterministic default keeps execution model-free in this phase.
        arguments = {
            "user_id": state.user_id,
            "reason": "COMPLAINT",
            "description": state.user_message,
        }
        if order_id:
            arguments["order_id"] = order_id
    else:  # pragma: no cover - guarded by _ROUTE_TOOL_SPEC in the caller
        raise ValueError(f"route {state.route} has no tool mapping")

    return ToolRequest(
        tool_name=spec.tool_name,
        arguments=arguments,
        reason=spec.reason,
        requires_confirmation=spec.requires_confirmation,
    )


def _plan_refund_execution_request(state: AgentState) -> ToolRequest:
    """Second refund step: create the refund after eligibility passed.

    The amount is never part of the arguments - the RefundService derives the
    authoritative amount from the order. Risk control / human approval for the
    execution step arrives in Phase 5.
    """
    order_id = state.entities.order_id if state.entities else None
    return ToolRequest(
        tool_name="create_refund",
        arguments={"order_id": order_id},
        reason=(
            "REFUND_REQUEST intent: eligibility passed; create a refund request "
            "with the service-authoritative amount."
        ),
        requires_confirmation=False,
    )
