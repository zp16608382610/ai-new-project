"""Deterministic development / mock seed data.

业务规则说明:
- 订单金额 total_amount 必须等于其明细的 sum(quantity * unit_price)。
- 可退款订单:DELIVERED 且订单内所有商品 returnable=True。
- 不可退款订单:包含 returnable=False 商品,或订单已 CANCELLED。
- 物流 EXCEPTION 订单用于模拟配送异常(可转投诉工单)。
"""
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Logistics, Order, OrderItem, Product, Refund, Ticket, User
from app.db.enums import (
    LogisticsStatus,
    OrderStatus,
    RefundStatus,
    TicketPriority,
    TicketStatus,
)

UTC = timezone.utc


def _dt(month: int, day: int, hour: int = 10) -> datetime:
    return datetime(2026, month, day, hour, 0, 0, tzinfo=UTC)


def _products() -> list[Product]:
    return [
        Product(name="iPhone 15 Pro", category="Electronics", price=Decimal("7999.00"), returnable=False, created_at=_dt(7, 1)),
        Product(name="Wireless Earbuds Pro", category="Electronics", price=Decimal("1299.00"), returnable=True, created_at=_dt(7, 1)),
        Product(name="Running Shoes Air", category="Sports", price=Decimal("699.00"), returnable=True, created_at=_dt(7, 1)),
        Product(name="Smart Watch S2", category="Electronics", price=Decimal("1899.00"), returnable=False, created_at=_dt(7, 1)),
        Product(name="Cotton T-Shirt", category="Apparel", price=Decimal("129.00"), returnable=True, created_at=_dt(7, 2)),
        Product(name="LED Desk Lamp", category="Home", price=Decimal("199.00"), returnable=True, created_at=_dt(7, 2)),
        Product(name="Vitamin C Tablets", category="Health", price=Decimal("89.00"), returnable=False, created_at=_dt(7, 2)),
        Product(name="Insulated Bottle 1L", category="Home", price=Decimal("159.00"), returnable=True, created_at=_dt(7, 2)),
    ]


def seed_dev_data(session: Session) -> bool:
    """Insert deterministic dev data once. Returns False if DB is already seeded."""
    existing = session.scalar(select(func.count()).select_from(User))
    if existing:
        return False

    users = [
        User(name="Alice Zhang", email="alice@example.com", created_at=_dt(7, 1)),
        User(name="Bob Li", email="bob@example.com", created_at=_dt(7, 1)),
        User(name="Carol Wang", email="carol@example.com", created_at=_dt(7, 2)),
    ]
    session.add_all(users)
    products = _products()
    session.add_all(products)

    p = {prod.name: prod for prod in products}
    iphone, earbuds, shoes, watch = p["iPhone 15 Pro"], p["Wireless Earbuds Pro"], p["Running Shoes Air"], p["Smart Watch S2"]
    tshirt, lamp, vitamins, bottle = p["Cotton T-Shirt"], p["LED Desk Lamp"], p["Vitamin C Tablets"], p["Insulated Bottle 1L"]

    orders: list[Order] = []

    def build(user: User, tag: str, status: OrderStatus, created: datetime, lines: list[tuple[Product, int]]):
        order = Order(user=user, status=status, total_amount=Decimal("0.00"), currency="CNY", created_at=created, updated_at=created)
        total = Decimal("0.00")
        for product, qty in lines:
            order.items.append(
                OrderItem(
                    product=product,
                    product_name=product.name,
                    quantity=qty,
                    unit_price=product.price,
                )
            )
            total += product.price * qty
        order.total_amount = total
        orders.append(order)
        return order

    # u1 = users[0], u2 = users[1], u3 = users[2]
    o1 = build(users[0], "u1-o1", OrderStatus.PENDING, _dt(8, 1), [(tshirt, 1)])               # 未支付
    o2 = build(users[0], "u1-o2", OrderStatus.PAID, _dt(8, 2), [(lamp, 2)])                      # 已支付,运输中
    o3 = build(users[0], "u1-o3", OrderStatus.SHIPPED, _dt(8, 5), [(earbuds, 1)])                # 已发货,派送中
    o4 = build(users[0], "u1-o4", OrderStatus.DELIVERED, _dt(8, 10), [(tshirt, 3), (bottle, 2)])  # 已签收,全可退 → 可退款
    o5 = build(users[1], "u2-o1", OrderStatus.PAID, _dt(8, 3), [(iphone, 1)])                     # 含不可退商品
    o6 = build(users[1], "u2-o2", OrderStatus.SHIPPED, _dt(8, 8), [(vitamins, 1)])               # 保健品不可退 + 物流异常
    o7 = build(users[1], "u2-o3", OrderStatus.DELIVERED, _dt(8, 12), [(watch, 1)])               # 不可退 → 不可退款
    o8 = build(users[2], "u3-o1", OrderStatus.CANCELLED, _dt(8, 4), [(shoes, 1)])                # 已取消
    o9 = build(users[2], "u3-o2", OrderStatus.REFUNDED, _dt(8, 6), [(earbuds, 1)])               # 已退款完成
    o10 = build(users[2], "u3-o3", OrderStatus.DELIVERED, _dt(8, 15), [(bottle, 1), (shoes, 1)])  # 已签收,全可退 → 可退款
    session.add_all(orders)

    logistics = [
        Logistics(order=o2, carrier="SF Express", tracking_number="SF10020001", status=LogisticsStatus.IN_TRANSIT, estimated_delivery=date(2026, 8, 8), updated_at=_dt(8, 3)),
        Logistics(order=o3, carrier="YTO Express", tracking_number="YT10020002", status=LogisticsStatus.OUT_FOR_DELIVERY, estimated_delivery=date(2026, 8, 7), updated_at=_dt(8, 6)),
        Logistics(order=o4, carrier="ZTO Express", tracking_number="ZT10020003", status=LogisticsStatus.DELIVERED, estimated_delivery=date(2026, 8, 12), updated_at=_dt(8, 11)),
        Logistics(order=o5, carrier="SF Express", tracking_number="SF10020004", status=LogisticsStatus.IN_TRANSIT, estimated_delivery=date(2026, 8, 9), updated_at=_dt(8, 4)),
        Logistics(order=o6, carrier="YTO Express", tracking_number="YT10020005", status=LogisticsStatus.EXCEPTION, estimated_delivery=date(2026, 8, 10), updated_at=_dt(8, 9)),
        Logistics(order=o9, carrier="ZTO Express", tracking_number="ZT10020006", status=LogisticsStatus.DELIVERED, estimated_delivery=date(2026, 8, 8), updated_at=_dt(8, 7)),
    ]
    session.add_all(logistics)

    refunds = [
        Refund(order=o4, user=users[0], amount=Decimal("705.00"), status=RefundStatus.PENDING, reason="change_of_mind", created_at=_dt(8, 12), updated_at=_dt(8, 12)),
        Refund(order=o9, user=users[2], amount=Decimal("1299.00"), status=RefundStatus.COMPLETED, reason="fits_poorly", created_at=_dt(8, 8), updated_at=_dt(8, 14)),
        Refund(order=o7, user=users[1], amount=Decimal("1899.00"), status=RefundStatus.REJECTED, reason="not_returnable_product", created_at=_dt(8, 13), updated_at=_dt(8, 15)),
    ]
    session.add_all(refunds)

    tickets = [
        Ticket(user=users[1], order=o6, category="LOGISTICS", priority=TicketPriority.URGENT, status=TicketStatus.OPEN, description="包裹在运输中出现异常,多日无更新,要求尽快处理", created_at=_dt(8, 10), updated_at=_dt(8, 10)),
        Ticket(user=users[0], order=o4, category="REFUND", priority=TicketPriority.MEDIUM, status=TicketStatus.PROCESSING, description="发起部分退款,等待审核", created_at=_dt(8, 13), updated_at=_dt(8, 13)),
        Ticket(user=users[1], order=o5, category="PRODUCT_QUALITY", priority=TicketPriority.HIGH, status=TicketStatus.RESOLVED, description="到货商品外壳有划痕,已补偿处理", created_at=_dt(8, 11), updated_at=_dt(8, 16)),
    ]
    session.add_all(tickets)
    session.flush()
    return True