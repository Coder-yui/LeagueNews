from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="CASCADE"), index=True
    )
    block_index: Mapped[int] = mapped_column(Integer)
    source_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    storage_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    public_path: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    visibility: Mapped[str] = mapped_column(String(30), default="private", index=True)
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    mime_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sha256: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alt_text: Mapped[str | None] = mapped_column(String(500), nullable=True)
    caption: Mapped[str | None] = mapped_column(Text, nullable=True)
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    raw_item: Mapped["RawItem"] = relationship(back_populates="media_assets")  # noqa: F821
    extractions: Mapped[list["MediaExtraction"]] = relationship(  # noqa: F821
        back_populates="media_asset", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint(
            "visibility IN ('private', 'published')",
            name="ck_media_assets_visibility",
        ),
    )
