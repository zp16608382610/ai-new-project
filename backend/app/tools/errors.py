"""Tool-layer error types and stable error codes (Phase 4B).

Handlers may raise these domain-adjacent exceptions; the ToolExecutor is the
single place that converts exceptions into normalized ToolResults, so no raw
SQLAlchemy / KeyError / traceback ever reaches the Agent.
"""
from __future__ import annotations


class ToolError(Exception):
    """Base class for tool-layer errors."""

    code: str = "INTERNAL_TOOL_ERROR"

    def __init__(self, detail: str, *, code: str | None = None) -> None:
        super().__init__(detail)
        self.detail = detail
        if code is not None:
            self.code = code


class ToolRegistryError(ToolError):
    """Registry misuse: duplicate registration, invalid definition, ..."""

    code = "TOOL_REGISTRY_ERROR"


class UnauthorizedOrderAccessError(ToolError):
    """The order exists but does not belong to the trusted user."""

    code = "UNAUTHORIZED_ORDER_ACCESS"


class ToolInputError(ToolError):
    """Semantic input problem detected at execution time (e.g. no user_id)."""

    code = "INVALID_ARGUMENTS"


# Stable machine-readable codes referenced by tests and the workflow mapping.
ERROR_ORDER_NOT_FOUND = "ORDER_NOT_FOUND"
ERROR_LOGISTICS_NOT_FOUND = "LOGISTICS_NOT_FOUND"
ERROR_USER_NOT_FOUND = "USER_NOT_FOUND"
ERROR_UNAUTHORIZED_ORDER_ACCESS = "UNAUTHORIZED_ORDER_ACCESS"
ERROR_INVALID_ARGUMENTS = "INVALID_ARGUMENTS"
ERROR_UNKNOWN_TOOL = "UNKNOWN_TOOL"
ERROR_INTERNAL_TOOL_ERROR = "INTERNAL_TOOL_ERROR"