"""LLM final response generation (Phase 7B).

Placed AFTER the deterministic workflow: RAG retrieval / Tool execution / Risk
Gate / Approval / Execute / Verify have already happened and produced
authoritative evidence. The LLM only converts that evidence into a natural
language answer. It never runs tools, never touches the database and never
changes business state.

Grounding rules enforced by the prompt + by construction here:
    - Only evidence actually collected by the workflow is passed to the model.
    - Empty evidence => no call (deterministic fallback text is kept).
    - Any provider/parsing failure returns None and the caller keeps the
      deterministic response - the agent never fabricates success.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.llm.base import ChatMessage, LLMProvider
from app.llm.errors import LLMError
from app.llm.prompts import (
    CUSTOMER_SERVICE_SYSTEM_PROMPT,
    FINAL_RESPONSE_INSTRUCTIONS,
)

logger = logging.getLogger(__name__)

JsonDict = dict[str, Any]

_MAX_KNOWLEDGE_ITEMS = 8
_MAX_TOOL_RESULTS = 3
_MAX_CONTENT_CHARS = 7000
_ROLES = frozenset({"system", "user", "assistant"})


def build_evidence_block(evidence: JsonDict) -> str:
    """Render workflow-collected evidence as compact, traceable fact lines."""
    knowledge = list(evidence.get("knowledge") or [])[:_MAX_KNOWLEDGE_ITEMS]
    tools = list(evidence.get("tools") or [])[:_MAX_TOOL_RESULTS]
    lines: list[str] = []
    for item in knowledge:
        title = str(item.get("title") or "未知文档")
        content = str(item.get("content") or "")
        citation = item.get("citation")
        head = f"[知识]《{title}》"
        if item.get("version"):
            head += f" v{item['version']}"
        if citation:
            head += f"({citation})"
        lines.append(f"{head}\n{content}")
    for tool in tools:
        name = str(tool.get("tool_name") or "?")
        data = tool.get("data") or {}
        if tool.get("status") not in (None, "SUCCESS"):
            lines.append(f"[工具 {name}] 执行未成功:{tool.get('error_message') or tool.get('status')}")
            continue
        try:
            rendered = json.dumps(data, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            rendered = str(data)
        lines.append(f"[工具 {name}] 权威结果:\n{rendered}")
    block = "\n".join(lines)
    if len(block) > _MAX_CONTENT_CHARS:
        block = block[:_MAX_CONTENT_CHARS] + "\n…(内容过长已截断)"
    return block


def _clean_history(history: list[JsonDict] | None, limit: int = 6) -> list[ChatMessage]:
    cleaned: list[ChatMessage] = []
    for turn in (history or [])[-limit:]:
        if not isinstance(turn, dict):
            continue
        role = str(turn.get("role") or "")
        content = turn.get("content")
        if role not in _ROLES or not isinstance(content, str) or not content.strip():
            continue
        cleaned.append({"role": role, "content": content.strip()[:800]})
    return cleaned


class FinalResponder:
    """Turn authoritative evidence into the final natural-language answer."""

    def __init__(self, provider: LLMProvider, *, max_tokens: int = 512) -> None:
        self._provider = provider
        self._max_tokens = max_tokens

    def respond(
        self,
        user_message: str,
        evidence: JsonDict | None,
        *,
        history: list[JsonDict] | None = None,
    ) -> str | None:
        block = build_evidence_block(evidence or {})
        if not block:
            return None
        user_content = (
            f"用户问题:\n{user_message}\n\n"
            f"已知事实(仅来自检索或业务工具的权威结果):\n{block}\n\n"
            f"{FINAL_RESPONSE_INSTRUCTIONS}"
        )
        messages: list[ChatMessage] = [{"role": "system", "content": CUSTOMER_SERVICE_SYSTEM_PROMPT}]
        messages.extend(_clean_history(history))
        messages.append({"role": "user", "content": user_content})
        try:
            raw = self._provider.generate(
                messages,
                temperature=0.3,
                max_tokens=self._max_tokens,
                json_mode=False,
            )
        except LLMError as exc:
            logger.warning("LLM final response unavailable (%s)", exc.code.value)
            return None
        except Exception as exc:  # defensive: keep the deterministic answer
            logger.warning("LLM final response failed: %s", type(exc).__name__)
            return None
        cleaned = raw.strip()
        return cleaned or None
