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


class AfterSalesCaseType(str, enum.Enum):
    """Root-cause category of an after-sales case (Phase 9A)."""

    QUALITY_ISSUE = "QUALITY_ISSUE"
    LOGISTICS_DISPUTE = "LOGISTICS_DISPUTE"
    OTHER = "OTHER"


class AfterSalesRequestedAction(str, enum.Enum):
    """What the customer asks the after-sales flow to do (Phase 9A)."""

    REFUND = "REFUND"
    EXCHANGE = "EXCHANGE"
    REPAIR = "REPAIR"
    UNKNOWN = "UNKNOWN"


class AfterSalesCaseStatus(str, enum.Enum):
    """Lifecycle of one after-sales case (Phase 9A).

    INFORMATION_COLLECTION -> ELIGIBILITY_CHECK -> PROCESSING -> COMPLETED
    PENDING_HUMAN is the hand-off state; REJECTED is a terminal "not allowed".
    """

    INFORMATION_COLLECTION = "INFORMATION_COLLECTION"
    ELIGIBILITY_CHECK = "ELIGIBILITY_CHECK"
    PROCESSING = "PROCESSING"
    PENDING_HUMAN = "PENDING_HUMAN"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
