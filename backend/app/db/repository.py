"""Repository layer.

调用方向(后续 Agent/Tool 阶段生效):Agent → Tool → Service → Repository → Database。
数据库访问只允许经过 Repository,业务代码不直接写 SQL。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.db.enums import KnowledgeCategory, KnowledgeStatus, OrderStatus, RefundStatus
from app.db.models import (
    KnowledgeChunk,
    KnowledgeDocument,
    Logistics,
    Order,
    OrderItem,
    Product,
    Refund,
    Ticket,
    User,
)


class BaseRepository:
    """Minimal generic repository around a single ORM model."""

    model: type

    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        """Expose the bound session for service-level commits."""
        return self._session

    def get(self, entity_id: int):
        return self._session.get(self.model, entity_id)

    def list(self, *, limit: int = 100, offset: int = 0):
        stmt = select(self.model).order_by(self.model.id).limit(limit).offset(offset)
        return list(self._session.scalars(stmt))

    def create(self, **values):
        entity = self.model(**values)
        self._session.add(entity)
        self._session.flush()
        return entity


class UserRepository(BaseRepository):
    model = User

    def get_by_email(self, email: str):
        stmt = select(User).where(User.email == email)
        return self._session.scalars(stmt).first()


class ProductRepository(BaseRepository):
    model = Product


class OrderRepository(BaseRepository):
    model = Order

    def get_full(self, order_id: int) -> Order | None:
        """Load an order aggregate (user, items) in one query."""
        stmt = (
            select(Order)
            .where(Order.id == order_id)
            .options(
                selectinload(Order.user),
                selectinload(Order.items),
            )
        )
        return self._session.scalars(stmt).first()

    def list_by_user(self, user_id: int, status: OrderStatus | None = None):
        stmt = select(Order).where(Order.user_id == user_id)
        if status is not None:
            stmt = stmt.where(Order.status == status)
        stmt = stmt.options(selectinload(Order.items)).order_by(Order.id)
        return list(self._session.scalars(stmt))


class OrderItemRepository(BaseRepository):
    model = OrderItem


class LogisticsRepository(BaseRepository):
    model = Logistics

    def list_by_order(self, order_id: int):
        stmt = select(Logistics).where(Logistics.order_id == order_id).order_by(Logistics.id)
        return list(self._session.scalars(stmt))

    def get_latest_by_order(self, order_id: int) -> Logistics | None:
        stmt = (
            select(Logistics)
            .where(Logistics.order_id == order_id)
            .order_by(Logistics.id.desc())
            .limit(1)
        )
        return self._session.scalars(stmt).first()


class RefundRepository(BaseRepository):
    model = Refund

    def list_by_order(self, order_id: int):
        stmt = select(Refund).where(Refund.order_id == order_id).order_by(Refund.id)
        return list(self._session.scalars(stmt))

    def list_active_by_order(self, order_id: int):
        """Refunds still in flight (PENDING / APPROVED) for an order."""
        stmt = (
            select(Refund)
            .where(Refund.order_id == order_id)
            .where(Refund.status.in_([RefundStatus.PENDING, RefundStatus.APPROVED]))
            .order_by(Refund.id)
        )
        return list(self._session.scalars(stmt))

    def has_completed(self, order_id: int) -> bool:
        stmt = (
            select(Refund.id)
            .where(Refund.order_id == order_id)
            .where(Refund.status == RefundStatus.COMPLETED)
            .limit(1)
        )
        return self._session.scalar(stmt) is not None


class TicketRepository(BaseRepository):
    model = Ticket

    def list_by_user(self, user_id: int):
        stmt = select(Ticket).where(Ticket.user_id == user_id).order_by(Ticket.id)
        return list(self._session.scalars(stmt))


class KnowledgeDocumentRepository(BaseRepository):
    """Repository for knowledge documents (single database boundary)."""

    model = KnowledgeDocument

    def get_by_natural_key(
        self, category: KnowledgeCategory, title: str, version: str
    ) -> KnowledgeDocument | None:
        stmt = (
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.category == category,
                KnowledgeDocument.title == title,
                KnowledgeDocument.version == version,
            )
            .order_by(KnowledgeDocument.id.desc())
        )
        return self._session.scalars(stmt).first()

    def list_by_category(
        self, category: KnowledgeCategory | None = None
    ) -> list[KnowledgeDocument]:
        stmt = select(KnowledgeDocument).order_by(
            KnowledgeDocument.category, KnowledgeDocument.title, KnowledgeDocument.id
        )
        if category is not None:
            stmt = stmt.where(KnowledgeDocument.category == category)
        return list(self._session.scalars(stmt))

    def list_active(
        self, category: KnowledgeCategory | None = None
    ) -> list[KnowledgeDocument]:
        """Only ACTIVE documents are valid future retrieval candidates."""
        stmt = (
            select(KnowledgeDocument)
            .where(KnowledgeDocument.status == KnowledgeStatus.ACTIVE)
            .order_by(KnowledgeDocument.category, KnowledgeDocument.title, KnowledgeDocument.id)
        )
        if category is not None:
            stmt = stmt.where(KnowledgeDocument.category == category)
        return list(self._session.scalars(stmt))


class KnowledgeChunkRepository(BaseRepository):
    """Repository for knowledge chunks."""

    model = KnowledgeChunk

    def list_by_document(self, document_id: int) -> list[KnowledgeChunk]:
        stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id)
            .order_by(KnowledgeChunk.chunk_index)
        )
        return list(self._session.scalars(stmt))
    def list_retrieval_candidates(
        self,
        *,
        status: KnowledgeStatus | None = KnowledgeStatus.ACTIVE,
        category: KnowledgeCategory | None = None,
        language: str | None = None,
    ) -> list[KnowledgeChunk]:
        """Candidate chunks joined with their document for the retrieval layer.

        Default status=ACTIVE enforces the Phase 3A lifecycle rule: DRAFT /
        ARCHIVED documents are not retrieval candidates unless explicitly
        overridden (internal/testing use only).
        """
        stmt = (
            select(KnowledgeChunk)
            .join(KnowledgeDocument, KnowledgeChunk.document_id == KnowledgeDocument.id)
            .options(selectinload(KnowledgeChunk.document))
        )
        if status is not None:
            stmt = stmt.where(KnowledgeDocument.status == status)
        if category is not None:
            stmt = stmt.where(KnowledgeDocument.category == category)
        if language is not None:
            stmt = stmt.where(KnowledgeDocument.language == language)
        stmt = stmt.order_by(KnowledgeDocument.id, KnowledgeChunk.chunk_index)
        return list(self._session.scalars(stmt))