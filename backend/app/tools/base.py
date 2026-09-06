"""Tool layer contracts (Phase 4B).

Owns the stable vocabulary shared by the tool registry / executor / handlers:

    RiskLevel             metadata only; a real risk engine lands in Phase 5
    ToolResultStatus      outcome of one tool execution
    ToolResult            stable domain result (never an ORM object)
    ToolExecutionContext  trusted per-request context (request/session/user)
    ToolDefinition        registry entry (name / schema / risk / handler)

Boundary rules:
    - This module imports only the standard library: the Tool layer contract
      stays lightweight so the Agent layer can depend on it without pulling
      SQLAlchemy / services into import graphs.
    - ToolResult.data is a JSON-serializable dict (or a Pydantic output model
      serialized before it enters AgentState), never an ORM instance.
    - user_id in ToolExecutionContext is the trusted identity. Model-provided
      arguments must never override it (see docs/DECISIONS.md).
"""
from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from pydantic import BaseModel

JsonDict = dict[str, Any]


class RiskLevel(str, enum.Enum):
    """Static risk metadata for a tool. Phase 5 consumes it; no engine yet."""

    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ToolResultStatus(str, enum.Enum):
    """Normalized outcome of one tool execution."""

    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    BUSINESS_ERROR = "BUSINESS_ERROR"


@dataclass(frozen=True)
class ToolExecutionContext:
    """Trusted per-request context passed to every tool execution.

    request_id / session_id come from the Agent request; user_id MUST come
    from the authenticated session, never from model-generated arguments.
    """

    request_id: str
    session_id: str | None = None
    user_id: int | None = None

    def to_dict(self) -> JsonDict:
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "ToolExecutionContext":
        return cls(
            request_id=str(data["request_id"]),
            session_id=data.get("session_id"),
            user_id=data.get("user_id"),
        )


@dataclass(frozen=True)
class ToolResult:
    """Normalized result of one tool execution (stable domain object).

    Fields:
        tool_name:     registered tool that produced this result
        status:        normalized outcome (see ToolResultStatus)
        data:          JSON-serializable business payload (output schema dump)
        error_code:    stable machine-readable code (e.g. ORDER_NOT_FOUND)
        error_message: safe, user-visible detail (never a traceback)
        metadata:      observability metadata (execution_id / request_id /
                       tool_name / duration_ms / success / risk_level ...)
    """

    tool_name: str
    status: ToolResultStatus
    data: JsonDict = field(default_factory=dict)
    error_code: str | None = None
    error_message: str | None = None
    metadata: JsonDict = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status is ToolResultStatus.SUCCESS

    def to_dict(self) -> JsonDict:
        return {
            "tool_name": self.tool_name,
            "status": self.status.value,
            "data": dict(self.data),
            "error_code": self.error_code,
            "error_message": self.error_message,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: JsonDict) -> "ToolResult":
        return cls(
            tool_name=str(data["tool_name"]),
            status=ToolResultStatus(str(data["status"])),
            data=dict(data.get("data") or {}),
            error_code=data.get("error_code"),
            error_message=data.get("error_message"),
            metadata=dict(data.get("metadata") or {}),
        )


class ToolHandler(Protocol):
    """A tool handler: validate-normalized payload + trusted context -> output.

    Handlers return an instance of the tool's output_schema (or a plain dict).
    They never return ORM objects and never raise raw SQLAlchemy exceptions.
    """

    def __call__(self, arguments: "BaseModel", context: ToolExecutionContext) -> Any:
        """Execute one tool and return a stable output."""
        ...


@dataclass(frozen=True)
class ToolDefinition:
    """Registry entry describing one callable tool.

    input_schema / output_schema are Pydantic models. risk_level is metadata
    only in Phase 4B (Phase 5 builds the risk engine on top of it).
    """

    name: str
    description: str
    input_schema: type
    output_schema: type
    risk_level: RiskLevel = RiskLevel.LOW
    handler: Callable[..., Any] | None = None

    def to_dict(self) -> JsonDict:
        return {
            "name": self.name,
            "description": self.description,
            "risk_level": self.risk_level.value,
        }