from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class EventItem(Base):
    __tablename__ = "event_items"

    event_id: Mapped[int] = mapped_column(
        ForeignKey("news_events.id", ondelete="CASCADE"), primary_key=True
    )
    normalized_item_id: Mapped[int] = mapped_column(
        ForeignKey("normalized_items.id", ondelete="CASCADE"), primary_key=True
    )
    relation_type: Mapped[str] = mapped_column(String(30), default="primary")
    is_primary: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["NewsEvent"] = relationship(back_populates="items")  # noqa: F821
    normalized_item: Mapped["NormalizedItem"] = relationship(back_populates="event_links")  # noqa: F821

