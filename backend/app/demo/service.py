"""Phase 7A demo orchestration service.

Wires the EXISTING components into one runnable demo stack:

    AgentWorkflow
      - classifier / entities / router : deterministic defaults
      - RAG runner                     : real RetrievalPipeline (Session)
      - tool provider                  : MCPToolAdapter
                                          -> MCP for get_order / get_logistics /
                                             create_ticket (stdio subprocess)
                                          -> internal ToolExecutor otherwise
      - RiskEngine                     : Phase 5 pure policy engine
      - ApprovalService                : real approval_requests table
      - BusinessVerifier               : Execute -> Verify

Nothing here rewrites AgentWorkflow / RiskEngine / ToolExecutor / MCP. The UI
only gets a JSON projection of the real AgentState/AgentResult.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.agent.workflow import AgentWorkflow
from app.core.config import get_settings
from app.llm import DeepSeekProvider, FinalResponder, LLMIntentExtractor
from app.mcp.adapter import MCPToolAdapter
from app.mcp.client import MCPClient
from app.retrieval.pipeline import RetrievalPipeline
from app.risk import RiskEngine
from app.services.approval_service import ApprovalService
from app.services.verification import BusinessVerifier
from app.tools.executor import ToolExecutor
from app.tools.handlers import build_default_registry

from app.demo.payloads import build_run_payload, _summarize_tool_result
from app.demo.store import (
    DemoRunStore,
    new_request_id,
    new_session_id,
    utcnow_iso,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]


class DemoComponents:
    """Everything a single demo request needs (bound to one DB session)."""

    def __init__(
        self,
        session: Session,
        *,
        database_url: str | None = None,
        mcp_client: MCPClient | None = None,
        llm_intent: LLMIntentExtractor | None = None,
        llm_responder: FinalResponder | None = None,
    ) -> None:
        self.session = session
        self.internal_executor = ToolExecutor(build_default_registry(session))
        resolved_url = database_url or _database_url_of(session)
        self.client = mcp_client or _real_mcp_client(resolved_url)
        self.provider = MCPToolAdapter(
            internal_executor=self.internal_executor, client=self.client
        )
        self.risk_engine = RiskEngine()
        self.approvals = ApprovalService(session)
        self.verifier = BusinessVerifier(session)
        self.retrieval = RetrievalPipeline(session)
        self.workflow = AgentWorkflow(
            retrieval=self.retrieval,
            tool_executor=self.provider,
            risk_engine=self.risk_engine,
            approval_gateway=self.approvals,
            verifier=self.verifier,
            llm_intent=llm_intent,
            llm_responder=llm_responder,
        )
        self.llm_provider_name = "DeepSeek" if llm_intent is not None else None


def _real_mcp_client(database_url: str | None) -> MCPClient:
    """Build a stdio MCP client that spawns app.mcp.server against the same DB."""
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    if database_url:
        env["DATABASE_URL"] = database_url
    return MCPClient(args=["-m", "app.mcp.server"], cwd=str(BACKEND_DIR), env=env)


def _database_url_of(session: Session) -> str | None:
    try:
        return str(session.get_bind().engine.url.render_as_string(hide_password=False))
    except Exception:
        return None


def _build_llm_components(*, force_disabled: bool = False):
    """Return (llm_intent, llm_responder, enabled) from settings.

    LLM_ENABLED=false, an empty DEEPSEEK_API_KEY or any provider config error
    disables the LLM path: the Agent keeps running deterministically. No key is
    ever logged or exposed. force_disabled is used by the Evaluation runner
    so the deterministic (offline) agent path is graded without a live model.
    """
    if force_disabled:
        return None, None, False
    try:
        settings = get_settings()
    except Exception:
        return None, None, False
    if not settings.llm_enabled or not (settings.deepseek_api_key or "").strip():
        return None, None, False
    try:
        provider = DeepSeekProvider(
            api_key=settings.deepseek_api_key,
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
            timeout_seconds=settings.deepseek_timeout_seconds,
        )
    except Exception:
        return None, None, False
    extractor = LLMIntentExtractor(provider)
    responder = FinalResponder(provider)
    return extractor, responder, True


def _history_from_runs(store: DemoRunStore, session_id: str | None, limit: int = 6):
    """Recent user/assistant turns of one session (bounded conversation context)."""
    if not session_id:
        return None
    try:
        runs = list(store.list_session(session_id))
    except Exception:
        return None
    turns: list[dict[str, str]] = []
    for run in runs[-limit:]:
        user_message = str(run.get("user_message") or "").strip()
        if user_message:
            turns.append({"role": "user", "content": user_message})
        text = str(run.get("text") or "").strip()
        if text:
            turns.append({"role": "assistant", "content": text})
    return turns or None


def _attach_llm_meta(payload: dict, enabled: bool) -> dict:
    payload["llm"] = {
        "enabled": enabled,
        "provider": "DeepSeek" if enabled else None,
    }
    return payload


def run_chat(
    session: Session,
    store: DemoRunStore,
    *,
    message: str,
    user_id: int,
    session_id: str | None = None,
    request_id: str | None = None,
    user_confirmed: bool | None = None,
    mcp_client: MCPClient | None = None,
    use_llm: bool | None = None,
) -> dict[str, Any]:
    """Run one user message through the real Agent workflow and store the run.

    use_llm=False forces the deterministic offline path (used by the
    Evaluation runner and by tests); the default honours the environment.
    """
    llm_intent, llm_responder, llm_enabled = _build_llm_components(
        force_disabled=use_llm is False
    )
    components = DemoComponents(
        session,
        mcp_client=mcp_client,
        llm_intent=llm_intent,
        llm_responder=llm_responder,
    )
    run_session = session_id or new_session_id()
    run_request = request_id or new_request_id()
    history = _history_from_runs(store, run_session)
    state, result = components.workflow.execute(
        run_request,
        message,
        user_id=user_id,
        session_id=run_session,
        user_confirmed=user_confirmed,
        history=history,
    )
    approval_view = None
    if result.approval_id is not None:
        try:
            approval_view = components.approvals.get(int(result.approval_id))
        except Exception:
            approval_view = None
    payload = build_run_payload(
        state,
        result,
        session_id=run_session,
        user_message=message,
        approval_view=approval_view,
    )
    # Keep the top-level approval_id in sync so the store indexes this run and
    # finalize_approval can find and update it after the human decision.
    approval_block = payload.get("approval")
    if isinstance(approval_block, dict) and approval_block.get("id") is not None:
        payload["approval_id"] = int(approval_block["id"])
    payload["created_at"] = utcnow_iso()
    _attach_llm_meta(payload, llm_enabled)
    return store.save(payload)


def finalize_approval(
    session: Session,
    store: DemoRunStore,
    approval_id: int,
    *,
    approved: bool,
    resolved_by: str | None = "demo-operator",
    mcp_client: MCPClient | None = None,
    use_llm: bool | None = None,
) -> dict[str, Any]:
    """Resume the original ToolRequest after a human decision (real workflow)."""
    llm_intent, llm_responder, llm_enabled = _build_llm_components(
        force_disabled=use_llm is False
    )
    components = DemoComponents(
        session,
        mcp_client=mcp_client,
        llm_intent=llm_intent,
        llm_responder=llm_responder,
    )
    view_before = components.approvals.get(approval_id)
    previous_run = store.find_by_approval(approval_id)
    history = _history_from_runs(store, (previous_run or {}).get("session_id"))
    original_message = (previous_run or {}).get("user_message") or None
    state, result = components.workflow.resume_after_approval(
        approval_id,
        approved=approved,
        resolved_by=resolved_by,
        user_message=original_message,
        history=history,
    )
    view_after = components.approvals.get(approval_id)

    execute_detail = ""
    if state.tool_results:
        try:
            execute_detail = _summarize_tool_result(dict(state.tool_results[-1]))
        except Exception:
            execute_detail = ""
    resolution = {
        "approval_id": approval_id,
        "id": approval_id,
        "request_id": view_before.request_id,
        "user_id": view_before.user_id,
        "tool_name": view_before.tool_name,
        "risk_level": view_before.risk_level,
        "reason": view_before.reason,
        "approved": approved,
        "status": "APPROVED" if approved else "REJECTED",
        "resolved_by": resolved_by,
        "resolved_at": (
            view_after.resolved_at.isoformat()
            if view_after.resolved_at is not None
            else utcnow_iso()
        ),
        "final_status": state.run_status.value,
        "final_agent_status": result.status.value,
        "execute_detail": execute_detail,
        "summary": (
            "审批通过,执行完成并通过校验。"
            if approved and state.run_status.value == "COMPLETED"
            else "审批拒绝,未执行任何写操作。"
        ),
    }

    previous = store.find_by_approval(approval_id)
    base_session = (previous or {}).get("session_id") or new_session_id()
    base_message = (previous or {}).get("user_message") or (
        f"审批单 {approval_id} 已处理"
    )
    payload = build_run_payload(
        state,
        result,
        session_id=base_session,
        user_message=base_message,
        resolution=resolution,
    )
    payload["request_id"] = view_before.request_id
    payload["approval_id"] = approval_id
    payload["approval_resolution"] = resolution
    payload["created_at"] = (previous or {}).get("created_at") or utcnow_iso()
    payload["updated_at"] = utcnow_iso()
    _attach_llm_meta(payload, llm_enabled)
    return store.save(payload)


def list_session_runs(store: DemoRunStore, session_id: str) -> list[dict[str, Any]]:
    return store.list_session(session_id)


def get_run(store: DemoRunStore, request_id: str) -> dict[str, Any] | None:
    return store.get(request_id)
