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

import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from app.agent.after_sales import (
    STATUS_ELIGIBILITY_CHECK,
    STATUS_PROCESSING,
    AfterSalesCaseManagerLike,
    AfterSalesCaseOutcome,
    AfterSalesInvestigationOutcome,
    AfterSalesTreatmentOutcome,
    AfterSalesTreatmentPlannerLike,
    CaseInvestigatorLike,
)
from app.agent.entities import DeterministicEntityExtractor, EntityExtractor, ExtractedEntities
from app.agent.intent import (
    DeterministicIntentClassifier,
    IntentClassifier,
)
from app.agent.router import RuleBasedRouter, WorkflowRouter
from app.agent.state import (
    AgentResult,
    AgentResultStatus,
    AgentRunStatus,
    AgentState,
    Intent,
    Route,
    ToolRequest,
    ToolRequestStatus,
    WorkflowStage,
)

if TYPE_CHECKING:
    from app.retrieval.context import ContextPackage

from app.risk.types import RiskAction, RiskContext, RiskDecision
from app.tools.base import ToolExecutionContext, ToolResult, ToolResultStatus

logger = logging.getLogger(__name__)


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


class RiskEngineLike(Protocol):
    """Phase 5 risk evaluator (app.risk.RiskEngine satisfies it)."""

    def evaluate(self, operation: str, context: RiskContext | None = None) -> RiskDecision:
        """Classify one operation and return a RiskDecision."""
        ...


class ApprovalGatewayLike(Protocol):
    """Approval persistence + resolution used by the Risk Gate.

    ApprovalService (service layer) structurally satisfies this interface; the
    agent layer never imports services directly.
    """

    def create(self, *, request_id, tool_name, tool_arguments, risk_level, reason, user_id):
        """Persist a PENDING approval and return its id (or an object with .id)."""
        ...

    def get(self, approval_id):
        """Return the approval view (None when not found)."""
        ...

    def approve(self, approval_id, resolved_by=None):
        """Transition PENDING -> APPROVED."""
        ...

    def reject(self, approval_id, resolved_by=None):
        """Transition PENDING -> REJECTED."""
        ...


class ToolVerifierLike(Protocol):
    """Execute -> Verify hook. Returns None when authoritative business state
    matches; raises an exception when verification fails."""

    def verify(self, tool_name: str, data: dict) -> None:
        """Re-check business state after a successful write tool."""
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
        risk_engine: RiskEngineLike | None = None,
        approval_gateway: ApprovalGatewayLike | None = None,
        verifier: ToolVerifierLike | None = None,
        llm_intent: 'LLMIntentExtractor | None' = None,
        llm_responder: 'FinalResponder | None' = None,
        case_manager: AfterSalesCaseManagerLike | None = None,
        case_investigator: CaseInvestigatorLike | None = None,
        treatment_planner: AfterSalesTreatmentPlannerLike | None = None,
    ) -> None:
        self._classifier = classifier or DeterministicIntentClassifier()
        self._entity_extractor = entity_extractor or DeterministicEntityExtractor()
        self._router = router or RuleBasedRouter()
        self._retrieval = retrieval
        self._tool_executor = tool_executor
        self._risk_engine = risk_engine
        self._approval_gateway = approval_gateway
        self._verifier = verifier
        self._llm_intent = llm_intent
        self._llm_responder = llm_responder
        self._case_manager = case_manager
        self._case_investigator = case_investigator
        self._treatment_planner = treatment_planner

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
    @property
    def risk_engine(self) -> RiskEngineLike | None:
        return self._risk_engine

    @property
    def approval_gateway(self) -> ApprovalGatewayLike | None:
        return self._approval_gateway

    @property
    def verifier(self) -> ToolVerifierLike | None:
        return self._verifier

    @property
    def case_manager(self) -> AfterSalesCaseManagerLike | None:
        return self._case_manager

    @property
    def case_investigator(self) -> CaseInvestigatorLike | None:
        return self._case_investigator

    @property
    def treatment_planner(self) -> AfterSalesTreatmentPlannerLike | None:
        return self._treatment_planner


    def run(
        self,
        request_id: str,
        user_message: str,
        *,
        user_id: int | None = None,
        session_id: str | None = None,
        user_confirmed: bool | None = None,
        history: list[dict[str, str]] | None = None,
        active_case_id: str | None = None,
    ) -> AgentResult:
        """Run the workflow and return the Response State (AgentResult)."""
        _, result = self.execute(
            request_id,
            user_message,
            user_id=user_id,
            session_id=session_id,
            user_confirmed=user_confirmed,
            history=history,
            active_case_id=active_case_id,
        )
        return result

    def execute(
        self,
        request_id: str,
        user_message: str,
        *,
        user_id: int | None = None,
        session_id: str | None = None,
        user_confirmed: bool | None = None,
        history: list[dict[str, str]] | None = None,
        active_case_id: str | None = None,
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
            result = self._execute_inner(
                state,
                user_confirmed=user_confirmed,
                history=history,
                active_case_id=active_case_id,
            )
            result = self._with_llm_response(state, result, history=history)
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
        if state.run_status is AgentRunStatus.RUNNING:
            if result.status is AgentResultStatus.SUCCESS or result.status is AgentResultStatus.TOOL_REQUESTED:
                state.run_status = AgentRunStatus.COMPLETED
            elif result.status is AgentResultStatus.ERROR:
                state.run_status = AgentRunStatus.FAILED
        return state, result

    # -- internals ----------------------------------------------------------

    def _execute_inner(
        self,
        state: AgentState,
        *,
        user_confirmed: bool | None = None,
        history: list[dict[str, str]] | None = None,
        active_case_id: str | None = None,
    ) -> AgentResult:
        state.status = WorkflowStage.UNDERSTAND
        proposal: LLMIntentProposal | None = None
        if self._llm_intent is not None:
            proposal = self._llm_intent.understand(state.user_message)
        deterministic_entities = self._entity_extractor.extract(state.user_message)
        state.entities = self._merge_entities(proposal, deterministic_entities)

        state.status = WorkflowStage.CLASSIFY_INTENT
        if proposal is not None:
            state.intent = proposal.intent_enum
            state.intent_confidence = proposal.confidence
        else:
            intent_result = self._classifier.classify(state.user_message)
            state.intent = intent_result.intent
            state.intent_confidence = intent_result.confidence

        # Phase 9B: after-sales case upsert. Gated on the intents the existing
        # pipeline cannot handle, so every existing intent -> route behaviour
        # (RAG / Order / Logistics / Cancel / Refund / Ticket) is untouched.
        if self._case_manager is not None and state.intent in (
            Intent.UNSUPPORTED,
            Intent.AMBIGUOUS,
        ):
            case_result = self._run_case_management(state, active_case_id=active_case_id)
            if case_result is not None:
                return case_result

        state.status = WorkflowStage.ROUTE
        decision = self._router.decide(
            state.intent,
            state.entities,
            user_id=state.user_id,
            user_message=state.user_message,
        )
        state.route = decision.route

        if decision.route is Route.RAG:
            return self._run_rag(state)
        if decision.route in _ROUTE_TOOL_SPEC:
            return self._run_tool_branch(state, user_confirmed=user_confirmed)
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

    def _run_case_management(
        self, state: AgentState, *, active_case_id: str | None = None
    ) -> AgentResult | None:
        """Phase 9B Case Upsert + Information Collection (+ 9C investigation).

        Returns None when the message is not an after-sales case request (the
        workflow then continues on its normal path) or when the case service
        fails - a case-service error must never break the chat flow, so it is
        logged and the deterministic path wins.
        """
        state.status = WorkflowStage.CASE_MANAGEMENT
        try:
            outcome: AfterSalesCaseOutcome | None = self._case_manager.handle(
                user_id=state.user_id,
                session_id=state.session_id,
                user_message=state.user_message,
                entities=state.entities,
                active_case_id=active_case_id,
            )
        except Exception as exc:  # guarded boundary: chat must keep working
            logger.warning("after-sales case management failed: %s", type(exc).__name__)
            return None
        if outcome is None:
            return None

        case = outcome.to_dict()
        state.after_sales_case = case
        state.intent = Intent.AFTER_SALES_REQUEST
        state.route = Route.AFTER_SALES_CASE
        if outcome.needs_information:
            return AgentResult(
                status=AgentResultStatus.NEEDS_CLARIFICATION,
                intent=state.intent,
                route=state.route,
                needs_clarification=True,
                after_sales_case=case,
            )

        # Phase 9C: a complete case (ELIGIBILITY_CHECK) is investigated against
        # the real business data + the retrieved policy evidence. The conclusion
        # comes from the deterministic EligibilityEngine, never from the LLM
        # (docs/DECISIONS.md Decision 045).
        investigation = self._run_case_investigation(state, outcome)
        if investigation is None:
            return AgentResult(
                status=AgentResultStatus.SUCCESS,
                intent=state.intent,
                route=state.route,
                after_sales_case=case,
            )

        case = investigation.case.to_dict()
        state.after_sales_case = case
        state.after_sales_eligibility = investigation.eligibility
        state.after_sales_investigation = investigation.investigation

        # Phase 9D: an eligible case additionally gets a deterministic treatment
        # plan and an after-sales ticket. Nothing is executed here - the plan
        # only records what a later phase must do (docs/DECISIONS.md Decision 047).
        treatment = self._run_case_treatment(state, investigation)
        if treatment is not None:
            case = treatment.case.to_dict()
            state.after_sales_case = case
            state.after_sales_treatment = treatment.treatment
            state.after_sales_ticket = treatment.ticket

        return AgentResult(
            status=(
                AgentResultStatus.NEEDS_CLARIFICATION
                if investigation.needs_information
                else AgentResultStatus.SUCCESS
            ),
            intent=state.intent,
            route=state.route,
            needs_clarification=investigation.needs_information,
            after_sales_case=case,
            after_sales_eligibility=investigation.eligibility,
            after_sales_investigation=investigation.investigation,
            after_sales_treatment=state.after_sales_treatment,
            after_sales_ticket=state.after_sales_ticket,
        )

    def _run_case_treatment(
        self, state: AgentState, investigation: AfterSalesInvestigationOutcome
    ) -> AfterSalesTreatmentOutcome | None:
        """Phase 9D Treatment Planning -> Ticket Creation (no execution).

        Runs only for a case whose deterministic eligibility is a definite
        True: a rejected or inconclusive case must not get an execution ticket.
        The injected planner owns the treatment rules and the TicketService; a
        failure must never break the chat flow and is never reported as a
        successfully created ticket.
        """
        if self._treatment_planner is None:
            return None
        eligibility = (
            investigation.eligibility
            if isinstance(investigation.eligibility, dict)
            else {}
        )
        if eligibility.get("eligible") is not True:
            return None
        if investigation.status != STATUS_PROCESSING:
            return None
        state.status = WorkflowStage.CASE_TREATMENT
        try:
            return self._treatment_planner.plan_and_register(
                investigation.case, eligibility
            )
        except Exception as exc:  # guarded boundary: chat must keep working
            logger.warning(
                "after-sales treatment planning failed: %s", type(exc).__name__
            )
            return None

    def _run_case_investigation(
        self, state: AgentState, outcome: AfterSalesCaseOutcome
    ) -> AfterSalesInvestigationOutcome | None:
        """Phase 9C Order + Policy Investigation -> Eligibility Check.

        The injected investigator owns OrderService, the existing RAG pipeline
        and the deterministic EligibilityEngine; the agent layer only receives
        the serialized result. A failing investigation must never break the
        chat flow: it is logged and the case keeps its ELIGIBILITY_CHECK state.
        """
        if self._case_investigator is None:
            return None
        if outcome.status != STATUS_ELIGIBILITY_CHECK:
            return None
        state.status = WorkflowStage.CASE_INVESTIGATION
        try:
            return self._case_investigator.investigate(outcome)
        except Exception as exc:  # guarded boundary: chat must keep working
            logger.warning(
                "after-sales eligibility investigation failed: %s",
                type(exc).__name__,
            )
            return None

    @staticmethod
    def _merge_entities(
        proposal: LLMIntentProposal | None,
        deterministic: ExtractedEntities,
    ) -> ExtractedEntities:
        """LLM entities fill gaps; the deterministic no-guess rule wins."""
        # Lazy import: app.llm.nlu -> app.agent would create an import cycle at
        # module load time (app.agent/__init__ -> app.agent.workflow).
        from app.llm.nlu import proposal_entities
        if proposal is None:
            return deterministic
        llm_entities = proposal_entities(proposal)
        order_id = deterministic.order_id
        if (
            not deterministic.has_multiple_order_ids
            and llm_entities is not None
            and llm_entities.order_id
        ):
            order_id = llm_entities.order_id
        tracking_number = deterministic.tracking_number
        if (
            tracking_number is None
            and llm_entities is not None
            and llm_entities.tracking_number
        ):
            tracking_number = llm_entities.tracking_number
        return ExtractedEntities(
            order_id=order_id,
            tracking_number=tracking_number,
            has_multiple_order_ids=deterministic.has_multiple_order_ids,
            has_multiple_tracking_numbers=deterministic.has_multiple_tracking_numbers,
        )

    def _with_llm_response(
        self,
        state: AgentState,
        result: AgentResult,
        *,
        user_message: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> AgentResult:
        """Attach the LLM final response to a successful answerable run.

        Evidence comes only from what the workflow already collected (retrieved
        knowledge and/or authoritative ToolResults). Any responder failure
        returns None and keeps the deterministic response text.
        """
        if result.status is not AgentResultStatus.SUCCESS or self._llm_responder is None:
            return result
        knowledge: list[dict[str, object]] = []
        if state.retrieved_context is not None:
            knowledge = [
                {
                    "title": item.title,
                    "version": item.version,
                    "section": item.section,
                    "citation": item.citation,
                    "content": item.content,
                }
                for item in state.retrieved_context.items
            ]
        evidence: dict[str, object] = {
            "knowledge": knowledge,
            "tools": [dict(item) for item in state.tool_results],
        }
        if not knowledge and not state.tool_results:
            return result
        message = state.user_message if user_message is None else user_message
        text = self._llm_responder.respond(message, evidence, history=history)
        if not text:
            return result
        return replace(result, response=text)

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

    def _run_tool_branch(self, state: AgentState, *, user_confirmed: bool | None = None) -> AgentResult:
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
        if self._risk_engine is None:
            return self._execute_tool_plan(state, [request])
        return self._execute_risk_gated(state, [request], user_confirmed=user_confirmed)

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

    # -- Phase 5: Risk Gate -----------------------------------------------

    def _execute_risk_gated(
        self,
        state: AgentState,
        requests: list[ToolRequest],
        *,
        user_confirmed: bool | None = None,
    ) -> AgentResult:
        """ToolRequest -> Risk Engine -> Risk Gate -> Tool Executor -> Verify.

        LOW:        AUTO_EXECUTE (run immediately).
        MEDIUM:     USER_CONFIRM -> WAITING_USER_CONFIRMATION until the user
                    confirms; confirmed=True executes, confirmed=False rejects.
        HIGH / CRITICAL: HUMAN_APPROVAL -> a PENDING approval is persisted and
                    the run stops with WAITING_HUMAN_APPROVAL. resume_after_
                    approval() later resumes the ORIGINAL ToolRequest.
        """
        plan = list(requests)
        processed: list[ToolRequest] = []
        results: list[ToolResult] = []
        while plan:
            request = plan.pop(0)
            state.status = WorkflowStage.TOOL_EXECUTION
            decision = self._evaluate_risk(state, request, results)
            # Observability (Phase 7C): keep every Risk Gate decision on the
            # AgentState so payloads/traces and the Evaluation runner can see
            # the exact gate result for each tool request.
            state.risk_decisions = state.risk_decisions + (
                {
                    "tool": request.tool_name,
                    "risk_level": decision.risk_level.value,
                    "risk_action": decision.action.value,
                    "policy_id": decision.policy_id,
                    "reason": decision.reason,
                },
            )
            if decision.action is RiskAction.AUTO_EXECUTE:
                stop = self._execute_auto_or_confirmed(state, request, processed, results)
                if stop is not None:
                    return stop
                if (
                    processed[-1].tool_name == "check_refund_eligibility"
                    and results[-1].status is ToolResultStatus.SUCCESS
                    and bool(results[-1].data.get("eligible"))
                ):
                    plan.append(_plan_refund_execution_request(state))
                continue
            if decision.action is RiskAction.USER_CONFIRM:
                if user_confirmed is None:
                    return self._wait_user_confirmation(state, request, processed, results)
                if user_confirmed is False:
                    pending = replace(request)  # stays PENDING: never executed
                    state.tool_requests = tuple(processed) + (pending,)
                    state.run_status = AgentRunStatus.REJECTED
                    return AgentResult(
                        status=AgentResultStatus.REJECTED,
                        intent=state.intent,
                        route=state.route,
                        tool_requests=tuple(processed) + (pending,),
                        confirmation_message=_confirmation_message(request),
                    )
                stop = self._execute_auto_or_confirmed(state, request, processed, results)
                if stop is not None:
                    return stop
                continue
            if decision.action is RiskAction.HUMAN_APPROVAL:
                return self._wait_human_approval(state, request, decision, processed, results)
            # RiskAction.BLOCK: operations without a policy rule never execute.
            pending = replace(request)
            state.tool_requests = tuple(processed) + (pending,)
            state.run_status = AgentRunStatus.FAILED
            return AgentResult(
                status=AgentResultStatus.ERROR,
                intent=state.intent,
                route=state.route,
                tool_requests=tuple(processed) + (pending,),
                error=f"Operation blocked by risk policy: {decision.reason}",
            )
        state.run_status = AgentRunStatus.COMPLETED
        return AgentResult(
            status=AgentResultStatus.SUCCESS,
            intent=state.intent,
            route=state.route,
            tool_requests=tuple(processed),
        )

    def _evaluate_risk(
        self, state: AgentState, request: ToolRequest, results: list[ToolResult]
    ) -> RiskDecision:
        """Build the business context (never user-controlled) and ask the engine."""
        refund_amount = None
        if request.tool_name == "create_refund":
            for item in results:
                if item.tool_name == "check_refund_eligibility":
                    amount = item.data.get("refund_amount")
                    if amount is not None:
                        refund_amount = Decimal(str(amount))
                    break
        context = RiskContext(
            request_id=state.request_id,
            user_id=state.user_id,
            refund_amount=refund_amount,
        )
        return self._risk_engine.evaluate(request.tool_name, context)

    def _execute_auto_or_confirmed(
        self,
        state: AgentState,
        request: ToolRequest,
        processed: list[ToolRequest],
        results: list[ToolResult],
    ) -> AgentResult | None:
        """Run one gated request through the executor and verify when needed."""
        result = self._execute_one_tool(state, request)
        request = replace(
            request,
            status=ToolRequestStatus.EXECUTED if result.success else ToolRequestStatus.FAILED,
        )
        processed.append(request)
        results.append(result)
        state.tool_requests = tuple(processed)
        state.tool_results = tuple(item.to_dict() for item in results)
        if not result.success:
            state.run_status = AgentRunStatus.FAILED
            return AgentResult(
                status=AgentResultStatus.ERROR,
                intent=state.intent,
                route=state.route,
                tool_requests=tuple(processed),
                error=result.error_message or f"Tool {result.tool_name} failed: {result.status.value}",
            )
        if self._verifier is not None and request.tool_name in ("create_refund", "cancel_order"):
            try:
                self._verifier.verify(request.tool_name, result.data)
            except Exception as exc:  # any verify failure ends the run
                state.run_status = AgentRunStatus.VERIFICATION_FAILED
                return AgentResult(
                    status=AgentResultStatus.VERIFICATION_FAILED,
                    intent=state.intent,
                    route=state.route,
                    tool_requests=tuple(processed),
                    error=f"Verification failed: {exc}",
                )
        return None

    def _wait_user_confirmation(
        self,
        state: AgentState,
        request: ToolRequest,
        processed: list[ToolRequest],
        results: list[ToolResult],
    ) -> AgentResult:
        pending = replace(request)
        state.tool_requests = tuple(processed) + (pending,)
        state.tool_results = tuple(item.to_dict() for item in results)
        state.run_status = AgentRunStatus.WAITING_USER_CONFIRMATION
        return AgentResult(
            status=AgentResultStatus.WAITING_USER_CONFIRMATION,
            intent=state.intent,
            route=state.route,
            tool_requests=tuple(processed) + (pending,),
            confirmation_message=_confirmation_message(request),
        )

    def _wait_human_approval(
        self,
        state: AgentState,
        request: ToolRequest,
        decision: RiskDecision,
        processed: list[ToolRequest],
        results: list[ToolResult],
    ) -> AgentResult:
        if self._approval_gateway is None:
            state.run_status = AgentRunStatus.FAILED
            return AgentResult(
                status=AgentResultStatus.ERROR,
                intent=state.intent,
                route=state.route,
                error=(
                    "Human approval is required but no approval gateway is configured."
                ),
            )
        created = self._approval_gateway.create(
            request_id=state.request_id,
            tool_name=request.tool_name,
            tool_arguments=dict(request.arguments),
            risk_level=decision.risk_level.value,
            reason=decision.reason,
            user_id=state.user_id,
        )
        # ApprovalService returns an ApprovalView; a stub may return an int.
        approval_id = created.id if not isinstance(created, int) else created
        pending = replace(request)
        state.tool_requests = tuple(processed) + (pending,)
        state.tool_results = tuple(item.to_dict() for item in results)
        state.run_status = AgentRunStatus.WAITING_HUMAN_APPROVAL
        return AgentResult(
            status=AgentResultStatus.WAITING_HUMAN_APPROVAL,
            intent=state.intent,
            route=state.route,
            tool_requests=tuple(processed) + (pending,),
            approval_id=approval_id,
        )

    def resume_after_approval(
        self,
        approval_id: int,
        *,
        approved: bool,
        resolved_by: str | None = None,
        user_message: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> tuple[AgentState, AgentResult]:
        """Resume the ORIGINAL ToolRequest bound to an approval.

        approve -> resolve -> Tool Executor -> Verify (snapshot is executed);
        reject  -> REJECTED, nothing is executed. Arguments are never re-planned.
        """
        if self._approval_gateway is None:
            return self._resume_error(approval_id, "No approval gateway is configured.")
        try:
            view = self._approval_gateway.get(approval_id)
        except Exception as exc:  # real gateway raises NotFoundError on missing ids
            return self._resume_error(approval_id, str(exc))
        if view is None:
            return self._resume_error(approval_id, "Approval request not found.")
        try:
            if approved:
                self._approval_gateway.approve(approval_id, resolved_by)
            else:
                self._approval_gateway.reject(approval_id, resolved_by)
        except Exception as exc:  # already resolved / invalid transition
            return self._resume_error(approval_id, str(exc))

        view = self._approval_gateway.get(approval_id)
        state = AgentState(
            request_id=view.request_id or f"resume-{approval_id}",
            user_message="",
            user_id=view.user_id,
        )
        state.status = WorkflowStage.TOOL_EXECUTION
        if not approved:
            state.run_status = AgentRunStatus.REJECTED
            return state, AgentResult(
                status=AgentResultStatus.REJECTED,
                route=state.route,
                tool_requests=(),
            )
        request = ToolRequest(
            tool_name=view.tool_name,
            arguments=dict(view.tool_arguments),
            reason="Approved by human; resuming the original ToolRequest snapshot.",
            requires_confirmation=False,
        )
        processed: list[ToolRequest] = []
        results: list[ToolResult] = []
        stop = self._execute_auto_or_confirmed(state, request, processed, results)
        if stop is not None:
            return state, stop
        state.run_status = AgentRunStatus.COMPLETED
        result = AgentResult(
            status=AgentResultStatus.SUCCESS,
            route=state.route,
            tool_requests=tuple(processed),
        )
        result = self._with_llm_response(
            state, result, user_message=user_message, history=history
        )
        state.response = result.response
        return state, result

    def _resume_error(self, approval_id: int, message: str) -> tuple[AgentState, AgentResult]:
        state = AgentState(request_id=f"resume-{approval_id}", user_message="")
        state.run_status = AgentRunStatus.FAILED
        return state, AgentResult(
            status=AgentResultStatus.ERROR,
            error=message,
        )


def _display_order_ref(arguments: dict) -> str:
    """Render an order reference for user-facing confirmation messages."""
    value = (arguments or {}).get("order_id")
    if value is None:
        return ""
    text = str(value).strip()
    if text.upper().startswith("ORD"):
        return text
    return f"ORD-{text}"


def _confirmation_message(request: ToolRequest) -> str:
    """Message shown before a MEDIUM (USER_CONFIRM) action executes."""
    ref = _display_order_ref(request.arguments)
    if not ref:
        return "该操作需要您确认后才能执行，请确认是否继续？"
    return f"取消订单 {ref} 将导致订单进入取消状态，请确认是否继续？"


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
