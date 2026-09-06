"""knowledge_documents and knowledge_chunks tables (Phase 3A).

设计说明:
- 版本唯一键:(category, title, version)。历史版本永不覆盖,靠新 version 演进。
- status 生命周期:DRAFT → ACTIVE → ARCHIVED;仅 ACTIVE 是未来检索候选。
- chunk 通过 document_id 关联回来源文档,并冗余 snapshot metadata_json,
  保证 Document → Version → Chunk → Source 的完整可溯源,同时为未来
  「引用/过滤」做好准备(不在此阶段实现检索)。
- checksum = sha256(标准化全文),用于幂等入库判断。
- 本阶段不引入 pgvector / embedding 列;向量字段留给 Phase 3B(见 DECISIONS)。
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import Enum as SAEnum

from app.db.base import Base
from app.db.enums import KnowledgeCategory, KnowledgeStatus


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[KnowledgeCategory] = mapped_column(
        SAEnum(
            KnowledgeCategory,
            name="knowledge_category",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        nullable=False,
        index=True,
    )
    version: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[KnowledgeStatus] = mapped_column(
        SAEnum(
            KnowledgeStatus,
            name="knowledge_status",
            native_enum=False,
            create_constraint=True,
            validate_strings=True,
            length=20,
        ),
        default=KnowledgeStatus.DRAFT,
        nullable=False,
        index=True,
    )
    source: Mapped[str] = mapped_column(String(100), nullable=False, server_default="internal/mock")
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False, server_default="zh-CN")
    checksum: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    chunks: Mapped[list["KnowledgeChunk"]] = relationship(
        "KnowledgeChunk",
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="KnowledgeChunk.chunk_index",
    )

    __table_args__ = (
        UniqueConstraint("category", "title", "version", name="uq_knowledge_doc_version"),
    )


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    metadata_json: Mapped[dict[str, Any] | None] = mapped_column(
        "metadata", JSON, nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped[KnowledgeDocument] = relationship(
        "KnowledgeDocument", back_populates="chunks"
    )

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_knowledge_chunk_index"),
    )