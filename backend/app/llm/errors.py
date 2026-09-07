"""LLM layer errors (Phase 7B).

Every provider / parsing failure is normalized into a stable LLM error code.
Raw SDK/HTTP exceptions or API details must NEVER leak into logs, API
responses, the agent trace or the frontend. The workflow treats any LLMError
as a signal to fall back to the deterministic path.
"""
from __future__ import annotations

import enum


class LLMErrorCode(str, enum.Enum):
    """Stable internal error classification for the LLM provider layer."""

    LLM_CONFIG_ERROR = "LLM_CONFIG_ERROR"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_AUTH_ERROR = "LLM_AUTH_ERROR"
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"
    LLM_INVALID_OUTPUT = "LLM_INVALID_OUTPUT"


# Safe, user-facing copy used by callers when a fallback message is needed.
# The original API error text is never forwarded to the user.
LLM_UNAVAILABLE_MESSAGE = "抱歉,当前 AI 服务暂时不可用,请稍后重试或转人工。"


class LLMError(Exception):
    """Normalized LLM-layer error (safe message only)."""

    def __init__(self, code: LLMErrorCode | str, message: str) -> None:
        self.code = LLMErrorCode(code) if not isinstance(code, LLMErrorCode) else code
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message
