from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Float, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MediaExtraction(Base):
    __tablename__ = "media_extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    task_type: Mapped[str] = mapped_column(String(60), index=True)
    provider: Mapped[str] = mapped_column(String(120))
    ocr_engine: Mapped[str] = mapped_column(String(120))
    structuring_model: Mapped[str] = mapped_column(String(120))
    schema_version: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    raw_ocr_text: Mapped[str] = mapped_column(Text)
    ocr_lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    structured_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    processing_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    media_asset: Mapped["MediaAsset"] = relationship(back_populates="extractions")  # noqa: F821
