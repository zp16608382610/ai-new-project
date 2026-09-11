"""Service layer: business rules and state transitions (framework-independent).

调用链:HTTP API / Future Tool → Service → Repository → Database。
Service 不依赖 FastAPI;领域异常见 errors.py,由 API 层统一映射。
"""
from app.services.errors import (
    BusinessError,
    ConflictError,
    InvalidOperationError,
    NotFoundError,
)
from app.services.after_sales_service import AfterSalesService
from app.services.order_service import OrderService
from app.services.refund_service import RefundService
from app.services.ticket_service import TicketService

__all__ = [
    "AfterSalesService",
    "BusinessError",
    "ConflictError",
    "InvalidOperationError",
    "NotFoundError",
    "OrderService",
    "RefundService",
    "TicketService",
]
