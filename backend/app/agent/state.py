"""Agent domain state (Phase 4A).

This module owns the typed vocabulary of the agent layer:

    Intent            what the user wants (KNOWLEDGE_QA ... AMBIGUOUS)
    Route             which pipeline step should run next (RAG / *TOOL /
                      CLARIFY / ESCALATE)
    WorkflowStage     observable position inside the workflow
    AgentState        mutable per-request working state
    AgentResult       final Response State handed to later phases
    ToolRequest       planned tool call (never executed in Phase 4A)

Boundary rules:
    - FastAPI- and framework-agnostic: no LangGraph import, no SQLAlchemy
      import, no retrieval import needed at runtime.
    - Intent and Route are deliberately separate enums (see router.py and
      docs/DECISIONS.md Decision 019).
    - AgentResult/Response State is a structured object, never only a string,
      so later LLM response generation has citations / tool requests /
      clarification / escalation signals available.
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent.entities import ExtractedEntities

if TYPE_CHECKING:
    from app.retrieval.context import ContextPackage

JsonDict = dict[str, Any]


class Intent(str, enum.Enum):
    """What the user wants. Values never leak as raw strings in code."""

    KNOWLEDGE_QA = "KNOWLEDGE_QA"
    ORDER_STATUS = "ORDER_STATUS"
    LOGISTICS_TRACKING = "LOGISTICS_TRACKING"
    REFUND_INQUIRY = "REFUND_INQUIRY"
    REFUND_REQUEST = "REFUND_REQUEST"
    CANCEL_ORDER = "CANCEL_ORDER"
    CREATE_TICKET = "CREATE_TICKET"
    UNSUPPORTED = "UNSUPPORTED"
    AMBIGUOUS = "AMBIGUOUS"


class Route(str, enum.Enum):
    """The next pipeline step decided by the router (NOT the intent)."""

    RAG = "RAG"
    ORDER_TOOL = "ORDER_TOOL"
    LOGISTICS_TOOL = "LOGISTICS_TOOL"
    REFUND_TOOL = "REFUND_TOOL"
    CANCEL_TOOL = "CANCEL_TOOL"
    TICKET_TOOL = "TICKET_TOOL"
    CLARIFY = "CLARIFY"
    ESCALATE = "ESCALATE"


class WorkflowStage(str, enum.Enum):
    """Observable stage of the workflow (START -> ... -> END)."""

    START = "START"
    UNDERSTAND = "UNDERSTAND"
    CLASSIFY_INTENT = "CLASSIFY_INTENT"
    ROUTE = "ROUTE"
    RAG = "RAG"
    BUSINESS_TOOL_REQUEST = "BUSINESS_TOOL_REQUEST"
    TOOL_EXECUTION = "TOOL_EXECUTION"
    CLARIFY = "CLARIFY"
    ESCALATE = "ESCALATE"
    FINALIZE = "FINALIZE"
    END = "END"


class ToolRequestStatus(str, enum.Enum):
    """Lifecycle of a planned tool request.

    Phase 4A only creates PENDING requests. Phase 4B marks executed requests
    EXECUTED (tool ran and returned SUCCESS) or FAILED (tool ran but did not
    succeed); planning-only runs keep PENDING.
    """

    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"


class AgentResultStatus(str, enum.Enum):
    """High-level outcome of one workflow run (Response State)."""

    SUCCESS = "success"
    TOOL_REQUESTED = "tool_requested"
    NEEDS_CLARIFICATION = "needs_clarification"
    ESCALATION_REQUIRED = "escalation_required"
    ERROR = "error"


@dataclass(frozen=True)
class ToolRequest:
    """A planned business tool call (interface only - never executed here).

    Fields:
        tool_name:            Phase 4B tool id (get_order / get_logistics /
                              check_refund_eligibility / create_refund /
                              cancel_order / create_ticket).
        arguments:            entity-derived arguments prepared by the agent.
        reason:               why the workflow planned this call.
        requires_confirmation:whether execution must ask the user again.
        status:               PENDING in Phase 4A.
    """

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    requires_confirmation: bool = False
    status: ToolRequestStatus = ToolRequestStatus.PENDING

    def to_dict(self) -> JsonDict:
        return {
            "tool_name": self.tool_name,
            "arguments": dict(self.arguments),
            "reason": self.reason,
            "requires_confirmation": self.requires_confirmation,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "ToolRequest":
        status = ToolRequestStatus(str(data.get("status", ToolRequestStatus.PENDING.value)))
        return cls(
            tool_name=str(data["tool_name"]),
            arguments=dict(data.get("arguments") or {}),
            reason=str(data.get("reason", "")),
            requires_confirmation=bool(data.get("requires_confirmation", False)),
            status=status,
        )


@dataclass(frozen=True)
class AgentResult:
    """Response State produced by the workflow (not a bare string).

    Later phases generate natural language from this state; the structured
    fields (citations, tool_requests, clarification / escalation flags) are
    the contract those phases consume.
    """

    status: AgentResultStatus
    response: str | None = None
    intent: Intent | None = None
    route: Route | None = None
    citations: tuple[str, ...] = ()
    tool_requests: tuple[ToolRequest, ...] = ()
    needs_clarification: bool = False
    escalation_required: bool = False
    error: str | None = None

    def to_dict(self) -> JsonDict:
        return {
            "status": self.status.value,
            "response": self.response,
            "intent": self.intent.value if self.intent else None,
            "route": self.route.value if self.route else None,
            "citations": list(self.citations),
            "tool_requests": [request.to_dict() for request in self.tool_requests],
            "needs_clarification": self.needs_clarification,
            "escalation_required": self.escalation_required,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "AgentResult":
        return cls(
            status=AgentResultStatus(str(data["status"])),
            response=data.get("response"),
            intent=Intent(str(data["intent"])) if data.get("intent") else None,
            route=Route(str(data["route"])) if data.get("route") else None,
            citations=tuple(str(item) for item in (data.get("citations") or [])),
            tool_requests=tuple(
                ToolRequest.from_dict(item) for item in (data.get("tool_requests") or [])
            ),
            needs_clarification=bool(data.get("needs_clarification", False)),
            escalation_required=bool(data.get("escalation_required", False)),
            error=data.get("error"),
        )


@dataclass
class AgentState:
    """Strongly typed per-request working state.

    Fields follow the Phase 4A spec (request_id / session_id / user_id /
    user_message / intent / intent_confidence / route / retrieved_context /
    tool_requests / tool_results / response / error / status) plus the typed
    entities needed to prepare ToolRequest arguments. No speculative fields.
    """

    request_id: str
    user_message: str
    session_id: str | None = None
    user_id: int | None = None
    intent: Intent | None = None
    intent_confidence: float | None = None
    route: Route | None = None
    entities: ExtractedEntities | None = None
    retrieved_context: ContextPackage | None = None
    tool_requests: tuple[ToolRequest, ...] = ()
    tool_results: tuple[dict[str, Any], ...] = ()
    response: str | None = None
    error: str | None = None
    status: WorkflowStage = WorkflowStage.START

    def to_dict(self) -> JsonDict:
        entities: JsonDict | None = None
        if isinstance(self.entities, ExtractedEntities):
            entities = self.entities.to_dict()
        context_summary: JsonDict | None = None
        if self.retrieved_context is not None:
            package = self.retrieved_context
            context_summary = {
                "query": package.query,
                "total_items": package.total_items,
                "included_items": len(package.items),
                "truncated": package.truncated,
                "estimated_tokens": package.estimated_tokens,
                "citations": [item.citation for item in package.items],
            }
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "user_message": self.user_message,
            "intent": self.intent.value if self.intent else None,
            "intent_confidence": self.intent_confidence,
            "route": self.route.value if self.route else None,
            "entities": entities,
            "retrieved_context": context_summary,
            "tool_requests": [request.to_dict() for request in self.tool_requests],
            "tool_results": [dict(item) for item in self.tool_results],
            "response": self.response,
            "error": self.error,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "AgentState":
        state = cls(
            request_id=str(data["request_id"]),
            user_message=str(data["user_message"]),
            session_id=data.get("session_id"),
            user_id=data.get("user_id"),
            intent=Intent(str(data["intent"])) if data.get("intent") else None,
            intent_confidence=data.get("intent_confidence"),
            route=Route(str(data["route"])) if data.get("route") else None,
            tool_requests=tuple(
                ToolRequest.from_dict(item) for item in (data.get("tool_requests") or [])
            ),
            tool_results=tuple(
                dict(item) for item in (data.get("tool_results") or [])
            ),
            response=data.get("response"),
            error=data.get("error"),
            status=WorkflowStage(str(data.get("status", WorkflowStage.START.value))),
        )
        entities_data = data.get("entities")
        if isinstance(entities_data, dict):
            state.entities = ExtractedEntities.from_dict(entities_data)
        # retrieved_context is a runtime-only structured object; serialization
        # keeps a summary (see to_dict). Deserialization intentionally restores
        # the serializable contract with retrieved_context=None.
        return state
