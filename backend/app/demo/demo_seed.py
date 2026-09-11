"""Phase 7A demo dataset (local SQLite demo only).

Reuses the deterministic dev seed (backend/app/db/seed.py) and adds a small,
stable set of agent-facing orders with ids >= 1000 so the deterministic entity
extractor (ORD-<3+ digits>) and the Phase 5/6 test vocabulary match:

    ORD-1001 Alice DELIVERED  Wireless Earbuds Pro x1 (1299 -> CRITICAL refund)
    ORD-1002 Alice PAID       Cotton T-Shirt x1        (129  -> cancellable)
    ORD-1003 Alice DELIVERED  LED Desk Lamp x1         (199  -> normal refund)
    ORD-2001 Bob   PAID       Wireless Earbuds Pro x1  (cross-user guard)

Demo orders belong to the users created by seed_dev_data (Alice id 1 / Bob id 2).
This module is used by backend/app/demo/bootstrap.py and the demo API tests; it
is NOT part of the production migration path.

Timestamps are anchored to a single injectable "now" (the real clock by
default) instead of fixed calendar dates, so the live demo still sits inside
the 15-day after-sales window whenever it is run. Tests and the evaluation
runner pass a fixed anchor for determinism.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.enums import LogisticsStatus, OrderStatus
from app.db.models import Logistics, Order, OrderItem, Product, User
from app.db.seed import seed_dev_data
from app.knowledge.seed import seed_knowledge

UTC = timezone.utc


def _reference_now() -> datetime:
    """Reference "now" for the demo dataset (the wall clock by default)."""
    return datetime.now(UTC)


def demo_orders_present(session: Session) -> bool:
    """True when the agent-facing demo orders already exist in this database."""
    return session.get(Order, 1001) is not None


def seed_demo_orders(session: Session, *, now: datetime | None = None) -> bool:
    """Insert the agent-facing demo orders (idempotent). Requires dev seed.

    ``now`` anchors every demo timestamp; when omitted the real clock is used
    so the live demo orders stay inside the current after-sales window.
    """
    if demo_orders_present(session):
        return False
    anchor = now or _reference_now()
    alice = session.scalar(select(User).where(User.email == "alice@example.com"))
    bob = session.scalar(select(User).where(User.email == "bob@example.com"))
    if alice is None or bob is None:
        raise RuntimeError("Demo seed requires the dev seed users (Alice / Bob).")
    products = {product.name: product for product in session.scalars(select(Product))}

    def _add(order_id: int, user, status: OrderStatus, name: str, quantity: int, created) -> Order:
        product = products[name]
        order = Order(
            id=order_id,
            user=user,
            status=status,
            total_amount=product.price * quantity,
            currency="CNY",
            created_at=created,
            updated_at=created,
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

    o1001 = _add(
        1001, alice, OrderStatus.DELIVERED, "Wireless Earbuds Pro", 1,
        anchor - timedelta(days=4),
    )
    _add(
        1002, alice, OrderStatus.PAID, "Cotton T-Shirt", 1,
        anchor - timedelta(days=3),
    )
    _add(
        1003, alice, OrderStatus.DELIVERED, "LED Desk Lamp", 1,
        anchor - timedelta(days=2),
    )
    _add(
        2001, bob, OrderStatus.PAID, "Wireless Earbuds Pro", 1,
        anchor - timedelta(days=1),
    )
    session.add(
        Logistics(
            order=o1001,
            carrier="SF Express",
            tracking_number="SF10020099",
            status=LogisticsStatus.IN_TRANSIT,
            estimated_delivery=(anchor - timedelta(days=1)).date(),
            updated_at=anchor - timedelta(days=3),
        )
    )
    session.commit()
    return True


def prepare_demo_database(database_url: str, *, now: datetime | None = None) -> None:
    """Create + seed a local demo SQLite database (schema + dev + demo + KB).

    Fast, offline and deterministic - intended for the interview demo and the
    demo API tests. Uses Base.metadata directly (no Alembic run) because this
    is a scratch local file, never a shared/production database.
    """
    from app.db.base import Base
    from app.db.session import create_db_engine, create_session_factory

    engine = create_db_engine(database_url)
    Base.metadata.create_all(engine)
    session = create_session_factory(engine)()
    try:
        seed_dev_data(session)
        seed_demo_orders(session, now=now)
        seed_knowledge(session)
        session.commit()
    finally:
        session.close()
        engine.dispose()
