from datetime import datetime

from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, Index, JSON, String, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    connector_type: Mapped[str] = mapped_column(String(60), default="manual", index=True)
    external_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    connector_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_official: Mapped[bool] = mapped_column(Boolean, default=False)
    reliability_score: Mapped[float] = mapped_column(Float, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    raw_items: Mapped[list["RawItem"]] = relationship(back_populates="source")  # noqa: F821
    collection_schedule: Mapped["SourceCollectionSchedule | None"] = relationship(  # noqa: F821
        back_populates="source",
        cascade="all, delete-orphan",
        uselist=False,
    )

    __table_args__ = (
        Index(
            "uq_sources_connector_external_key",
            "connector_type",
            "external_key",
            unique=True,
            postgresql_where=text("external_key IS NOT NULL"),
        ),
        CheckConstraint(
            "reliability_score >= 0 AND reliability_score <= 1",
            name="ck_sources_reliability_score",
        ),
    )
