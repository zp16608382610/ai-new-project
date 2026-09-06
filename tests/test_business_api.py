"""Phase 2B Mock Business API tests.

策略:与 Phase 2A 相同,使用内存 SQLite(开启外键)+ 确定性 seed。
PostgreSQL / Docker 不可用,因此这里只运行 SQLite 单元级 API 测试。

覆盖:订单/物流/用户订单查询、退款资格判定、退款创建、订单取消、
工单创建,以及「API 必须经由 Repository 访问数据库」的边界验证。
"""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db.base import Base
from app.db.enums import OrderStatus, RefundStatus
from app.db.models import Order, OrderItem, Refund, User
from app.db.repository import OrderRepository
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory, get_db
from app.main import create_app

API = "/api/v1"


class ApiCtx:
    """Test context: FastAPI TestClient + the shared SQLite session."""

    def __init__(self, client: TestClient, session) -> None:
        self.client = client
        self.session = session


@pytest.fixture()
def api() -> ApiCtx:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = create_session_factory(engine)()
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
        yield ApiCtx(client=client, session=session)
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


def _find_order(session, email: str, status: OrderStatus) -> Order:
    stmt = (
        select(Order)
        .join(Order.user)
        .where(User.email == email, Order.status == status)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
    )
    order = session.scalars(stmt).first()
    assert order is not None, f"seed order missing: {email} {status}"
    return order


def _as_float(value) -> float:
    return float(value)


# ---------------------------------------------------------------- orders
def test_get_order_returns_aggregate(api):
    order = _find_order(api.session, "alice@example.com", OrderStatus.PAID)
    resp = api.client.get(f"{API}/orders/{order.id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == order.id
    assert body["status"] == "PAID"
    assert body["user"]["id"] == order.user_id
    assert body["user"]["name"] == "Alice Zhang"
    assert body["currency"] == "CNY"
    assert len(body["items"]) == len(order.items)
    assert _as_float(body["total_amount"]) == pytest.approx(float(order.total_amount), abs=0.001)
    assert body["created_at"] and body["updated_at"]
    assert "email" not in body["user"]  # 不暴露多余内部字段


def test_get_order_not_found(api):
    resp = api.client.get(f"{API}/orders/999999")
    assert resp.status_code == 404
    assert resp.json()["code"] == "ORDER_NOT_FOUND"


# ------------------------------------------------------------- logistics
def test_get_logistics_existing(api):
    order = _find_order(api.session, "alice@example.com", OrderStatus.PAID)  # SF Express, IN_TRANSIT
    resp = api.client.get(f"{API}/orders/{order.id}/logistics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["carrier"] == "SF Express"
    assert body["tracking_number"].startswith("SF")
    assert body["status"] == "IN_TRANSIT"
    assert body["estimated_delivery"]
    assert body["updated_at"]


def test_get_logistics_nonexistent_order(api):
    resp = api.client.get(f"{API}/orders/999999/logistics")
    assert resp.status_code == 404
    assert resp.json()["code"] == "ORDER_NOT_FOUND"


def test_get_logistics_order_without_record(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.DELIVERED)  # o10: no logistics
    resp = api.client.get(f"{API}/orders/{order.id}/logistics")
    assert resp.status_code == 404
    assert resp.json()["code"] == "LOGISTICS_NOT_FOUND"


# -------------------------------------------------------- user order list
def test_list_user_orders(api):
    resp = api.client.get(f"{API}/users/1/orders")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 4  # Alice 有 4 个订单
    assert {o["status"] for o in body} == {"PENDING", "PAID", "SHIPPED", "DELIVERED"}


def test_list_user_orders_status_filter(api):
    resp = api.client.get(f"{API}/users/1/orders?status=PAID")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["status"] == "PAID"


def test_list_user_orders_invalid_status(api):
    resp = api.client.get(f"{API}/users/1/orders?status=BOGUS")
    assert resp.status_code == 422
    assert resp.json()["code"] == "VALIDATION_ERROR"


def test_list_user_orders_user_not_found(api):
    resp = api.client.get(f"{API}/users/999999/orders")
    assert resp.status_code == 404
    assert resp.json()["code"] == "USER_NOT_FOUND"


# ----------------------------------------------------- refund eligibility
def test_refund_eligibility_refundable(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.DELIVERED)  # 全可退,无退款
    resp = api.client.post(f"{API}/refunds/check-eligibility", json={"order_id": order.id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is True
    assert _as_float(body["refund_amount"]) == pytest.approx(float(order.total_amount), abs=0.001)


def test_refund_eligibility_non_refundable(api):
    order = _find_order(api.session, "bob@example.com", OrderStatus.DELIVERED)  # watch: returnable=False
    resp = api.client.post(f"{API}/refunds/check-eligibility", json={"order_id": order.id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is False
    assert "non-returnable" in body["reason"]
    assert body["refund_amount"] == 0


def test_refund_eligibility_already_refunded(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.REFUNDED)  # o9: completed refund
    resp = api.client.post(f"{API}/refunds/check-eligibility", json={"order_id": order.id})
    assert resp.status_code == 200
    body = resp.json()
    assert body["eligible"] is False
    assert "already been refunded" in body["reason"]


def test_refund_eligibility_nonexistent_order(api):
    resp = api.client.post(f"{API}/refunds/check-eligibility", json={"order_id": 999999})
    assert resp.status_code == 404
    assert resp.json()["code"] == "ORDER_NOT_FOUND"


# ----------------------------------------------------------- refund create
def test_refund_create_valid_and_authoritative_amount(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.DELIVERED)
    resp = api.client.post(
        f"{API}/refunds",
        json={"order_id": order.id, "reason": "change_of_mind", "amount": 1.0},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"
    assert body["user_id"] == order.user_id
    # 金额来自订单权威数据,而不是请求里的任意 amount
    assert _as_float(body["amount"]) == pytest.approx(float(order.total_amount), abs=0.001)


def test_refund_create_duplicate_rejected(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.DELIVERED)
    first = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "a"})
    assert first.status_code == 201
    second = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "b"})
    assert second.status_code == 409
    assert second.json()["code"] == "DUPLICATE_REFUND"


def test_refund_create_already_refunded_rejected(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.REFUNDED)
    resp = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "again"})
    assert resp.status_code == 409
    assert resp.json()["code"] == "ORDER_ALREADY_REFUNDED"


def test_refund_create_invalid_order_rejected(api):
    resp = api.client.post(f"{API}/refunds", json={"order_id": 999999, "reason": "x"})
    assert resp.status_code == 404
    assert resp.json()["code"] == "ORDER_NOT_FOUND"


def test_refund_create_not_eligible_rejected(api):
    order = _find_order(api.session, "bob@example.com", OrderStatus.DELIVERED)  # non-returnable
    resp = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "x"})
    assert resp.status_code == 422
    assert resp.json()["code"] == "REFUND_NOT_ELIGIBLE"


def test_refund_record_persisted(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.DELIVERED)
    resp = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "persist"})
    assert resp.status_code == 201
    refund = api.session.scalar(
        select(Refund).where(Refund.order_id == order.id, Refund.status == RefundStatus.PENDING)
    )
    assert refund is not None
    assert Decimal(str(resp.json()["amount"])) == order.total_amount


# ------------------------------------------------------------- cancellation
def test_cancel_cancellable_order(api):
    order = _find_order(api.session, "alice@example.com", OrderStatus.PENDING)
    resp = api.client.post(f"{API}/orders/{order.id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["previous_status"] == "PENDING"
    assert body["status"] == "CANCELLED"
    # 状态已持久化
    get_resp = api.client.get(f"{API}/orders/{order.id}")
    assert get_resp.json()["status"] == "CANCELLED"


def test_cancel_already_cancelled_rejected(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.CANCELLED)
    resp = api.client.post(f"{API}/orders/{order.id}/cancel")
    assert resp.status_code == 409
    assert resp.json()["code"] == "ORDER_ALREADY_CANCELLED"


def test_cancel_delivered_rejected(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.DELIVERED)
    resp = api.client.post(f"{API}/orders/{order.id}/cancel")
    assert resp.status_code == 422
    assert resp.json()["code"] == "ORDER_NOT_CANCELLABLE"


def test_cancel_refunded_rejected(api):
    order = _find_order(api.session, "carol@example.com", OrderStatus.REFUNDED)
    resp = api.client.post(f"{API}/orders/{order.id}/cancel")
    assert resp.status_code == 422
    assert resp.json()["code"] == "ORDER_NOT_CANCELLABLE"


# ----------------------------------------------------------------- tickets
def test_create_ticket_valid(api):
    resp = api.client.post(
        f"{API}/tickets",
        json={
            "user_id": 1,
            "order_id": 2,
            "category": "REFUND",
            "priority": "HIGH",
            "description": "需要加快处理退款",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_id"] == 1
    assert body["order_id"] == 2
    assert body["category"] == "REFUND"
    assert body["priority"] == "HIGH"
    assert body["status"] == "OPEN"
    assert body["id"] > 0


def test_create_ticket_invalid_user(api):
    resp = api.client.post(
        f"{API}/tickets",
        json={"user_id": 999999, "category": "REFUND", "description": "x"},
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "USER_NOT_FOUND"


def test_create_ticket_invalid_order(api):
    resp = api.client.post(
        f"{API}/tickets",
        json={
            "user_id": 1,
            "order_id": 999999,
            "category": "REFUND",
            "description": "x",
        },
    )
    assert resp.status_code == 404
    assert resp.json()["code"] == "ORDER_NOT_FOUND"


# --------------------------------------------------- repository boundary
def test_api_uses_repository_layer(api, monkeypatch):
    order = _find_order(api.session, "alice@example.com", OrderStatus.PAID)
    calls: list[int] = []
    original = OrderRepository.get_full

    def spy(self, order_id):
        calls.append(order_id)
        return original(self, order_id)

    monkeypatch.setattr(OrderRepository, "get_full", spy)
    resp = api.client.get(f"{API}/orders/{order.id}")
    assert resp.status_code == 200
    assert calls == [order.id]  # API -> Service -> Repository(而非直接 SQL)