"""Phase 7A demo API tests (HTTP boundary, real AgentWorkflow).

Covers the Chat + Console flow the frontend drives:

    /demo/chat                RAG (Scenario A) / cancel confirm (D)
    /demo/approvals           queue + resolved projection
    /demo/approvals/{id}/approve  -> resume -> execute -> verify (E)
    /demo/approvals/{id}/reject   -> nothing executes

Uses a FILE-backed SQLite demo database (knowledge + dev seed + demo orders).
Order/logistics/ticket go through MCP in production; these tests exercise the
internal + approval paths (already covered end-to-end by Phase 6 MCP tests).
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.base import Base
from app.db.models import Order, Refund
from app.db.session import create_db_engine, create_session_factory, get_db
from app.demo.demo_seed import prepare_demo_database
from app.demo.store import get_store, reset_store
from app.main import create_app
from app.services.after_sales_case_manager import AfterSalesCaseManager
from app.services.after_sales_service import AfterSalesService

API = "/api/v1"


@pytest.fixture()
def demo_api(tmp_path):
    db_path = tmp_path / "demo.db"
    url = "sqlite+pysqlite:///" + db_path.as_posix()
    prepare_demo_database(url)
    engine = create_db_engine(url)
    session = create_session_factory(engine)()

    app = create_app()

    def override_get_db():
        try:
            yield session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)
    with client:
        yield client, session
    app.dependency_overrides.clear()
    reset_store()
    session.close()
    engine.dispose()


def _chat(client, message, session_id="demo-s", user_confirmed=None, user_id=1):
    payload = {
        "message": message,
        "user_id": user_id,
        "session_id": session_id,
    }
    if user_confirmed is not None:
        payload["user_confirmed"] = user_confirmed
    resp = client.post(f"{API}/demo/chat", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_chat_knowledge_qa_returns_grounded_sources(demo_api):
    client, _ = demo_api
    run = _chat(client, "退款需要满足什么条件？")
    assert run["route"] == "RAG"
    assert run["run_status"] == "COMPLETED"
    assert len(run["sources"]) >= 1
    assert run["text"].strip()


def test_cancel_requires_confirmation_then_executes(demo_api):
    client, session = demo_api
    first = _chat(client, "帮我取消订单 ORD-1002")
    assert first["run_status"] == "WAITING_USER_CONFIRMATION"
    assert first["action"] and first["action"]["type"] == "user_confirmation"

    confirmed = _chat(client, "帮我取消订单 ORD-1002", user_confirmed=True)
    assert confirmed["run_status"] == "COMPLETED"
    assert confirmed["intent"] == "CANCEL_ORDER"
    order = session.get(Order, 1002)
    assert order is not None and order.status.value == "CANCELLED"


def test_refund_approval_queue_and_resume(demo_api):
    client, session = demo_api
    run = _chat(client, "帮我把订单 ORD-1003 退款")
    assert run["run_status"] == "WAITING_HUMAN_APPROVAL"
    approval_id = run["approval"]["id"]
    assert run["approval"]["risk_level"] == "HIGH"

    queue = client.get(f"{API}/demo/approvals").json()
    assert any(item["id"] == approval_id for item in queue["pending"])

    resolved = client.post(
        f"{API}/demo/approvals/{approval_id}/approve", json={"resolved_by": "ops"}
    ).json()
    assert resolved["approval"]["status"] == "APPROVED"
    final = resolved["run"]
    assert final["run_status"] == "COMPLETED"
    assert final["approval_resolution"]["approved"] is True
    assert final["text"].strip()

    detail = client.get(f"{API}/demo/approvals/{approval_id}").json()
    assert detail["resolution"]["status"] == "APPROVED"

    refunds = session.scalars(
        select(Refund).where(Refund.order_id == 1003)
    )
    assert len(list(refunds)) == 1
    session.expire_all()
    refund = session.scalars(
        select(Refund).where(Refund.order_id == 1003)
    ).first()
    assert refund.status.value == "PENDING"


def test_refund_reject_does_not_execute(demo_api):
    client, session = demo_api
    run = _chat(client, "帮我把订单 ORD-1001 退款")
    assert run["run_status"] == "WAITING_HUMAN_APPROVAL"
    approval_id = run["approval"]["id"]
    assert run["approval"]["risk_level"] == "CRITICAL"

    resolved = client.post(
        f"{API}/demo/approvals/{approval_id}/reject", json={"resolved_by": "ops"}
    ).json()
    assert resolved["approval"]["status"] == "REJECTED"
    final = resolved["run"]
    assert final["run_status"] == "REJECTED"
    assert "未通过人工审核" in final["text"]

    refunds = list(
        session.scalars(select(Refund).where(Refund.order_id == 1001))
    )
    assert refunds == []


def test_session_history_and_run_lookup(demo_api):
    client, _ = demo_api
    sid = "history-1"
    _chat(client, "帮我查一下订单 ORD-1001", session_id=sid)
    _chat(client, "订单 ORD-1001 到哪里了？", session_id=sid)
    runs = client.get(f"{API}/demo/sessions/{sid}/runs").json()
    assert len(runs) == 2
    assert runs[0]["request_id"]
    detail = client.get(f"{API}/demo/runs/{runs[0]['request_id']}").json()
    assert detail["request_id"] == runs[0]["request_id"]


def test_cancel_short_order_ref_ord2_reaches_confirmation(demo_api, monkeypatch):
    """Regression: /demo/chat must recognize ORD-2 instead of clarifying.

    Reproduces the reported runtime failure on the real HTTP + AgentWorkflow
    path (LLM disabled so the run is offline/deterministic): the message
    "帮我取消订单 ORD-2" must produce CANCEL_ORDER + order_id ORD-2 and stop at
    user confirmation - never the generic clarification reply.
    """
    from app.core.config import Settings

    monkeypatch.setattr(
        "app.demo.service.get_settings",
        lambda: Settings(llm_enabled=False, deepseek_api_key=""),
    )
    client, session = demo_api
    run = _chat(client, "帮我取消订单 ORD-2")
    assert run["intent"] == "CANCEL_ORDER"
    assert run["route"] == "CANCEL_TOOL"
    assert run["run_status"] == "WAITING_USER_CONFIRMATION"
    assert run["entities"]["order_id"] == "ORD-2"
    assert run["action"]["type"] == "user_confirmation"
    session.expire_all()
    order = session.get(Order, 2)
    assert order is not None
    assert order.status.value != "CANCELLED"


# ---------------------------------------------------------------------------
# Phase 9B: after-sales case management over the real HTTP + workflow path
# ---------------------------------------------------------------------------


def test_after_sales_case_multi_turn_api(demo_api):
    """The objective's two-turn information-collection example, end to end."""
    client, session = demo_api
    first = _chat(client, "我的耳机坏了，帮我处理一下。")

    assert first["intent"] == "AFTER_SALES_REQUEST"
    assert first["route"] == "AFTER_SALES_CASE"
    assert first["agent_status"] == "needs_clarification"
    case = first["case"]
    assert case["case_id"].startswith("CASE-")
    assert case["created"] is True
    assert case["case_type"] == "QUALITY_ISSUE"
    assert case["requested_action"] == "UNKNOWN"
    assert case["problem_description"] == "我的耳机坏了"
    assert case["status"] == "INFORMATION_COLLECTION"
    assert case["missing_information"] == ["order_id", "requested_action"]
    assert first["text"] == (
        "可以帮你处理售后。请先提供对应的订单号，并告诉我是希望退款、换货还是维修。"
    )
    labels = [step["label"] for step in first["steps"]]
    assert "Case Upsert" in labels
    assert "Information Collection" in labels

    second = _chat(client, "订单是 ORD-1004，我想换货。")

    updated = second["case"]
    assert updated["case_id"] == case["case_id"]
    assert updated["created"] is False
    assert updated["order_ref"] == "ORD-1004"
    assert updated["requested_action"] == "EXCHANGE"
    assert updated["status"] == "ELIGIBILITY_CHECK"
    assert updated["missing_information"] == []
    assert second["text"] == (
        "已获取订单 ORD-1004 和换货诉求，接下来可以检查该订单是否符合换货条件。"
    )
    # Phase 9B manages case state only: one case, no business write.
    assert len(AfterSalesService(session).list_cases(user_id=1)) == 1


def test_non_after_sales_chat_has_no_case(demo_api):
    client, session = demo_api
    run = _chat(client, "帮我写一份 Python 教程")

    assert run["case"] is None
    assert run["intent"] == "UNSUPPORTED"
    assert run["route"] == "ESCALATE"
    assert AfterSalesService(session).list_cases() == []


def test_case_service_failure_keeps_chat_working(demo_api, monkeypatch):
    client, session = demo_api

    def boom(self, **kwargs):  # noqa: ANN001 - test stub
        raise RuntimeError("case service down")

    monkeypatch.setattr(AfterSalesCaseManager, "handle", boom)
    run = _chat(client, "我的耳机坏了，帮我处理一下。")

    assert run["route"] == "ESCALATE"
    assert run["case"] is None
    assert run["text"].strip()
    assert AfterSalesService(session).list_cases() == []
