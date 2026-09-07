"""Status / priority enums stored in the database as constrained values."""
import enum


class OrderStatus(str, enum.Enum):
    PENDING = "PENDING"
    PAID = "PAID"
    SHIPPED = "SHIPPED"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    REFUNDED = "REFUNDED"


class LogisticsStatus(str, enum.Enum):
    PENDING = "PENDING"
    IN_TRANSIT = "IN_TRANSIT"
    OUT_FOR_DELIVERY = "OUT_FOR_DELIVERY"
    DELIVERED = "DELIVERED"
    EXCEPTION = "EXCEPTION"


class RefundStatus(str, enum.Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"


class TicketStatus(str, enum.Enum):
    OPEN = "OPEN"
    PROCESSING = "PROCESSING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"


class TicketPriority(str, enum.Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    URGENT = "URGENT"


class KnowledgeCategory(str, enum.Enum):
    """Knowledge document categories (customer-service policy domains)."""

    REFUND = "REFUND"
    RETURN = "RETURN"
    EXCHANGE = "EXCHANGE"
    LOGISTICS = "LOGISTICS"
    COUPON = "COUPON"
    SOP = "SOP"


class KnowledgeStatus(str, enum.Enum):
    """Knowledge document lifecycle state."""

    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


class ApprovalStatus(str, enum.Enum):
    """Approval request lifecycle for human-in-the-loop operations."""

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
