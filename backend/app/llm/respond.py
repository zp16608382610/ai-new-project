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


_RISK_ACTION_RENDER = {
    "AUTO_EXECUTE": "AUTO_EXECUTE(系统自动执行,无需人工审批)",
    "USER_CONFIRM": "USER_CONFIRM(等待用户确认)",
    "HUMAN_APPROVAL": "HUMAN_APPROVAL(人工审批之后才执行)",
    "BLOCK": "BLOCK(已拦截,未执行)",
}


def _render_outcome_block(outcome: JsonDict) -> str:
    """Render the workflow's own verdict (Risk Gate / Execute / Verify / approval).

    Phase 9F: the raw ToolResult of a refund carries ``status: PENDING``, which
    means "the record exists, no money moved yet" - not "a human is still
    reviewing". Passing the workflow's own conclusion keeps the model from
    inventing a human-in-the-loop step that never happened.
    """
    if not isinstance(outcome, dict) or not outcome:
        return ""
    lines = ["[系统结论](风控 / 执行 / 校验 / 数据库状态的最终结论,不得改写,也不得据此推断未给出的信息)"]
    risk = outcome.get("risk_action")
    if risk:
        level = outcome.get("risk_level") or "?"
        tool = outcome.get("risk_tool") or "?"
        rendered = _RISK_ACTION_RENDER.get(str(risk), str(risk))
        lines.append(f"- 风控判定:{level} / {rendered}(工具 {tool})")
    approval_id = outcome.get("approval_id")
    resolved_approval_id = outcome.get("resolved_approval_id")
    if approval_id is not None:
        lines.append(
            f"- 人工审批:存在待审批单 #{approval_id}(等待人工审批,尚未执行)"
        )
    elif resolved_approval_id is not None:
        lines.append(
            f"- 人工审批:已由人工审批通过(审批单 #{resolved_approval_id})并将该动作恢复执行,"
            "不是系统自动放行"
        )
    elif risk:
        lines.append("- 人工审批:无(本次请求不需要人工审批,也未创建任何审批单)")
    execution_status = outcome.get("execution_status")
    if execution_status:
        detail = f"- 执行结果:{execution_status}"
        if outcome.get("refund_id") is not None:
            detail += f"(退款记录 REFUND-{outcome['refund_id']}"
            if outcome.get("refund_amount") is not None:
                detail += f",金额 ¥{outcome['refund_amount']}"
            detail += ")"
        lines.append(detail)
    if outcome.get("verification_passed") is not None:
        passed = outcome.get("verification_passed")
        detail = f"- 执行后校验:{'通过(passed=true)' if passed else '未通过(passed=false)'}"
        checked = [str(item) for item in (outcome.get("verification_checked") or [])]
        if checked:
            detail += ";校验项:" + ", ".join(checked)
        lines.append(detail)
    if outcome.get("case_status"):
        lines.append(f"- 售后案件状态:{outcome['case_status']}")
    if str(execution_status or "").upper() == "COMPLETED":
        lines.append(
            "- 说明:退款记录 status=PENDING 只表示资金尚未处理(本系统不执行资金流转),"
            "不代表等待人工审核。"
        )
    # Never emit a header-only verdict block: no facts means no extra evidence.
    return "\n".join(lines) if len(lines) > 1 else ""


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
    outcome_block = _render_outcome_block(evidence.get("outcome") or {})
    if outcome_block:
        lines.append(outcome_block)
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
