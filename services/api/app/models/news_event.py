from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class NewsEvent(Base):
    __tablename__ = "news_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    summary: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(60), index=True)
    entities: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    importance_score: Mapped[float] = mapped_column(Float, default=0.5, index=True)
    credibility: Mapped[str] = mapped_column(String(30), default="unverified", index=True)
    event_type: Mapped[str] = mapped_column(String(60), default="other", index=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    primary_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="SET NULL"), nullable=True
    )
    first_published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    items: Mapped[list["EventItem"]] = relationship(  # noqa: F821
        back_populates="event", cascade="all, delete-orphan"
    )
    revisions: Mapped[list["EventRevision"]] = relationship(  # noqa: F821
        back_populates="event", cascade="all, delete-orphan"
    )
