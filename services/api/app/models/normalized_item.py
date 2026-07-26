from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text, func
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
    category: Mapped[str] = mapped_column(String(60), index=True)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    importance_score: Mapped[float] = mapped_column(Float, index=True)
    credibility: Mapped[str] = mapped_column(String(30), index=True)
    credibility_score: Mapped[float] = mapped_column(Float, index=True)
    credibility_evidence: Mapped[list[str]] = mapped_column(JSON, default=list)
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    target_language: Mapped[str] = mapped_column(String(30), default="zh-CN")
    translated_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    translated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    translated_content_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    translation_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    translation_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    analysis_model: Mapped[str] = mapped_column(String(120))
    analysis_version: Mapped[str] = mapped_column(String(30), default="v2")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    raw_item: Mapped["RawItem"] = relationship(back_populates="normalized_item")  # noqa: F821
    media_links: Mapped[list["NormalizedItemMediaExtraction"]] = relationship(
        back_populates="normalized_item", cascade="all, delete-orphan"
    )
    event_membership: Mapped["EventMessage | None"] = relationship(  # noqa: F821
        back_populates="normalized_item", uselist=False
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    normalized_item: Mapped[NormalizedItem] = relationship(back_populates="media_links")
    media_extraction: Mapped["MediaExtraction"] = relationship()  # noqa: F821
