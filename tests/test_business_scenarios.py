"""Phase 2C business scenario tests.

验证 Phase 2A 数据层 + Phase 2B Mock Business API 是否共同支撑核心售后
业务工作流(而非只测单个函数):订单/物流查询、退款资格矩阵、退款创建、
重复退款、订单取消、工单创建,以及跨域一致性(退款↔取消)与业务不变量。

策略:内存 SQLite(外键开启)+ 确定性 seed;每个测试独立数据库实例。
PostgreSQL/Docker 不可用,不声称任何 PG 集成测试。
"""
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db.base import Base
from app.db.enums import LogisticsStatus, OrderStatus, RefundStatus
from app.db.models import Logistics, Order, OrderItem, Product, Refund, Ticket, User
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory, get_db
from app.main import create_app

API = "/api/v1"


class ApiCtx:
    """Shared TestClient + engine/session/factory for persistence checks."""

    def __init__(self, client: TestClient, engine, session: Session, factory) -> None:
        self.client = client
        self.engine = engine
        self.session = session
        self.factory = factory


@pytest.fixture()
def api() -> ApiCtx:
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
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
        yield ApiCtx(client=client, engine=engine, session=session, factory=factory)
    app.dependency_overrides.clear()
    session.close()
    engine.dispose()


def _load_order(session: Session, email: str, status: OrderStatus) -> Order:
    stmt = (
        select(Order)
        .join(Order.user)
        .where(User.email == email, Order.status == status)
        .options(selectinload(Order.items).selectinload(OrderItem.product))
    )
    order = session.scalars(stmt).first()
    assert order is not None, f"seed order missing: {email} {status}"
    return order


def _count(session: Session, model) -> int:
    return session.scalar(select(func.count()).select_from(model))


def _refunds_of(session: Session, order_id: int) -> list[Refund]:
    return list(
        session.scalars(
            select(Refund).where(Refund.order_id == order_id).order_by(Refund.id)
        )
    )


def _assert_controlled_error(response, status_code: int, code: str) -> dict:
    """统一错误结构 + 不泄漏数据库实现细节 + 非 500。"""
    assert response.status_code == status_code
    body = response.json()
    assert body["status"] == "error"
    assert body["code"] == code
    assert "detail" in body
    raw = response.text.lower()
    assert "sqlalchemy" not in raw
    assert "traceback" not in raw
    assert "integrityerror" not in raw
    return body


# --------------------------------------------------------------- S1 order
def test_scenario_s1_query_order(api):
    order = _load_order(api.session, "alice@example.com", OrderStatus.PAID)
    resp = api.client.get(f"{API}/orders/{order.id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["id"] == order.id
    assert body["status"] == "PAID"
    # 用户关系
    assert body["user"]["id"] == order.user_id
    assert body["user"]["name"] == "Alice Zhang"
    # 订单明细齐全且金额正确(Σ qty * unit_price)
    assert len(body["items"]) == len(order.items)
    line_total = sum(
        Decimal(str(i["quantity"])) * Decimal(str(i["unit_price"])) for i in body["items"]
    )
    assert line_total == order.total_amount
    assert Decimal(str(body["total_amount"])) == order.total_amount
    assert body["currency"] == "CNY"
    assert body["created_at"] and body["updated_at"]
    # 面向客服的视图不应暴露数据库内部字段
    assert "user_id" not in body
    assert set(body["user"]) == {"id", "name"}


# ---------------------------------------------------------- S2 logistics
def test_scenario_s2_query_logistics(api):
    order = _load_order(api.session, "alice@example.com", OrderStatus.SHIPPED)  # YTO OUT_FOR_DELIVERY
    resp = api.client.get(f"{API}/orders/{order.id}/logistics")
    assert resp.status_code == 200
    body = resp.json()
    assert body["carrier"] == "YTO Express"
    assert body["tracking_number"] == "YT10020002"
    assert body["status"] == "OUT_FOR_DELIVERY"
    assert body["estimated_delivery"] == "2026-08-07"
    assert body["updated_at"]


def test_scenario_s2_order_without_logistics(api):
    order = _load_order(api.session, "carol@example.com", OrderStatus.DELIVERED)  # o10: 无物流记录
    resp = api.client.get(f"{API}/orders/{order.id}/logistics")
    _assert_controlled_error(resp, 404, "LOGISTICS_NOT_FOUND")


# ---------------------------------------------------- S3 nonexistent order
def test_scenario_s3_nonexistent_order(api):
    resp = api.client.get(f"{API}/orders/999999")
    body = _assert_controlled_error(resp, 404, "ORDER_NOT_FOUND")
    assert resp.status_code == 404  # 明确非 500


# ----------------------------------------------- S4 refund eligibility matrix
def test_scenario_s4_refund_eligibility_matrix(api):
    cases = [
        # 标签: email, 状态, 期望 eligible, reason 片段
        ("eligible_delivered", "carol@example.com", OrderStatus.DELIVERED, True, None),
        ("non_refundable_product", "bob@example.com", OrderStatus.DELIVERED, False, "non-returnable"),
        ("already_refunded", "carol@example.com", OrderStatus.REFUNDED, False, "already been refunded"),
        ("inflight_refund", "alice@example.com", OrderStatus.DELIVERED, False, "pending refund request"),
        ("cancelled", "carol@example.com", OrderStatus.CANCELLED, False, "cancelled"),
        ("not_paid", "alice@example.com", OrderStatus.PENDING, False, "not been paid"),
        ("not_delivered", "alice@example.com", OrderStatus.PAID, False, "not been delivered"),
        ("shipped_not_delivered", "alice@example.com", OrderStatus.SHIPPED, False, "not been delivered"),
    ]
    for label, email, status, eligible, reason_fragment in cases:
        order = _load_order(api.session, email, status)
        resp = api.client.post(f"{API}/refunds/check-eligibility", json={"order_id": order.id})
        assert resp.status_code == 200, label
        body = resp.json()
        assert body["eligible"] is eligible, label
        if eligible:
            assert Decimal(str(body["refund_amount"])) == order.total_amount, label
        else:
            assert body["refund_amount"] == 0.0, label
            assert reason_fragment in body["reason"].lower(), (label, body["reason"])
# ---------------------------------------------------- S5 create refund flow
def test_scenario_s5_create_refund_full_flow(api):
    order = _load_order(api.session, "carol@example.com", OrderStatus.DELIVERED)  # 全可退、无退款
    before = _count(api.session, Refund)

    # 客户端尝试伪造金额(extra fields,Pydantic 默认忽略)
    resp = api.client.post(
        f"{API}/refunds",
        json={
            "order_id": order.id,
            "reason": "change_of_mind",
            "amount": 0.01,
            "refund_amount": 0.01,
            "status": "APPROVED",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "PENDING"          # 客户端无法伪造状态
    assert body["order_id"] == order.id
    assert body["user_id"] == order.user_id
    # 权威金额 = 订单 total_amount,而非请求里的 0.01
    assert Decimal(str(body["amount"])) == order.total_amount

    # 数据库持久化(独立 session 复读,验证跨请求提交)
    other = api.factory()
    try:
        row = other.scalar(select(Refund).where(Refund.order_id == order.id))
        assert row is not None
        assert row.status == RefundStatus.PENDING
        assert row.user_id == order.user_id
        assert row.amount == order.total_amount
        assert _count(other, Refund) == before + 1
    finally:
        other.close()


# ------------------------------------------------------ S6 duplicate refund
def test_scenario_s6_duplicate_refund_rejected(api):
    order = _load_order(api.session, "carol@example.com", OrderStatus.DELIVERED)
    first = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "a"})
    assert first.status_code == 201

    second = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "b"})
    _assert_controlled_error(second, 409, "DUPLICATE_REFUND")

    assert _count(api.session, Refund) == 4  # 3 条 seed + 1 条新增;重复请求未创建新记录


def test_scenario_s6_completed_order_duplicate_rejected(api):
    order = _load_order(api.session, "carol@example.com", OrderStatus.REFUNDED)
    resp = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "again"})
    _assert_controlled_error(resp, 409, "ORDER_ALREADY_REFUNDED")
    assert _count(api.session, Refund) == 3  # seed 数量不变


# ----------------------------------------------- S7 cancel order state machine
@pytest.mark.parametrize(
    ("email", "status"),
    [
        ("alice@example.com", OrderStatus.PENDING),
        ("alice@example.com", OrderStatus.PAID),
        ("alice@example.com", OrderStatus.SHIPPED),
    ],
)
def test_scenario_s7_valid_cancellation_persists(api, email, status):
    order = _load_order(api.session, email, status)
    resp = api.client.post(f"{API}/orders/{order.id}/cancel")
    assert resp.status_code == 200
    body = resp.json()
    assert body["order_id"] == order.id
    assert body["previous_status"] == status.value
    assert body["status"] == "CANCELLED"
    # 状态转移持久化
    other = api.factory()
    try:
        fresh = other.get(Order, order.id)
        assert fresh.status == OrderStatus.CANCELLED
    finally:
        other.close()


@pytest.mark.parametrize(
    ("email", "status", "http_status", "code"),
    [
        ("carol@example.com", OrderStatus.CANCELLED, 409, "ORDER_ALREADY_CANCELLED"),
        ("bob@example.com", OrderStatus.DELIVERED, 422, "ORDER_NOT_CANCELLABLE"),
        ("carol@example.com", OrderStatus.REFUNDED, 422, "ORDER_NOT_CANCELLABLE"),
        # 跨域(见 test_cross_domain_refund_blocks_cancel):DELIVERED + PENDING 退款
        # 因订单已签收不可取消而返回 422 ORDER_NOT_CANCELLABLE(退款检查在其后,
        # 属防御性守卫:仅对理论上的 PENDING/PAID/SHIPPED+在途退款生效)。
    ],
)
def test_scenario_s7_invalid_cancellations_rejected(api, email, status, http_status, code):
    order = _load_order(api.session, email, status)
    resp = api.client.post(f"{API}/orders/{order.id}/cancel")
    _assert_controlled_error(resp, http_status, code)
    # 状态未被破坏
    other = api.factory()
    try:
        fresh = other.get(Order, order.id)
        assert fresh.status == status
    finally:
        other.close()


# ----------------------------------------------------- S8 create support ticket
def test_scenario_s8_ticket_created_and_persisted(api):
    before = _count(api.session, Ticket)
    resp = api.client.post(
        f"{API}/tickets",
        json={
            "user_id": 1,
            "order_id": 2,
            "category": "PRODUCT_QUALITY",
            "priority": "HIGH",
            "description": "外壳划痕,申请补偿",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["user_id"] == 1
    assert body["order_id"] == 2
    assert body["category"] == "PRODUCT_QUALITY"
    assert body["priority"] == "HIGH"
    assert body["status"] == "OPEN"

    other = api.factory()
    try:
        row = other.get(Ticket, body["id"])
        assert row is not None
        assert row.user_id == 1 and row.order_id == 2
        assert _count(other, Ticket) == before + 1
    finally:
        other.close()


def test_scenario_s8_ticket_invalid_user(api):
    before = _count(api.session, Ticket)
    resp = api.client.post(
        f"{API}/tickets",
        json={"user_id": 999999, "category": "REFUND", "description": "x"},
    )
    _assert_controlled_error(resp, 404, "USER_NOT_FOUND")
    assert _count(api.session, Ticket) == before


def test_scenario_s8_ticket_invalid_order(api):
    before = _count(api.session, Ticket)
    resp = api.client.post(
        f"{API}/tickets",
        json={"user_id": 1, "order_id": 999999, "category": "REFUND", "description": "x"},
    )
    _assert_controlled_error(resp, 404, "ORDER_NOT_FOUND")
    assert _count(api.session, Ticket) == before


def test_scenario_s8_ticket_without_order(api):
    """用户级工单(order_id 省略)应被当前领域模型允许。"""
    resp = api.client.post(
        f"{API}/tickets",
        json={"user_id": 3, "category": "ACCOUNT", "description": "账户相关问题"},
    )
    assert resp.status_code == 201
    assert resp.json()["order_id"] is None
# -------------------------------------------------- cross-domain consistency
def test_cross_domain_refund_blocks_cancel(api):
    order = _load_order(api.session, "carol@example.com", OrderStatus.DELIVERED)  # 无退款 → 可创建
    create = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "r"})
    assert create.status_code == 201

    cancel = api.client.post(f"{API}/orders/{order.id}/cancel")
    # 判定顺序:DELIVERED 订单不可取消(DELIVERED → CANCELLED 属非法转移),因此返回
    # 422 ORDER_NOT_CANCELLABLE;在途退款的存在使取消无论如何都被拒绝。
    _assert_controlled_error(cancel, 422, "ORDER_NOT_CANCELLABLE")

    # 订单状态未被破坏,退款记录仍在
    assert api.session.get(Order, order.id).status == OrderStatus.DELIVERED
    assert any(r.status == RefundStatus.PENDING for r in _refunds_of(api.session, order.id))


def test_cross_domain_cancel_blocks_refund(api):
    order = _load_order(api.session, "alice@example.com", OrderStatus.PENDING)
    cancel = api.client.post(f"{API}/orders/{order.id}/cancel")
    assert cancel.status_code == 200

    check = api.client.post(f"{API}/refunds/check-eligibility", json={"order_id": order.id})
    assert check.status_code == 200
    assert check.json()["eligible"] is False
    assert "cancelled" in check.json()["reason"].lower()

    create = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "late"})
    _assert_controlled_error(create, 422, "REFUND_NOT_ELIGIBLE")
    assert not _refunds_of(api.session, order.id)  # 未创建任何退款
    assert api.session.get(Order, order.id).status == OrderStatus.CANCELLED


def test_cross_domain_already_refunded_blocks_refund(api):
    order = _load_order(api.session, "carol@example.com", OrderStatus.REFUNDED)
    before = _count(api.session, Refund)
    resp = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "again"})
    _assert_controlled_error(resp, 409, "ORDER_ALREADY_REFUNDED")
    assert _count(api.session, Refund) == before


# ------------------------------------------------------------ invariants
def test_invariant_refund_amount_equals_order_total(api):
    """退款金额不变量:refund.amount == order.total_amount。"""
    refunds = api.session.scalars(select(Refund)).all()
    assert refunds, "seed refunds expected"
    for refund in refunds:
        order = api.session.get(Order, refund.order_id)
        assert refund.amount == order.total_amount
        assert refund.user_id == order.user_id


def test_invariant_cancelled_order_cannot_become_refunded(api):
    order = _load_order(api.session, "alice@example.com", OrderStatus.PENDING)
    api.client.post(f"{API}/orders/{order.id}/cancel")
    for _ in range(2):
        resp = api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "x"})
        assert resp.status_code == 422
    assert api.session.get(Order, order.id).status == OrderStatus.CANCELLED
    assert not _refunds_of(api.session, order.id)


def test_invariant_single_active_refund_per_order(api):
    order = _load_order(api.session, "carol@example.com", OrderStatus.DELIVERED)
    api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "a"})
    api.client.post(f"{API}/refunds", json={"order_id": order.id, "reason": "b"})  # 409

    active = [
        r
        for r in _refunds_of(api.session, order.id)
        if r.status in (RefundStatus.PENDING, RefundStatus.APPROVED)
    ]
    assert len(active) == 1


def test_invariant_ticket_references_valid_records(api):
    """ticket.user_id 必须存在;order_id 非空时必须指向存在的订单。"""
    tickets = api.session.scalars(select(Ticket)).all()
    assert tickets
    for ticket in tickets:
        assert api.session.get(User, ticket.user_id) is not None
        if ticket.order_id is not None:
            assert api.session.get(Order, ticket.order_id) is not None


# ------------------------------------------- architecture boundary (2C scope)
def test_scenario_cancel_flows_through_service_and_repository(api, monkeypatch):
    """取消订单必须经由 Service → Repository;路由不直接执行 SQL。"""
    from app.db.repository import OrderRepository, RefundRepository

    order = _load_order(api.session, "alice@example.com", OrderStatus.PENDING)
    seen = []

    orig_full = OrderRepository.get_full

    def spy_full(self, order_id):
        seen.append(("order.get_full", order_id))
        return orig_full(self, order_id)

    orig_active = RefundRepository.list_active_by_order

    def spy_active(self, order_id):
        seen.append(("refund.active", order_id))
        return orig_active(self, order_id)

    monkeypatch.setattr(OrderRepository, "get_full", spy_full)
    monkeypatch.setattr(RefundRepository, "list_active_by_order", spy_active)

    resp = api.client.post(f"{API}/orders/{order.id}/cancel")
    assert resp.status_code == 200
    assert ("order.get_full", order.id) in seen
    assert ("refund.active", order.id) in seen
    assert api.session.get(Order, order.id).status == OrderStatus.CANCELLED
