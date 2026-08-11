from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class NormalizedItem(Base):
    __tablename__ = "normalized_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="CASCADE"), unique=True, index=True
    )
    normalized_title: Mapped[str] = mapped_column(String(500))
    normalized_text: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    products: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["unknown"])
    message_type: Mapped[str] = mapped_column(String(80), default="unknown", index=True)
    topics: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["unknown"])
    classification_version: Mapped[str] = mapped_column(String(40), default="message-taxonomy-v3")
    content_form: Mapped[str] = mapped_column(String(30), default="original")
    facets: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    importance_score: Mapped[float] = mapped_column(Float, index=True)
    importance_dimensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    importance_policy_version: Mapped[str] = mapped_column(
        String(80), default="importance-v11-repost-weekly-rotation"
    )
    importance_calculation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    priority_score: Mapped[float] = mapped_column(Float, default=0.5, index=True)
    priority_calculation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    target_language: Mapped[str] = mapped_column(String(30), default="zh-CN")
    translated_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    translated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    translated_content_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    translation_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    translation_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    analysis_model: Mapped[str] = mapped_column(String(120))
    analysis_version: Mapped[str] = mapped_column(String(30), default="message-processing-v1.1")
    current_revision: Mapped[int] = mapped_column(Integer, default=1)
    publication_status: Mapped[str] = mapped_column(String(30), default="published", index=True)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    withdrawal_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    raw_item: Mapped["RawItem"] = relationship(back_populates="normalized_item")  # noqa: F821
    media_links: Mapped[list["NormalizedItemMediaExtraction"]] = relationship(
        back_populates="normalized_item", cascade="all, delete-orphan"
    )
    revisions: Mapped[list["NormalizedItemRevision"]] = relationship(
        back_populates="normalized_item",
        cascade="all, delete-orphan",
        order_by="NormalizedItemRevision.revision",
    )

    __table_args__ = (
        CheckConstraint(
            "priority_score >= 0 AND priority_score <= 1",
            name="ck_normalized_items_priority_score",
        ),
        CheckConstraint(
            "current_revision >= 1",
            name="ck_normalized_items_current_revision_positive",
        ),
        CheckConstraint(
            "publication_status IN ('published', 'withdrawn', 'superseded')",
            name="ck_normalized_items_publication_status",
        ),
    )

    @property
    def approved_media_extraction_ids(self) -> list[int]:
        return [link.media_extraction_id for link in self.media_links]

    @property
    def translated_media_extractions(self) -> list[dict[str, Any]]:
        return [
            {
                "extraction_id": link.media_extraction_id,
                "translated_data": link.translated_structured_data,
                "translation_status": link.translation_status,
                "translation_model": link.translation_model,
            }
            for link in self.media_links
        ]


class NormalizedItemMediaExtraction(Base):
    __tablename__ = "normalized_item_media_extractions"

    normalized_item_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="CASCADE"), primary_key=True
    )
    media_extraction_id: Mapped[int] = mapped_column(
        ForeignKey("media_extractions.id", ondelete="RESTRICT"), primary_key=True
    )
    translated_structured_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    translation_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    translation_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    normalized_item: Mapped[NormalizedItem] = relationship(back_populates="media_links")
    media_extraction: Mapped["MediaExtraction"] = relationship()  # noqa: F821


class NormalizedItemRevision(Base):
    __tablename__ = "normalized_item_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    normalized_item_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    processing_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("processing_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    change_note: Mapped[str] = mapped_column(Text, default="published")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    normalized_item: Mapped[NormalizedItem] = relationship(back_populates="revisions")

    __table_args__ = (
        UniqueConstraint(
            "normalized_item_id",
            "revision",
            name="uq_normalized_item_revisions_item_revision",
        ),
        CheckConstraint(
            "revision >= 1",
            name="ck_normalized_item_revisions_revision_positive",
        ),
    )
