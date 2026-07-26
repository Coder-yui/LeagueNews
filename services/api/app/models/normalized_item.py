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
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    target_language: Mapped[str] = mapped_column(String(30), default="zh-CN")
    translated_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    translated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    translated_content_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    approved_media_extraction_ids: Mapped[list[int]] = mapped_column(JSON, default=list)
    translation_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    translation_model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    analysis_model: Mapped[str] = mapped_column(String(120))
    analysis_version: Mapped[str] = mapped_column(String(30), default="v2")
    event_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    raw_item: Mapped["RawItem"] = relationship(back_populates="normalized_item")  # noqa: F821
    event_links: Mapped[list["EventItem"]] = relationship(  # noqa: F821
        back_populates="normalized_item", cascade="all, delete-orphan"
    )
