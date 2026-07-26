from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class OCRProfile(Base):
    __tablename__ = "ocr_profiles"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    source_test_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("ocr_test_runs.id", ondelete="SET NULL", use_alter=True),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class OCRTestRun(Base):
    __tablename__ = "ocr_test_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    media_asset_id: Mapped[int] = mapped_column(
        ForeignKey("media_assets.id", ondelete="CASCADE"), index=True
    )
    profile_name: Mapped[str] = mapped_column(String(120))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="completed", index=True)
    raw_text: Mapped[str] = mapped_column(Text)
    lines: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float)
    source_width: Mapped[int] = mapped_column()
    source_height: Mapped[int] = mapped_column()
    processed_width: Mapped[int] = mapped_column()
    processed_height: Mapped[int] = mapped_column()
    overlay_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    table_overlay_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    table_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    structure_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    engine: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
