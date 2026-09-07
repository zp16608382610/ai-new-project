"""Shared helpers for Phase 5 risk-control tests (sibling test modules import
this plain module; it is not a pytest plugin and defines no fixtures).

Mirrors the Phase 4B integration pattern: in-memory SQLite (FK on) + the
deterministic seed + ORD-* test orders. The injected AgentWorkflow wires the
Phase 5 stack:

    Agent -> ToolRequest -> RiskEngine -> Risk Gate -> ToolExecutor
          -> Service -> Repository -> DB -> Verify
"""
from datetime import date, datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.workflow import AgentWorkflow
from app.db.base import Base
from app.db.enums import LogisticsStatus, OrderStatus
from app.db.models import ApprovalRequest, Logistics, Order, OrderItem, Product, Refund, User
from app.db.seed import seed_dev_data
from app.db.session import create_db_engine, create_session_factory
from app.risk import RiskEngine
from app.services.approval_service import ApprovalService
from app.services.verification import BusinessVerifier
from app.tools.executor import ToolExecutor
from app.tools.handlers import build_default_registry

UTC = timezone.utc


def build_in_memory_session():
    """Create an in-memory SQLite engine + session with all tables."""
    engine = create_db_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = create_session_factory(engine)()
    return engine, session


def _add_order(session, order_id, user, status, product, quantity, created_at):
    order = Order(
        id=order_id,
        user=user,
        status=status,
        total_amount=product.price * quantity,
        currency="CNY",
        created_at=created_at,
        updated_at=created_at,
    )
    order.items.append(
        OrderItem(
            product=product,
            product_name=product.name,
            quantity=quantity,
            unit_price=product.price,
        )
    )
    session.add(order)
    return order


def seed_extended(session):
    """Seed dev data plus deterministic agent-facing orders.

    ORD-1001 Alice DELIVERED Wireless Earbuds Pro x1 (1299, high value)
    ORD-1002 Alice PAID Cotton T-Shirt x1 (129, cancellable)
    ORD-1003 Alice DELIVERED LED Desk Lamp x1 (199, normal refund)
    ORD-2001 Bob PAID Wireless Earbuds Pro x1 (cross-user guard)
    """
    assert seed_dev_data(session) is True
    alice = session.scalar(select(User).where(User.email == "alice@example.com"))
    bob = session.scalar(select(User).where(User.email == "bob@example.com"))
    assert alice is not None and bob is not None
    products = {product.name: product for product in session.scalars(select(Product))}

    o1001 = _add_order(
        session, 1001, alice, OrderStatus.DELIVERED,
        products["Wireless Earbuds Pro"], 1,
        datetime(2026, 8, 20, 10, 0, tzinfo=UTC),
    )
    _add_order(
        session, 1002, alice, OrderStatus.PAID,
        products["Cotton T-Shirt"], 1,
        datetime(2026, 8, 21, 10, 0, tzinfo=UTC),
    )
    _add_order(
        session, 1003, alice, OrderStatus.DELIVERED,
        products["LED Desk Lamp"], 1,
        datetime(2026, 8, 22, 10, 0, tzinfo=UTC),
    )
    _add_order(
        session, 2001, bob, OrderStatus.PAID,
        products["Wireless Earbuds Pro"], 1,
        datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
    )
    session.add(
        Logistics(
            order=o1001,
            carrier="SF Express",
            tracking_number="SF10020099",
            status=LogisticsStatus.IN_TRANSIT,
            estimated_delivery=date(2026, 8, 26),
            updated_at=datetime(2026, 8, 23, 10, 0, tzinfo=UTC),
        )
    )
    session.commit()
    return session


def make_workflow(
    session,
    *,
    risk_engine=True,
    approval_gateway=True,
    verifier=True,
):
    """Build the Phase 5 AgentWorkflow over one shared session."""
    executor = ToolExecutor(build_default_registry(session))
    return AgentWorkflow(
        tool_executor=executor,
        risk_engine=RiskEngine() if risk_engine else None,
        approval_gateway=ApprovalService(session) if approval_gateway else None,
        verifier=BusinessVerifier(session) if verifier else None,
    )


def refunds_of(session, order_id):
    stmt = select(Refund).where(Refund.order_id == order_id).order_by(Refund.id)
    return list(session.scalars(stmt))


def approvals_of(session):
    stmt = select(ApprovalRequest).order_by(ApprovalRequest.id)
    return list(session.scalars(stmt))


def order_of(session, order_id):
    return session.get(Order, order_id)