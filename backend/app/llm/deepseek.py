"""DeepSeek provider (OpenAI-compatible) - Phase 7B.

Implements the LLMProvider Protocol with a lightweight, explicit HTTP client
(httpx). The DeepSeek REST API is OpenAI-compatible:
POST {base_url}/chat/completions  (messages / model / temperature / max_tokens)

This file deliberately contains NO agent / business / tool logic: it only maps
Prompt/messages -> HTTP call -> text response. No LangChain / LangGraph /
LlamaIndex / CrewAI / AutoGen is used anywhere in the project.
"""
from __future__ import annotations

import json
import logging
import time

import httpx

from app.llm.base import ChatMessage
from app.llm.errors import LLMError, LLMErrorCode

logger = logging.getLogger(__name__)


class DeepSeekProvider:
    """Thin OpenAI-compatible client for the DeepSeek API."""

    provider_name = "DeepSeek"

    def __init__(
        self,
        *,
        api_key: str,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        key = (api_key or "").strip()
        if not key:
            raise LLMError(
                LLMErrorCode.LLM_CONFIG_ERROR,
                "DEEPSEEK_API_KEY is not configured.",
            )
        self._api_key = key
        self.model = model or "deepseek-chat"
        self._base_url = (base_url or "https://api.deepseek.com").rstrip("/")
        self._timeout = timeout_seconds
        # Tests inject an httpx.MockTransport; production uses the real network.
        self._transport = transport

    # -- LLMProvider --------------------------------------------------------

    def generate(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.0,
        max_tokens: int = 300,
        json_mode: bool = False,
    ) -> str:
        """POST /chat/completions and return the assistant text (never the key)."""
        payload: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
                transport=self._transport,
            ) as client:
                response = client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            logger.warning(
                "LLM provider timeout (model=%s, latency_ms=%.0f)",
                self.model,
                (time.monotonic() - started) * 1000,
            )
            raise LLMError(
                LLMErrorCode.LLM_TIMEOUT,
                "The LLM provider request timed out.",
            ) from exc
        except httpx.HTTPError as exc:
            logger.warning(
                "LLM provider network error (model=%s, latency_ms=%.0f)",
                self.model,
                (time.monotonic() - started) * 1000,
            )
            raise LLMError(
                LLMErrorCode.LLM_PROVIDER_ERROR,
                "The LLM provider is unreachable.",
            ) from exc

        latency_ms = round((time.monotonic() - started) * 1000, 1)
        status = response.status_code
        if status == 401 or status == 403:
            logger.error("LLM provider auth error (status=%s)", status)
            raise LLMError(
                LLMErrorCode.LLM_AUTH_ERROR,
                "LLM provider rejected the API key.",
            )
        if status == 429:
            logger.warning("LLM provider rate limited (status=%s)", status)
            raise LLMError(
                LLMErrorCode.LLM_RATE_LIMITED,
                "LLM provider is rate limited.",
            )
        if status >= 500:
            logger.error("LLM provider error (status=%s)", status)
            raise LLMError(
                LLMErrorCode.LLM_PROVIDER_ERROR,
                "LLM provider returned a server error.",
            )
        if status != 200:
            logger.error("LLM provider unexpected status (status=%s)", status)
            raise LLMError(
                LLMErrorCode.LLM_PROVIDER_ERROR,
                "LLM provider returned an unexpected status.",
            )

        try:
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError("empty content")
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            logger.error("LLM provider returned a malformed completion payload")
            raise LLMError(
                LLMErrorCode.LLM_INVALID_OUTPUT,
                "LLM provider returned a malformed completion.",
            ) from exc

        logger.info(
            "LLM provider ok (provider=%s model=%s status=%s latency_ms=%s)",
            self.provider_name,
            self.model,
            status,
            latency_ms,
        )
        return content.strip()

    # -- helpers for tests ------------------------------------------------

    @staticmethod
    def json_text(data: dict) -> str:
        """Render a dict as JSON text for fake/MockTransport test responses."""
        return json.dumps(data, ensure_ascii=False)
