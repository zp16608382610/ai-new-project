"""ToolExecutor (Phase 4B): the single execution path for tools.

Responsibilities (single responsibility - no business rules here):
    1. look the tool up in the registry allowlist
    2. override any model-supplied user_id with the trusted context identity
    3. validate arguments against the tool's Pydantic input schema
    4. execute the handler with a validated payload + ToolExecutionContext
    5. catch every exception and normalize it into a ToolResult
    6. attach observability metadata (execution_id / duration_ms / ...)

The executor never decides business rules, never runs RAG, and never reasons.
"""
from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from pydantic import BaseModel, ValidationError

from app.services.errors import BusinessError, NotFoundError
from app.tools.base import (
    JsonDict,
    ToolDefinition,
    ToolExecutionContext,
    ToolResult,
    ToolResultStatus,
)
from app.tools.errors import (
    ERROR_INTERNAL_TOOL_ERROR,
    ERROR_INVALID_ARGUMENTS,
    ERROR_UNKNOWN_TOOL,
    ToolInputError,
    UnauthorizedOrderAccessError,
)
from app.tools.registry import ToolRegistry

logger = logging.getLogger("app.tools.executor")


class ToolExecutor:
    """Executes registered tools behind one normalized boundary."""

    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    @property
    def registry(self) -> ToolRegistry:
        return self._registry

    def execute(
        self,
        tool_name: str,
        arguments: JsonDict | None,
        context: ToolExecutionContext,
        *,
        requires_confirmation: bool = False,
    ) -> ToolResult:
        """Run one tool call and always return a normalized ToolResult.

        requires_confirmation is preserved as execution metadata (Phase 4B
        records it; Phase 5 builds risk approval on top of it).
        """
        started = time.perf_counter()
        metadata: JsonDict = {
            "execution_id": uuid.uuid4().hex,
            "request_id": context.request_id,
            "session_id": context.session_id,
            "tool_name": tool_name,
            "duration_ms": 0.0,
            "success": False,
            "requires_confirmation": bool(requires_confirmation),
        }

        tool = self._registry.get(tool_name)
        if tool is None:
            return self._result(
                tool_name,
                ToolResultStatus.FAILED,
                error_code=ERROR_UNKNOWN_TOOL,
                error_message=f"Unknown tool '{tool_name}': not present in the registry allowlist.",
                metadata=metadata,
                started=started,
            )
        metadata["risk_level"] = tool.risk_level.value

        # Trusted identity: the context always wins over model arguments.
        args = dict(arguments or {})
        args["user_id"] = context.user_id

        try:
            payload = tool.input_schema(**args)
        except ValidationError as exc:
            return self._result(
                tool_name,
                ToolResultStatus.VALIDATION_ERROR,
                error_code=ERROR_INVALID_ARGUMENTS,
                error_message=f"Tool arguments failed validation: {self._first_validation_error(exc)}",
                metadata=metadata,
                started=started,
            )

        try:
            output = tool.handler(payload, context)
        except ToolInputError as exc:
            return self._result(
                tool_name,
                ToolResultStatus.VALIDATION_ERROR,
                error_code=exc.code,
                error_message=exc.detail,
                metadata=metadata,
                started=started,
            )
        except UnauthorizedOrderAccessError as exc:
            return self._result(
                tool_name,
                ToolResultStatus.FAILED,
                error_code=exc.code,
                error_message=exc.detail,
                metadata=metadata,
                started=started,
            )
        except NotFoundError as exc:
            return self._result(
                tool_name,
                ToolResultStatus.NOT_FOUND,
                error_code=exc.code or "NOT_FOUND",
                error_message=exc.detail,
                metadata=metadata,
                started=started,
            )
        except BusinessError as exc:
            return self._result(
                tool_name,
                ToolResultStatus.BUSINESS_ERROR,
                error_code=exc.code or "BUSINESS_ERROR",
                error_message=exc.detail,
                metadata=metadata,
                started=started,
            )
        except Exception as exc:  # defensive boundary: never leak internals
            logger.exception("Unexpected tool failure: tool=%s", tool_name)
            diagnostic = f"{type(exc).__name__}: {exc}"
            return self._result(
                tool_name,
                ToolResultStatus.FAILED,
                error_code=ERROR_INTERNAL_TOOL_ERROR,
                error_message="Unexpected internal tool error.",
                metadata={**metadata, "diagnostic": diagnostic},
                started=started,
            )

        data = self._serialize_output(output)
        return self._result(
            tool_name,
            ToolResultStatus.SUCCESS,
            data=data,
            metadata=metadata,
            started=started,
        )

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _serialize_output(output: Any) -> JsonDict:
        if isinstance(output, BaseModel):
            return dict(output.model_dump(mode="json"))
        if isinstance(output, dict):
            return dict(output)
        raise TypeError(
            f"Tool handler must return a Pydantic model or dict, got {type(output).__name__}"
        )

    @staticmethod
    def _result(
        tool_name: str,
        status: ToolResultStatus,
        *,
        data: JsonDict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        metadata: JsonDict,
        started: float,
    ) -> ToolResult:
        elapsed_ms = round((time.perf_counter() - started) * 1000, 3)
        finished = dict(metadata)
        finished["duration_ms"] = elapsed_ms
        finished["success"] = status is ToolResultStatus.SUCCESS
        return ToolResult(
            tool_name=tool_name,
            status=status,
            data=data or {},
            error_code=error_code,
            error_message=error_message,
            metadata=finished,
        )

    @staticmethod
    def _first_validation_error(exc: ValidationError) -> str:
        try:
            first = exc.errors()[0]
            location = ".".join(str(part) for part in first.get("loc", ()))
            return f"{location}: {first.get('msg', 'invalid value')}"
        except Exception:  # pragma: no cover - defensive formatting
            return "invalid value"
