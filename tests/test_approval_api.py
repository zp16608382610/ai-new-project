"""Phase 5 Approval API tests (spec items 10-14 at the HTTP boundary).

    GET  /api/v1/approvals              -> pending approvals
    POST /api/v1/approvals/{id}/approve -> PENDING -> APPROVED
    POST /api/v1/approvals/{id}/reject  -> PENDING -> REJECTED

PENDING -> APPROVED, PENDING -> REJECTED; already-resolved approvals answer
409 and missing ids answer 404.
"""
import pytest
from fastapi.testclient import TestClient

import phase5_helpers as helpers
from app.db.base import Base
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory, get_db
from app.main import create_app
from app.services.approval_service import ApprovalService

API = "/api/v1"


@pytest.fixture()
def api():
    engine, session = helpers.build_in_memory_session()
    assert seed_dev_data(session) is True
    session.commit()

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
    session.close()
    engine.dispose()


def _create_approval(session, *, tool_name="create_refund", order_ref="ORD-1001"):
    service = ApprovalService(session)
    return service.create(
        request_id="req-api",
        tool_name=tool_name,
        tool_arguments={"order_id": order_ref},
        risk_level="CRITICAL",
        reason="high value refund",
        user_id=1,
    )


def test_list_pending_returns_only_pending(api):
    client, session = api
    first = _create_approval(session)
    second = _create_approval(session, order_ref="ORD-1003")

    resp = client.get(f"{API}/approvals")
    assert resp.status_code == 200
    body = resp.json()
    assert [item["id"] for item in body] == [second.id, first.id]  # newest first
    assert body[0]["status"] == "PENDING"
    assert body[0]["tool_name"] == "create_refund"
    assert body[0]["tool_arguments"] == {"order_id": "ORD-1003"}
    assert body[0]["risk_level"] == "CRITICAL"


def test_list_pending_is_empty_without_requests(api):
    client, _ = api
    resp = client.get(f"{API}/approvals")
    assert resp.status_code == 200
    assert resp.json() == []


def test_approve_pending_without_body(api):
    # Optional resolve body: a bare POST is valid.
    client, session = api
    approval = _create_approval(session)
    resp = client.post(f"{API}/approvals/{approval.id}/approve")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == approval.id
    assert body["status"] == "APPROVED"
    assert body["resolved_by"] is None
    assert body["resolved_at"] is not None


def test_approve_records_resolved_by(api):
    client, session = api
    approval = _create_approval(session)
    resp = client.post(
        f"{API}/approvals/{approval.id}/approve", json={"resolved_by": "ops-1"}
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "APPROVED"
    assert resp.json()["resolved_by"] == "ops-1"


def test_duplicate_approve_conflict_409(api):
    # APPROVED cannot be approved again (spec 13).
    client, session = api
    approval = _create_approval(session)
    assert client.post(f"{API}/approvals/{approval.id}/approve").status_code == 200
    resp = client.post(f"{API}/approvals/{approval.id}/approve")
    assert resp.status_code == 409
    assert resp.json()["code"] == "APPROVAL_ALREADY_RESOLVED"


def test_approve_after_reject_conflict_409(api):
    # REJECTED cannot be approved again (spec 14).
    client, session = api
    approval = _create_approval(session)
    assert client.post(f"{API}/approvals/{approval.id}/reject").status_code == 200
    resp = client.post(f"{API}/approvals/{approval.id}/approve")
    assert resp.status_code == 409
    assert resp.json()["code"] == "APPROVAL_ALREADY_RESOLVED"


def test_reject_pending_moves_out_of_pending_list(api):
    client, session = api
    approval = _create_approval(session)
    resp = client.post(f"{API}/approvals/{approval.id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "REJECTED"

    listed = client.get(f"{API}/approvals").json()
    assert all(item["id"] != approval.id for item in listed)


def test_approve_missing_approval_404(api):
    client, _ = api
    resp = client.post(f"{API}/approvals/99999/approve")
    assert resp.status_code == 404
    assert resp.json()["code"] == "APPROVAL_NOT_FOUND"


def test_reject_missing_approval_404(api):
    client, _ = api
    resp = client.post(f"{API}/approvals/99999/reject")
    assert resp.status_code == 404
    assert resp.json()["code"] == "APPROVAL_NOT_FOUND"