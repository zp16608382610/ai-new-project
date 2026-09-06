"""Database model / constraint / seed tests.

测试策略:本机没有 Docker/PostgreSQL,数据库测试使用 SQLite 内存库
并开启 PRAGMA foreign_keys=ON 以验证外键约束。PostgreSQL 集成验证
留待具备 Docker 的环境执行(本阶段无 pgvector 需求)。
"""
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from app.db.base import Base
from app.db.enums import (
    LogisticsStatus,
    OrderStatus,
    RefundStatus,
    TicketPriority,
    TicketStatus,
)
from app.db.models import Logistics, Order, OrderItem, Product, Refund, Ticket, User
from app.db.repository import UserRepository
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory


@pytest.fixture()
def db_session():
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = create_session_factory(engine)
    session = factory()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _add(session, entity):
    session.add(entity)
    session.flush()
    return entity


def _user(session, name="Ada", email="ada@example.com"):
    return _add(session, User(name=name, email=email))


def _product(session, name="Test Product", price="99.00", returnable=True):
    return _add(
        session,
        Product(name=name, category="Test", price=Decimal(price), returnable=returnable),
    )


def _order(session, user, status=OrderStatus.PAID, amount="100.00"):
    return _add(
        session,
        Order(user_id=user.id, status=status, total_amount=Decimal(amount), currency="CNY"),
    )


def test_1_user_can_be_created(db_session):
    user = UserRepository(db_session).create(name="Ada Lovelace", email="ada@example.com")
    assert user.id is not None
    fetched = db_session.get(User, user.id)
    assert fetched is not None
    assert fetched.email == "ada@example.com"
    assert fetched.created_at is not None


def test_2_order_belongs_to_user(db_session):
    user = _user(db_session)
    order = _order(db_session, user)
    assert order.user is user
    assert order in user.orders
    assert UserRepository(db_session).get_by_email("ada@example.com") is user


def test_3_order_item_belongs_to_order_and_product(db_session):
    user = _user(db_session)
    product = _product(db_session)
    order = _order(db_session, user)
    item = _add(
        db_session,
        OrderItem(
            order_id=order.id,
            product_id=product.id,
            product_name=product.name,
            quantity=2,
            unit_price=Decimal("99.00"),
        ),
    )
    assert item.order is order
    assert item.product is product
    assert order.items == [item]
    assert product.items == [item]


def test_4_logistics_belongs_to_order(db_session):
    user = _user(db_session)
    order = _order(db_session, user)
    logistics = _add(
        db_session,
        Logistics(
            order_id=order.id,
            carrier="SF Express",
            tracking_number="SF0001",
            status=LogisticsStatus.IN_TRANSIT,
        ),
    )
    assert logistics.order is order
    assert order.logistics == [logistics]


def test_5_refund_belongs_to_order_and_user(db_session):
    user = _user(db_session)
    order = _order(db_session, user)
    refund = _add(
        db_session,
        Refund(
            order_id=order.id,
            user_id=user.id,
            amount=Decimal("50.00"),
            status=RefundStatus.PENDING,
            reason="test refund",
        ),
    )
    assert refund.order is order
    assert refund.user is user
    assert order.refunds == [refund]


def test_6_ticket_belongs_to_user_and_order(db_session):
    user = _user(db_session)
    order = _order(db_session, user)
    ticket = _add(
        db_session,
        Ticket(
            user_id=user.id,
            order_id=order.id,
            category="REFUND",
            priority=TicketPriority.MEDIUM,
            status=TicketStatus.OPEN,
            description="test ticket",
        ),
    )
    assert ticket.user is user
    assert ticket.order is order
    assert user.tickets == [ticket]


def test_7_foreign_key_is_enforced(db_session):
    user = _user(db_session)
    order = _order(db_session, user)
    db_session.commit()

    # orders.user_id -> users.id (missing user) must be rejected
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("INSERT INTO orders (user_id, status, total_amount, currency) VALUES (99999, 'PAID', 1.00, 'CNY')")
        )
    db_session.rollback()

    # order_items.product_id -> products.id (missing product) must be rejected
    with pytest.raises(IntegrityError):
        db_session.execute(
            text("INSERT INTO order_items (order_id, product_id, product_name, quantity, unit_price) VALUES (:o, 99999, 'ghost', 1, 1.00)"),
            {"o": order.id},
        )
    db_session.rollback()

    # deleting a user still referenced by orders must be rejected
    with pytest.raises(IntegrityError):
        db_session.delete(user)
        db_session.flush()
    db_session.rollback()

def test_8_status_columns_reject_invalid_values(db_session):
    user = _user(db_session)
    order = _order(db_session, user)
    db_session.commit()

    cases = [
        (
            "orders.status",
            "INSERT INTO orders (user_id, status, total_amount, currency) VALUES (:u, 'BOGUS', 1.00, 'CNY')",
            {"u": user.id},
        ),
        (
            "logistics.status",
            "INSERT INTO logistics (order_id, carrier, tracking_number, status) VALUES (:o, 'SF', 'TN-BOGUS-1', 'BOGUS')",
            {"o": order.id},
        ),
        (
            "refunds.status",
            "INSERT INTO refunds (order_id, user_id, amount, status) VALUES (:o, :u, 1.00, 'BOGUS')",
            {"o": order.id, "u": user.id},
        ),
        (
            "tickets.status",
            "INSERT INTO tickets (user_id, order_id, category, priority, status, description) VALUES (:u, :o, 'REFUND', 'MEDIUM', 'BOGUS', 'x')",
            {"u": user.id, "o": order.id},
        ),
        (
            "tickets.priority",
            "INSERT INTO tickets (user_id, order_id, category, priority, status, description) VALUES (:u, :o, 'REFUND', 'BOGUS', 'OPEN', 'x')",
            {"u": user.id, "o": order.id},
        ),
    ]
    for label, sql, params in cases:
        with pytest.raises(IntegrityError):
            db_session.execute(text(sql), params)
        db_session.rollback()
        assert label  # 确保每个用例都被执行


def test_seed_dev_data_is_deterministic_and_consistent(db_session):
    assert seed_dev_data(db_session) is True
    assert seed_dev_data(db_session) is False  # 幂等:已 seed 时不重复插入

    def count(model):
        return db_session.scalar(select(func.count()).select_from(model))

    assert count(User) == 3
    assert count(Order) == 10
    assert count(Product) == 8
    assert count(OrderItem) == 12
    assert count(Logistics) == 6
    assert count(Refund) == 3
    assert count(Ticket) == 3

    orders = list(db_session.scalars(select(Order).order_by(Order.id)))
    order_statuses = {o.status for o in orders}
    assert order_statuses == {s for s in OrderStatus}  # 覆盖全部 6 种订单状态

    logistics_statuses = {lg.status for lg in db_session.scalars(select(Logistics))}
    assert LogisticsStatus.EXCEPTION in logistics_statuses
    assert LogisticsStatus.DELIVERED in logistics_statuses

    # 业务规则 1:订单总额必须等于明细之和
    for o in orders:
        expected = sum((Decimal(i.quantity) * i.unit_price for i in o.items), Decimal("0.00"))
        assert o.total_amount == expected

    # 业务规则 2:至少 2 个「已签收且全部商品可退」的可退款订单
    refundable = [
        o
        for o in orders
        if o.status == OrderStatus.DELIVERED and all(i.product.returnable for i in o.items)
    ]
    assert len(refundable) >= 2

    # 业务规则 3:至少 1 个含不可退商品(不可全额退款)的订单
    non_refundable = [
        o
        for o in orders
        if o.status in (OrderStatus.DELIVERED, OrderStatus.PAID)
        and any(not i.product.returnable for i in o.items)
    ]
    assert len(non_refundable) >= 1

    # 业务规则 4:至少 1 个已取消订单(不可退款)
    assert any(o.status == OrderStatus.CANCELLED for o in orders)

    # 退款状态覆盖 PENDING / COMPLETED / REJECTED
    refund_statuses = {r.status for r in db_session.scalars(select(Refund))}
    assert {RefundStatus.PENDING, RefundStatus.COMPLETED, RefundStatus.REJECTED} <= refund_statuses
