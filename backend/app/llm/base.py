"""LLM provider abstraction (Phase 7B).

The Agent only depends on this narrow Protocol. DeepSeekProvider is one
implementation; tests inject lightweight fakes behind the same interface so
the deterministic workflow can be verified with zero network access.

Boundary rules:
    - generate() turns Prompt/messages -> DeepSeek-compatible HTTP call -> text.
    - The provider never queries a database, never calls a repository, never
      modifies orders/refunds, never creates approvals and never calls MCP.
    - The API key is used only in the Authorization header and is never part
      of any exception / log line / trace / payload.
"""
from __future__ import annotations

from typing import Protocol

# OpenAI-compatible message shape: {"role": "system"|"user"|"assistant", "content": ...}
ChatMessage = dict[str, str]


class LLMProvider(Protocol):
    """Minimal OpenAI-compatible completion provider."""

    provider_name: str
    model: str

    def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 300,
        json_mode: bool = False,
    ) -> str:
        """Send a chat completion request and return the text response.

        Raises:
            LLMError: normalized classification (timeout/auth/rate/provider/
                      invalid output/config). No raw API exception escapes.
        """
        ...
