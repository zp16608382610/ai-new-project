"""ORM models. Import this package so every model is registered on Base.metadata."""
from app.db.base import Base
from app.db.models.user import User
from app.db.models.product import Product
from app.db.models.order import Order, OrderItem
from app.db.models.logistics import Logistics
from app.db.models.refund import Refund
from app.db.models.approval import ApprovalRequest
from app.db.models.ticket import Ticket
from app.db.models.knowledge import KnowledgeChunk, KnowledgeDocument
from app.db.models.after_sales import AfterSalesCase

__all__ = [
    "Base",
    "User",
    "Product",
    "Order",
    "OrderItem",
    "Logistics",
    "Refund",
    "ApprovalRequest",
    "Ticket",
    "KnowledgeChunk",
    "KnowledgeDocument",
    "AfterSalesCase",
]
