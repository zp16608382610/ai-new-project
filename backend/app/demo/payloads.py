"""Demo payload builders (Phase 7A).

Turn the REAL AgentState / AgentResult / approval records into a small JSON
contract for the Chat and Console UIs. This layer only formats what the
workflow already produced - it never fabricates business results, never
classifies intents and never executes tools itself.

The deterministic demo does not use an LLM yet: `text` is assembled from the
retrieved context, ToolResult data and approval records, so the UI never needs
to pretend an LLM answered.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.agent.state import (
    AgentResult,
    AgentResultStatus,
    AgentRunStatus,
    AgentState,
    Intent,
    Route,
    ToolRequestStatus,
)
from app.mcp.tools import MCP_TOOL_NAMES
from app.retrieval.context import ContextPackage
from app.risk import RiskDecision
from app.risk.types import RiskAction, RiskLevel
from app.services.approval_service import ApprovalView
from app.tools.base import ToolResultStatus

JsonDict = dict[str, Any]

_STATUS_LABEL = {
    "PENDING": "待支付",
    "PAID": "已支付",
    "SHIPPED": "已发货",
    "DELIVERED": "已签收",
    "CANCELLED": "已取消",
    "REFUNDED": "已退款",
    "IN_TRANSIT": "运输中",
    "OUT_FOR_DELIVERY": "派送中",
    "EXCEPTION": "配送异常",
    "SUCCESS": "成功",
    "FAILED": "失败",
    "VALIDATION_ERROR": "参数错误",
    "NOT_FOUND": "未找到",
    "BUSINESS_ERROR": "业务规则拒绝",
    "PENDING_REFUND": "等待处理",
}

_RISK_LABEL = {
    RiskLevel.LOW.value: "LOW · 低风险",
    RiskLevel.MEDIUM.value: "MEDIUM · 中风险",
    RiskLevel.HIGH.value: "HIGH · 高风险",
    RiskLevel.CRITICAL.value: "CRITICAL · 极高风险",
}

_ACTION_LABEL = {
    RiskAction.AUTO_EXECUTE.value: "自动执行",
    RiskAction.USER_CONFIRM.value: "需要用户确认",
    RiskAction.HUMAN_APPROVAL.value: "需要人工审批",
    RiskAction.BLOCK.value: "已拦截",
}

# Phase 9B after-sales case vocabulary (demo presentation only).
_CASE_TYPE_LABEL = {
    "QUALITY_ISSUE": "质量问题",
    "LOGISTICS_DISPUTE": "物流争议",
    "OTHER": "其他售后",
}

_CASE_STATUS_LABEL = {
    "INFORMATION_COLLECTION": "信息收集中",
    "ELIGIBILITY_CHECK": "待资格校验",
    "PROCESSING": "处理中",
    "PENDING_HUMAN": "待人工处理",
    "COMPLETED": "已完成",
    "REJECTED": "已拒绝",
}

_CASE_ACTION_LABEL = {
    "REFUND": "退款",
    "EXCHANGE": "换货",
    "REPAIR": "维修",
    "UNKNOWN": "售后",
}

_MISSING_LABEL = {
    "order_id": "订单号",
    "requested_action": "售后诉求",
    "problem_description": "问题描述",
}


def zh_label(value: str | None, fallback: str = "") -> str:
    if not value:
        return fallback
    return _STATUS_LABEL.get(str(value).upper(), str(value))


def risk_level_label(value: str | None) -> str:
    return _RISK_LABEL.get((value or "").upper(), value or "")


def _ord_display(value: Any) -> str:
    """Render an order id/ref as user-facing ORD-<id>."""
    if value is None:
        return ""
    text = str(value).strip()
    if text.upper().startswith("ORD"):
        return text.upper()
    return f"ORD-{text}"


def _refund_status_label(value: Any) -> str:
    """Refund status label shown to users (PENDING == awaiting review)."""
    if str(value).upper() == "PENDING":
        return "\u5f85\u5ba1\u6838"  # awaiting review
    return zh_label(value)



def _order_ref_from_arguments(arguments: dict | None) -> str | None:
    raw = (arguments or {}).get("order_id")
    if raw is None:
        return None
    text = str(raw).strip()
    if text.upper().startswith("ORD"):
        return text.upper()
    return f"ORD-{text}"


def _source_items(package: ContextPackage | None) -> list[JsonDict]:
    if package is None:
        return []
    items: list[JsonDict] = []
    for item in package.items:
        items.append(
            {
                "title": item.title,
                "version": item.version,
                "section": item.section,
                "category": item.category.value if item.category else None,
                "citation": item.citation,
                "content": item.content,
                "relevance_score": round(float(item.relevance_score), 4),
            }
        )
    return items


def _case_ref(case: JsonDict) -> str:
    """Render the user-given order reference (or the resolved id) as ORD-<id>."""
    ref = case.get("order_ref")
    if ref:
        text = str(ref).strip()
        return text.upper() if text.upper().startswith("ORD") else f"ORD-{text}"
    if case.get("order_id") is not None:
        return f"ORD-{case['order_id']}"
    return ""


def _case_timeline_steps(case: JsonDict) -> list[JsonDict]:
    """Phase 9B timeline: Case Upsert -> Information Collection."""
    steps: list[JsonDict] = [
        {
            "label": "Case Upsert",
            "state": "success",
            "detail": (
                f"{case.get('case_id')} · "
                f"{_CASE_TYPE_LABEL.get(str(case.get('case_type')), case.get('case_type'))} · "
                f"{_CASE_STATUS_LABEL.get(str(case.get('status')), case.get('status'))}"
                + ("（新建）" if case.get("created") else "（更新）")
            ),
        }
    ]
    missing = [str(item) for item in (case.get("missing_information") or [])]
    if missing:
        steps.append(
            {
                "label": "Information Collection",
                "state": "pending",
                "detail": "缺少:"
                + "、".join(_MISSING_LABEL.get(item, item) for item in missing),
            }
        )
    return steps


def _risk_decision_dict(decision: RiskDecision) -> JsonDict:
    return {
        "level": decision.risk_level.value,
        "level_label": risk_level_label(decision.risk_level.value),
        "action": decision.action.value,
        "action_label": _ACTION_LABEL.get(decision.action.value, decision.action.value),
        "reason": decision.reason,
        "policy_id": decision.policy_id,
    }


def _summarize_tool_result(result: dict[str, Any]) -> str:
    tool = str(result.get("tool_name") or "")
    status = str(result.get("status") or "")
    if status != ToolResultStatus.SUCCESS.value:
        return str(result.get("error_message") or status)
    data = dict(result.get("data") or {})
    if tool == "get_order":
        return (
            f"订单 {_ord_display(data.get('order_id'))} 状态 {zh_label(data.get('status'))},"
            f"金额 ¥{data.get('total_amount')} {data.get('currency')}"
        )
    if tool == "get_logistics":
        return (
            f"{data.get('carrier')} 运单 {data.get('tracking_number')},"
            f"当前 {zh_label(data.get('status'))}"
        )
    if tool == "check_refund_eligibility":
        if data.get("eligible") is True:
            return f"符合退款条件,可退金额 ¥{data.get('refund_amount')}"
        return f"不符合退款条件:{data.get('reason')}"
    if tool == "create_refund":
        return (
            f"退款申请已创建(单号 {data.get('id')}),金额 ¥{data.get('amount')},"
            f"状态 {_refund_status_label(data.get('status'))}"
        )
    if tool == "cancel_order":
        prev = data.get("previous_status")
        return f"订单已取消(原状态 {zh_label(prev)} → CANCELLED)"
    if tool == "create_ticket":
        return f"工单已创建(编号 {data.get('id')}),分类 {data.get('category')}"
    return str(data) if data else status


def tool_provider(tool_name: str) -> str:
    """Which provider executed this tool in the Phase 7A demo wiring."""
    if tool_name in MCP_TOOL_NAMES:
        return "MCP"
    return "Internal"


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------


def _build_timeline(
    state: AgentState,
    result: AgentResult,
    *,
    approval: ApprovalView | None = None,
    resolution: JsonDict | None = None,
) -> list[JsonDict]:
    steps: list[JsonDict] = []
    if resolution is not None:
        # Post-human-decision run (approve -> resume -> execute -> verify or
        # reject): replay the decision chain instead of re-emitting the earlier
        # waiting-state timeline, so the UI reads as one clean flow.
        approved = resolution.get("approved") is True
        steps.append(
            {
                "label": "Risk Gate",
                "state": "success" if approved else "failed",
                "detail": (
                    f"{risk_level_label(resolution.get('risk_level'))} · "
                    f"人工审批{'通过' if approved else '拒绝'}"
                ),
            }
        )
        steps.append(
            {
                "label": "Human Approval",
                "state": "success" if approved else "failed",
                "detail": f"{'已批准' if approved else '已拒绝'} (由 {resolution.get('resolved_by') or '客服'} 处理)",
            }
        )
        if approved and str(resolution.get("final_status")) == AgentRunStatus.COMPLETED.value:
            results_by_tool = {
                str(item.get("tool_name")): item
                for item in state.tool_results
                if isinstance(item, dict) and item.get("tool_name")
            }
            for request in state.tool_requests:
                tool_name = str(request.tool_name)
                outcome = results_by_tool.get(tool_name)
                executed = request.status in (
                    ToolRequestStatus.EXECUTED,
                    ToolRequestStatus.FAILED,
                )
                tool_step: JsonDict = {
                    "label": tool_name,
                    "state": "failed"
                    if executed
                    and outcome
                    and str(outcome.get("status")) != ToolResultStatus.SUCCESS.value
                    else ("success" if executed else "pending"),
                    "detail": _summarize_tool_result(outcome) if outcome else request.reason,
                    "provider": tool_provider(tool_name),
                }
                if outcome and str(outcome.get("status")) == ToolResultStatus.SUCCESS.value:
                    tool_step["result_data"] = dict(outcome.get("data") or {})
                steps.append(tool_step)
            steps.append(
                {
                    "label": "Execute",
                    "state": "success",
                    "detail": resolution.get("execute_detail") or "写操作已执行",
                }
            )
            steps.append(
                {"label": "Verify", "state": "success", "detail": "已与数据库权威状态核对"}
            )
        else:
            steps.append(
                {"label": "Execute", "state": "failed", "detail": "未执行(审批拒绝)"}
            )
            steps.append(
                {"label": "Verify", "state": "failed", "detail": "审批未通过,未执行"}
            )
        steps.append({"label": "Finalize", "state": "success", "detail": "Response 生成"})
        return steps
    intent_value = result.intent.value if result.intent else (state.intent.value if state.intent else None)
    if intent_value:
        steps.append(
            {
                "label": "Understand",
                "state": "success",
                "detail": f"Intent: {intent_value}",
            }
        )
    case = state.after_sales_case
    if isinstance(case, dict) and case.get("case_id"):
        steps.extend(_case_timeline_steps(case))
    route_value = result.route.value if result.route else (state.route.value if state.route else None)
    if route_value:
        steps.append({"label": "Route", "state": "success", "detail": f"Route: {route_value}"})

    is_rag = route_value == Route.RAG.value
    tool_steps: list[JsonDict] = []
    results_by_tool = {
        str(item.get("tool_name")): item
        for item in state.tool_results
        if isinstance(item, dict) and item.get("tool_name")
    }
    for request in state.tool_requests:
        tool_name = str(request.tool_name)
        executed = request.status in (ToolRequestStatus.EXECUTED, ToolRequestStatus.FAILED)
        outcome = results_by_tool.get(tool_name)
        step: JsonDict = {
            "label": tool_name,
            "state": "failed"
            if executed and outcome and str(outcome.get("status")) != ToolResultStatus.SUCCESS.value
            else ("success" if executed else "pending"),
            "detail": _summarize_tool_result(outcome) if outcome else request.reason,
            "provider": tool_provider(tool_name),
        }
        if outcome and str(outcome.get("status")) == ToolResultStatus.SUCCESS.value:
            step["result_data"] = dict(outcome.get("data") or {})
        tool_steps.append(step)
    run_status = state.run_status.value
    if is_rag:
        steps.append(
            {
                "label": "Retrieve",
                "state": "success" if state.retrieved_context else "failed",
                "detail": (
                    f"知识库检索到 {state.retrieved_context.total_items} 个片段"
                    if state.retrieved_context
                    else "没有检索到相关知识"
                ),
            }
        )
    else:
        for step in tool_steps:
            steps.append(step)

    # Phase 7C observability: replay every recorded Risk Gate decision as an
    # explicit trace step. Waiting flows already render their gate through the
    # dedicated branches below, so they are skipped here to avoid duplication.
    if run_status not in (
        AgentRunStatus.WAITING_USER_CONFIRMATION.value,
        AgentRunStatus.WAITING_HUMAN_APPROVAL.value,
    ):
        for decision in state.risk_decisions:
            level = str(decision.get("risk_level") or "")
            action = str(decision.get("risk_action") or "")
            steps.append(
                {
                    "label": "Risk Gate",
                    "state": "waiting"
                    if action in ("USER_CONFIRM", "HUMAN_APPROVAL")
                    else "success",
                    "detail": (
                        f"{risk_level_label(level)} · "
                        f"{_ACTION_LABEL.get(action, action)}"
                    ),
                    "risk_level": level,
                    "risk_action": action,
                    "policy_id": decision.get("policy_id"),
                    "reason": decision.get("reason"),
                }
            )

    risk_blocks: list[JsonDict] = []
    approval_view = approval
    if run_status == AgentRunStatus.WAITING_HUMAN_APPROVAL.value and approval_view is not None:
        steps.append(
            {
                "label": "Risk Gate",
                "state": "waiting",
                "detail": (
                    f"{risk_level_label(approval_view.risk_level)} · "
                    f"{_ACTION_LABEL.get('HUMAN_APPROVAL', '')}"
                ),
            }
        )
        steps.append(
            {
                "label": "Human Approval",
                "state": "waiting",
                "detail": "等待客服运营审批",
            }
        )
        steps.append({"label": "Execute", "state": "pending", "detail": "审批通过后执行"})
        steps.append({"label": "Verify", "state": "pending", "detail": "执行后校验"})
    elif run_status == AgentRunStatus.WAITING_USER_CONFIRMATION.value:
        steps.append(
            {
                "label": "Risk Gate",
                "state": "waiting",
                "detail": "MEDIUM · 需要用户确认",
            }
        )
        steps.append({"label": "Execute", "state": "pending", "detail": "确认后执行"})
        steps.append({"label": "Verify", "state": "pending", "detail": "执行后校验"})
    elif run_status in (AgentRunStatus.COMPLETED.value, AgentRunStatus.REJECTED.value):
        wrote = any(
            str(req.tool_name) in ("create_refund", "cancel_order") and req.status == ToolRequestStatus.EXECUTED
            for req in state.tool_requests
        )
        if wrote and run_status == AgentRunStatus.COMPLETED.value:
            steps.append({"label": "Execute", "state": "success", "detail": "写操作已执行"})
            steps.append({"label": "Verify", "state": "success", "detail": "已与数据库权威状态核对"})
        elif wrote and run_status == AgentRunStatus.REJECTED.value:
            steps.append({"label": "Execute", "state": "failed", "detail": "操作未执行(用户取消或审批拒绝)"})
    elif run_status == AgentRunStatus.VERIFICATION_FAILED.value:
        steps.append({"label": "Verify", "state": "failed", "detail": "校验失败,未向用户报成功"})
    elif run_status == AgentRunStatus.FAILED.value:
        steps.append({"label": "Execute", "state": "failed", "detail": result.error or "执行失败"})

    if resolution is not None:
        final_state = "success" if str(resolution.get("final_status")) == AgentRunStatus.COMPLETED.value else "failed"
        steps.append(
            {
                "label": "Human Approval",
                "state": final_state,
                "detail": (
                    f"{'已批准' if resolution.get('approved') is True else '已拒绝'}"
                    f" (由 {resolution.get('resolved_by') or '客服'} 处理)"
                ),
            }
        )
        if str(resolution.get("final_status")) == AgentRunStatus.COMPLETED.value:
            steps.append({"label": "Execute", "state": "success", "detail": resolution.get("execute_detail") or "写操作已执行"})
            steps.append({"label": "Verify", "state": "success", "detail": "已与数据库权威状态核对"})
        else:
            steps.append({"label": "Verify", "state": "failed", "detail": "审批未通过,未执行"})

    final_state = (
        "success"
        if result.status in (AgentResultStatus.SUCCESS, AgentResultStatus.TOOL_REQUESTED)
        else "failed"
    )
    steps.append({"label": "Finalize", "state": final_state, "detail": "Response 生成"})
    return steps


# ---------------------------------------------------------------------------
# Final text
# ---------------------------------------------------------------------------


def _grounded_answer(package: ContextPackage) -> str:
    if package is None or not package.items:
        return "没有在知识库中找到与问题匹配的内容。建议转人工客服或换个说法再问一次。"
    lines: list[str] = ["根据知识库检索到的政策内容整理如下(演示环境,确定性答案,无 LLM):"]
    for item in package.items[:5]:
        header = f"《{item.title}》"
        if item.version:
            header += f" v{item.version}"
        if item.section:
            header += f" · {item.section}"
        content = (item.content or "").strip()
        excerpt = content if len(content) <= 220 else content[:220] + "…"
        lines.append(f"- {header}:{excerpt}")
    return "\n".join(lines)


def build_run_payload(
    state: AgentState,
    result: AgentResult,
    *,
    session_id: str,
    user_message: str,
    approval_view: ApprovalView | None = None,
    resolution: JsonDict | None = None,
) -> JsonDict:
    """Build the Chat/Console JSON payload for one workflow execution."""
    package = state.retrieved_context
    sources = _source_items(package)
    # Phase 9B: after-sales case created/updated by this run (None otherwise).
    case_block = state.after_sales_case or result.after_sales_case

    risk: JsonDict | None = None
    approval_block: JsonDict | None = None
    if approval_view is not None:
        approval_block = {
            "id": approval_view.id,
            "request_id": approval_view.request_id,
            "tool_name": approval_view.tool_name,
            "order_ref": _order_ref_from_arguments(dict(approval_view.tool_arguments or {})),
            "risk_level": approval_view.risk_level,
            "risk_label": risk_level_label(approval_view.risk_level),
            "reason": approval_view.reason,
            "status": approval_view.status,
            "created_at": (
                approval_view.created_at.isoformat() if isinstance(approval_view.created_at, datetime) else approval_view.created_at
            ),
        }
        risk = {
            "tool": approval_view.tool_name,
            "level": approval_view.risk_level,
            "level_label": risk_level_label(approval_view.risk_level),
            "action": "HUMAN_APPROVAL",
            "reason": approval_view.reason,
        }
    elif state.run_status.value == AgentRunStatus.WAITING_USER_CONFIRMATION.value:
        risk = {
            "tool": "cancel_order",
            "level": "MEDIUM",
            "level_label": risk_level_label("MEDIUM"),
            "action": "USER_CONFIRM",
            "reason": "取消订单会改变订单状态,需要用户本人确认。",
        }

    # Best refund/expected amount from the eligibility result (authoritative).
    expected_amount: str | None = None
    expected_refund_order: str | None = None
    for item in state.tool_results:
        if isinstance(item, dict) and item.get("tool_name") == "check_refund_eligibility":
            data = dict(item.get("data") or {})
            if data.get("eligible") is True and data.get("refund_amount") is not None:
                expected_amount = str(data["refund_amount"])
            if data.get("order_id") is not None:
                expected_refund_order = f"ORD-{data['order_id']}"
    if approval_block is not None:
        approval_block["expected_amount"] = expected_amount
        approval_block["order_ref"] = approval_block.get("order_ref") or expected_refund_order

    action: JsonDict | None = None
    if state.run_status.value == AgentRunStatus.WAITING_USER_CONFIRMATION.value:
        action = {
            "type": "user_confirmation",
            "message": result.confirmation_message or "该操作需要您确认后才能执行。",
            "request_id": state.request_id,
        }
    elif state.run_status.value == AgentRunStatus.WAITING_HUMAN_APPROVAL.value and approval_view is not None:
        action = {
            "type": "human_approval",
            "approval_id": approval_view.id,
            "message": "该操作属于高风险操作,需要客服人工审批。",
        }

    # Phase 7B: when the workflow produced an LLM final response (grounded in
    # authoritative evidence), prefer it over the deterministic text. When the
    # LLM is disabled or failed, result.response is None and the deterministic
    # text stays - the demo never fabricates an LLM answer.
    llm_text = result.response or state.response
    text = llm_text if llm_text else build_text(
        state, result, approval_view=approval_view, resolution=resolution
    )
    return {
        "request_id": state.request_id,
        "session_id": session_id,
        "user_id": state.user_id,
        "user_message": user_message,
        "created_at": utcnow(),
        "updated_at": utcnow(),
        "agent_status": result.status.value,
        "run_status": state.run_status.value,
        "text": text,
        "intent": result.intent.value if result.intent else (state.intent.value if state.intent else None),
        "route": result.route.value if result.route else (state.route.value if state.route else None),
        "entities": state.entities.to_dict() if state.entities else None,
        "sources": sources,
        "steps": _build_timeline(state, result, approval=approval_view, resolution=resolution),
        "risk": risk,
        "approval": approval_block,
        "case": case_block,
        "expected_refund": {"amount": expected_amount, "order_ref": expected_refund_order}
        if expected_amount is not None
        else None,
        "action": action,
        "approval_resolution": resolution,
    }


def _utcnow_native() -> str:
    from datetime import timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


utcnow = _utcnow_native


def _latest_tool_result(state: AgentState) -> dict[str, Any] | None:
    if not state.tool_results:
        return None
    return dict(state.tool_results[-1])


def build_text(
    state: AgentState,
    result: AgentResult,
    *,
    approval_view: ApprovalView | None = None,
    resolution: JsonDict | None = None,
) -> str:
    """Assemble a user-facing assistant message from real workflow outputs."""
    intent = result.intent.value if result.intent else (state.intent.value if state.intent else None)
    route = result.route.value if result.route else (state.route.value if state.route else None)
    status = result.status.value

    case = state.after_sales_case or result.after_sales_case
    if isinstance(case, dict) and case.get("case_id"):
        return _case_text(case)

    if status in (AgentResultStatus.WAITING_USER_CONFIRMATION.value,):
        return result.confirmation_message or "该操作需要您确认后才能执行,请确认是否继续?"

    if status == AgentResultStatus.WAITING_HUMAN_APPROVAL.value and approval_view is not None:
        ref = approval_block_order(approval_view) or ""
        amount = ""
        for item in state.tool_results:
            if isinstance(item, dict) and item.get("tool_name") == "check_refund_eligibility":
                data = dict(item.get("data") or {})
                if data.get("refund_amount") is not None:
                    amount = f",预计退款金额 ¥{data['refund_amount']}"
        return (
            f"退款申请已提交人工审核。\n"
            f"订单:{ref}{amount}\n"
            f"风险等级:{risk_level_label(approval_view.risk_level)}\n"
            f"状态:等待客服审核"
        )

    if status in (AgentResultStatus.REJECTED.value,):
        if intent == Intent.CANCEL_ORDER.value:
            return "好的,已取消本次操作,订单状态未改变。"
        return "很抱歉,该申请未通过人工审核,因此未执行。如有疑问请联系人工客服。"

    if status == AgentResultStatus.VERIFICATION_FAILED.value:
        return f"系统在执后校验中发现问题,未能确认操作成功:{result.error or ''}"

    if status == AgentResultStatus.NEEDS_CLARIFICATION.value:
        return "您的请求需要补充信息(例如唯一的订单号),请明确后再发一次。"

    if status == AgentResultStatus.ESCALATION_REQUIRED.value:
        return "该请求已转人工客服处理。"

    if status == AgentResultStatus.ERROR.value:
        return f"抱歉,系统未能完成该请求:{result.error or '未知错误'}"

    if route == Route.RAG.value:
        return _grounded_answer(state.retrieved_context)

    tool = _latest_tool_result(state)
    if tool is None:
        if status == AgentResultStatus.TOOL_REQUESTED.value:
            return "已生成执行计划,但尚未配置工具执行器。"
        return "处理完成。"

    if tool.get("status") != ToolResultStatus.SUCCESS.value:
        return f"处理失败:{tool.get('error_message') or tool.get('status')}"
    return _final_text_from_tool(str(tool.get("tool_name")), dict(tool.get("data") or {}), resolution)


def _case_text(case: JsonDict) -> str:
    """Deterministic Phase 9B reply for the after-sales case branch.

    Information collection asks only for what is really missing; a complete
    case states the next step (eligibility check) without performing it.
    """
    missing = [str(item) for item in (case.get("missing_information") or [])]
    if missing:
        asks: list[str] = []
        if "order_id" in missing:
            asks.append("提供对应的订单号")
        if "requested_action" in missing:
            asks.append("告诉我是希望退款、换货还是维修")
        if "problem_description" in missing:
            asks.append("描述一下具体的问题")
        if not asks:
            asks.append("补充相关信息")
        return "可以帮你处理售后。请先" + "，并".join(asks) + "。"

    action = _CASE_ACTION_LABEL.get(str(case.get("requested_action") or ""), "售后")
    ref = _case_ref(case)
    if ref:
        return (
            f"已获取订单 {ref} 和{action}诉求，"
            f"接下来可以检查该订单是否符合{action}条件。"
        )
    return f"已获取{action}诉求，接下来可以进行后续处理。"


def approval_block_order(approval_view: ApprovalView) -> str:
    return _order_ref_from_arguments(dict(approval_view.tool_arguments or {})) or ""


def _final_text_from_tool(tool_name: str, data: dict[str, Any], resolution: JsonDict | None) -> str:
    if resolution is not None and resolution.get("approved") is False:
        return "很抱歉,该申请未通过人工审核,因此未执行。如有疑问请联系人工客服。"
    if tool_name == "get_order":
        return (
            f"订单 {_ord_display(data.get('order_id'))} 当前状态:{zh_label(data.get('status'))}。\n"
            f"订单金额:¥{data.get('total_amount')} {data.get('currency')},共 {len(data.get('items') or [])} 件商品。"
        )
    if tool_name == "get_logistics":
        estimated = data.get("estimated_delivery")
        tail = f",预计送达 {estimated}" if estimated else ""
        return (
            f"订单 {_ord_display(data.get('order_id'))} 的物流信息:\n"
            f"{data.get('carrier')} · 运单号 {data.get('tracking_number')}\n"
            f"当前状态:{zh_label(data.get('status'))}{tail}"
        )
    if tool_name == "create_refund":
        return (
            f"退款申请已创建并通过数据库校验。\n"
            f"退款单:{data.get('id')} 订单:{_ord_display(data.get('order_id'))}\n"
            f"金额:¥{data.get('amount')}(由订单权威数据确定)\n"
            f"状态:{_refund_status_label(data.get('status'))},等待后续处理。"
        )
    if tool_name == "cancel_order":
        return f"订单 {_ord_display(data.get('order_id'))} 已成功取消(原状态 {zh_label(data.get('previous_status'))} → CANCELLED),并已通过数据库校验。"
    if tool_name == "create_ticket":
        return (
            f"售后工单已创建:\n"
            f"工单号:{data.get('id')} 分类:{data.get('category')}\n"
            f"状态:{zh_label(data.get('status'))},我们会尽快跟进处理。"
        )
    if tool_name == "check_refund_eligibility":
        if data.get("eligible") is True:
            return f"该订单符合退款条件,可退金额 ¥{data.get('refund_amount')}。"
        return f"该订单暂不符合退款条件:{data.get('reason')}"
    return "处理完成。"
