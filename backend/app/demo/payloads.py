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

from app.agent.after_sales import (
    EXECUTION_COMPLETED,
    EXECUTION_FAILED,
    EXECUTION_REJECTED,
    EXECUTION_VERIFICATION_FAILED,
)
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


_TERMINAL_EXECUTION_STATUSES = frozenset(
    {
        EXECUTION_COMPLETED,
        EXECUTION_REJECTED,
        EXECUTION_VERIFICATION_FAILED,
        EXECUTION_FAILED,
    }
)


def _terminal_execution_verdict(state: AgentState) -> bool:
    """True when the workflow already reached a final after-sales verdict.

    Phase 9F: such a verdict (completed / rejected / verification failed /
    failed) is an authoritative business conclusion and must not be reworded
    by the LLM - the deterministic reply already states it exactly.
    """
    execution = state.after_sales_execution
    if not isinstance(execution, dict):
        return False
    return str(execution.get("status") or "").upper() in _TERMINAL_EXECUTION_STATUSES


def _refund_status_label(value: Any) -> str:
    """Refund status label shown to users (Phase 9F).

    ``refunds.status = PENDING`` means the refund record exists but no money
    has moved yet; it is NOT the human-approval state (that lives in
    ``approval_requests.status``). Labelling it "待审核" made the model and the
    UI conflate the two.
    """
    if str(value).upper() == "PENDING":
        return "处理中（等待资金处理，PENDING）"
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


def _investigation_timeline_steps(
    eligibility: JsonDict, investigation: JsonDict
) -> list[JsonDict]:
    """Phase 9C timeline: Order Investigation -> Policy Retrieval -> Eligibility.

    Replays exactly what the investigation service recorded; nothing is
    re-derived here (no second trace system).
    """
    order = investigation.get("order")
    policy = investigation.get("policy")
    order = order if isinstance(order, dict) else {}
    policy = policy if isinstance(policy, dict) else {}
    steps: list[JsonDict] = [
        {
            "label": "Order Investigation",
            "state": str(order.get("state") or "failed"),
            "detail": str(order.get("detail") or "未取得订单业务数据"),
        },
        {
            "label": "Policy Retrieval",
            "state": str(policy.get("state") or "failed"),
            "detail": str(policy.get("detail") or "未取得政策依据"),
            "query": policy.get("query"),
            "citations": [str(item) for item in (policy.get("citations") or [])],
        },
    ]
    eligible = eligibility.get("eligible")
    if eligible is True:
        state = "success"
    elif eligible is False:
        state = "failed"
    else:
        state = "pending"
    failed_rules = [str(item) for item in (eligibility.get("failed_rules") or [])]
    detail = str(eligibility.get("reason") or "")
    if failed_rules:
        detail = (detail + " " if detail else "") + "未通过规则:" + "、".join(failed_rules)
    step: JsonDict = {"label": "Eligibility Check", "state": state, "detail": detail}
    citations = [str(item) for item in (eligibility.get("policy_citations") or [])]
    if citations:
        step["citations"] = citations
    steps.append(step)
    return steps


def _treatment_timeline_steps(
    treatment: JsonDict, ticket: JsonDict | None
) -> list[JsonDict]:
    """Phase 9D timeline: Treatment Plan -> Ticket Creation.

    Replays exactly what the deterministic planner/service recorded; the agent
    layer never re-derives a treatment (no second trace system).
    """
    action = treatment.get("action")
    executable = bool(treatment.get("executable"))
    if action:
        detail = (
            f"{treatment.get('action_label') or action} · "
            f"{treatment.get('required_next_step') or ''}"
        ).strip(" ·")
    else:
        detail = str(treatment.get("reason") or "未生成可执行处理方案")
    steps: list[JsonDict] = [
        {
            "label": "Treatment Plan",
            "state": "success" if executable else "pending",
            "detail": detail,
            "action": action,
            "requires_execution": bool(treatment.get("requires_execution")),
            "required_next_step": treatment.get("required_next_step"),
        }
    ]
    if not executable:
        # Not eligible / no confirmed action: no ticket is created at all.
        return steps
    if isinstance(ticket, dict) and ticket.get("id") is not None:
        steps.append(
            {
                "label": "Ticket Creation",
                "state": "success",
                "detail": (
                    f"{ticket.get('ref') or ticket.get('id')} · "
                    f"{ticket.get('category')} · "
                    f"{'新建' if ticket.get('created') else '复用已有工单'}"
                ),
            }
        )
    else:
        steps.append(
            {
                "label": "Ticket Creation",
                "state": "failed",
                "detail": "售后工单创建失败,需要人工跟进",
            }
        )
    return steps


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
    eligibility = state.after_sales_eligibility or result.after_sales_eligibility
    investigation = (
        state.after_sales_investigation or result.after_sales_investigation
    )
    if isinstance(eligibility, dict) and eligibility:
        steps.extend(
            _investigation_timeline_steps(
                eligibility,
                investigation if isinstance(investigation, dict) else {},
            )
        )
    treatment = state.after_sales_treatment or result.after_sales_treatment
    ticket = state.after_sales_ticket or result.after_sales_ticket
    if isinstance(treatment, dict) and treatment:
        steps.extend(
            _treatment_timeline_steps(
                treatment, ticket if isinstance(ticket, dict) else None
            )
        )
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
    # Phase 9E: an after-sales treatment with no executable business system
    # (exchange / repair) or a business service that refused execution must be
    # visible instead of silently absent.
    execution_block = state.after_sales_execution or result.after_sales_execution
    if isinstance(execution_block, dict) and execution_block:
        steps.extend(_execution_timeline_steps(execution_block))
    steps.append({"label": "Finalize", "state": final_state, "detail": "Response 生成"})
    return steps


def _execution_timeline_steps(execution: JsonDict) -> list[JsonDict]:
    """Timeline steps for an after-sales execution that produced no write.

    A completed / failed / verification-failed run already renders its Execute
    and Verify steps from the run status, so only the "no execution happened"
    outcomes need an explicit, honest step here.
    """
    from app.agent.after_sales import (
        EXECUTION_HUMAN_HANDOFF,
        EXECUTION_NOT_EXECUTED,
        EXECUTION_NOT_IMPLEMENTED,
    )

    status = str(execution.get("status") or "")
    if status == EXECUTION_NOT_IMPLEMENTED:
        action = str(execution.get("action") or "")
        return [
            {
                "label": "Execution",
                "state": "waiting",
                "detail": (
                    f"{zh_label(action, action)} 已生成处理方案并创建工单,"
                    "但本阶段没有可执行的真实业务系统,已转人工处理。"
                ),
                "next_step": EXECUTION_HUMAN_HANDOFF,
            }
        ]
    if status == EXECUTION_NOT_EXECUTED:
        return [
            {
                "label": "Execution",
                "state": "waiting",
                "detail": (
                    "业务系统未确认该售后动作可执行,未发起任何写操作:"
                    f"{execution.get('reason') or '需要人工复核'}"
                ),
            }
        ]
    return []


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
    # Phase 9C: deterministic eligibility conclusion + the evidence behind it.
    eligibility_block = (
        state.after_sales_eligibility or result.after_sales_eligibility
    )
    investigation_block = (
        state.after_sales_investigation or result.after_sales_investigation
    )
    # Phase 9D: deterministic treatment plan + the after-sales ticket (if any).
    treatment_block = state.after_sales_treatment or result.after_sales_treatment
    ticket_block = state.after_sales_ticket or result.after_sales_ticket
    # Phase 9E: risk-gated execution + the independent verification result.
    execution_block = state.after_sales_execution or result.after_sales_execution
    verification_block = (
        state.after_sales_verification or result.after_sales_verification
    )

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
    # Phase 9F: never let the LLM reword a terminal execution verdict (e.g.
    # turn a completed, verified auto-execution into "waiting for human
    # review"). The deterministic reply distinguishes the outcomes already.
    if llm_text and _terminal_execution_verdict(state):
        llm_text = None
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
        "eligibility": (
            dict(eligibility_block) if isinstance(eligibility_block, dict) else None
        ),
        "investigation": (
            dict(investigation_block) if isinstance(investigation_block, dict) else None
        ),
        "treatment": (
            dict(treatment_block) if isinstance(treatment_block, dict) else None
        ),
        "ticket": (dict(ticket_block) if isinstance(ticket_block, dict) else None),
        "execution": (
            dict(execution_block) if isinstance(execution_block, dict) else None
        ),
        "verification": (
            dict(verification_block) if isinstance(verification_block, dict) else None
        ),
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
        return _case_text(
            case,
            state.after_sales_eligibility or result.after_sales_eligibility,
            state.after_sales_treatment or result.after_sales_treatment,
            state.after_sales_ticket or result.after_sales_ticket,
            state.after_sales_execution or result.after_sales_execution,
            state.after_sales_verification or result.after_sales_verification,
        )

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


def _missing_asks(missing: list[str]) -> str:
    asks: list[str] = []
    if "order_id" in missing:
        asks.append("提供对应的订单号")
    if "requested_action" in missing:
        asks.append("告诉我是希望退款、换货还是维修")
    if "problem_description" in missing:
        asks.append("描述一下具体的问题")
    if not asks:
        asks.append("补充相关信息")
    return "，并".join(asks)


def _case_text(
    case: JsonDict,
    eligibility: JsonDict | None = None,
    treatment: JsonDict | None = None,
    ticket: JsonDict | None = None,
    execution: JsonDict | None = None,
    verification: JsonDict | None = None,
) -> str:
    """Deterministic reply for the after-sales case branch (9B + 9C + 9D).

    Phase 9B asks only for what is really missing. Phase 9C additionally
    reports the deterministic eligibility conclusion. Phase 9D reports the
    treatment plan and the after-sales ticket that was really created. The text
    only repeats what the case row, the EligibilityResult and the treatment plan
    already decided: it never re-derives eligibility, never invents a policy
    claim and never claims a ticket that was not created.
    """
    from app.agent.after_sales import (
        EXECUTION_COMPLETED,
        EXECUTION_FAILED,
        EXECUTION_NOT_EXECUTED,
        EXECUTION_PENDING_APPROVAL,
        EXECUTION_REJECTED,
        EXECUTION_VERIFICATION_FAILED,
    )

    if not isinstance(eligibility, dict) or not eligibility:
        collected = case.get("collected_information")
        stored = collected.get("eligibility") if isinstance(collected, dict) else None
        eligibility = stored if isinstance(stored, dict) else None

    missing = [str(item) for item in (case.get("missing_information") or [])]
    action = _CASE_ACTION_LABEL.get(str(case.get("requested_action") or ""), "售后")
    reason = eligibility.get("reason") if isinstance(eligibility, dict) else None

    # Phase 9E: report what the execution step really did. Only a verified
    # execution may claim the case is completed; a failure / a pending approval
    # / a refused business action is reported as exactly that.
    if isinstance(execution, dict) and execution:
        execution_status = str(execution.get("status") or "")
        case_ref = case.get("case_id")
        if execution_status == EXECUTION_COMPLETED:
            return (
                f"{reason or ''}退款申请已完成处理。\n"
                f"退款金额：¥{execution.get('refund_amount')}（以订单业务系统实际金额为准）\n"
                f"退款记录：REFUND-{execution.get('refund_id')}\n"
                f"售后单号：{case_ref}\n"
                f"状态：COMPLETED（已由数据库权威状态校验）"
            )
        if execution_status == EXECUTION_PENDING_APPROVAL:
            ref = ""
            if isinstance(ticket, dict) and ticket.get("ref") is not None:
                ref = f"，并创建售后工单 {ticket.get('ref')}"
            return (
                f"{reason or ''}已生成{action}处理方案{ref}，已提交人工审批，"
                "审批通过后才会真正执行。"
            )
        if execution_status == EXECUTION_REJECTED:
            return "很抱歉，该售后申请未通过人工审核，因此未执行退款。"
        if execution_status == EXECUTION_VERIFICATION_FAILED:
            return (
                "退款已提交，但执行后校验未能确认成功，已转人工复核，"
                "未向您确认完成。"
            )
        if execution_status == EXECUTION_FAILED:
            return "退款执行失败，未向您确认完成，已转人工跟进。"
        if execution_status == EXECUTION_NOT_EXECUTED:
            return (
                f"{execution.get('reason') or ''}"
                "该售后动作未被执行，已转人工复核。"
            )

    # Phase 9D: only an executable plan (definite eligibility + a request the
    # user actually made) may claim a treatment/ticket. Everything else keeps
    # the 9B/9C wording below.
    if isinstance(treatment, dict) and treatment.get("executable"):
        action_label = _CASE_ACTION_LABEL.get(
            str(treatment.get("action") or ""), action
        )
        next_step = str(treatment.get("required_next_step") or "等待后续执行")
        if isinstance(ticket, dict) and ticket.get("id") is not None:
            return (
                f"{reason or ''}已生成{action_label}处理方案，"
                f"并创建售后工单 {ticket.get('ref') or ticket.get('id')}，"
                f"下一步：{next_step}。"
            )
        return (
            f"{reason or ''}已生成{action_label}处理方案，"
            "但售后工单创建失败，未执行任何业务操作，请联系人工客服跟进。"
        )

    if reason and not missing:
        if eligibility.get("eligible") is True:
            return f"{reason}该订单符合{action}条件，已进入后续处理。"
        if eligibility.get("eligible") is False:
            return f"{reason}该订单暂不符合{action}条件，如有疑问可转人工客服。"
        return f"{reason}该订单需要人工复核。"

    if reason:
        return f"{reason}请先{_missing_asks(missing)}。"
    if missing:
        return "可以帮你处理售后。请先" + _missing_asks(missing) + "。"

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
