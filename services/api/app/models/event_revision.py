from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class EventRevision(Base):
    __tablename__ = "event_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("news_events.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column()
    change_type: Mapped[str] = mapped_column(String(40))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_review_id: Mapped[int | None] = mapped_column(
        ForeignKey("review_tasks.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    event: Mapped["NewsEvent"] = relationship(back_populates="revisions")  # noqa: F821
