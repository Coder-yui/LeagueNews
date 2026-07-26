from datetime import datetime

from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, JSON, String, event, func, inspect, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class RawItem(Base):
    __tablename__ = "raw_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    native_title: Mapped[str | None] = mapped_column(String(500), nullable=True)
    canonical_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    content_kind: Mapped[str] = mapped_column(String(30), default="post")
    author_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(30), nullable=True)
    content_blocks: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    content_hash_version: Mapped[int] = mapped_column(Integer, default=2)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_raw_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("raw_items.id", ondelete="SET NULL"), nullable=True, index=True
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    source: Mapped["Source"] = relationship(back_populates="raw_items")  # noqa: F821
    media_assets: Mapped[list["MediaAsset"]] = relationship(  # noqa: F821
        back_populates="raw_item", cascade="all, delete-orphan"
    )
    normalized_item: Mapped["NormalizedItem | None"] = relationship(  # noqa: F821
        back_populates="raw_item", cascade="all, delete-orphan", uselist=False
    )
    source_payload: Mapped["RawItemSourcePayload | None"] = relationship(  # noqa: F821
        back_populates="raw_item", cascade="all, delete-orphan", uselist=False
    )
    processing_runs: Mapped[list["ProcessingRun"]] = relationship(  # noqa: F821
        back_populates="raw_item", cascade="all, delete-orphan"
    )
    supersedes: Mapped["RawItem | None"] = relationship(  # noqa: F821
        remote_side=[id], foreign_keys=[supersedes_raw_item_id]
    )

    @property
    def display_title(self) -> str | None:
        from app.content_blocks import display_title

        return display_title(
            native_title=self.native_title,
            author_name=self.author_name,
            source_name=self.source.name if self.source else None,
            blocks=self.content_blocks,
        )

    @property
    def processing_status(self) -> str:
        if self.normalized_item:
            return "analyzed"
        if not self.processing_runs:
            return "pending"
        latest = max(self.processing_runs, key=lambda run: run.id)
        return latest.status

    __table_args__ = (
        Index(
            "uq_raw_items_source_external_hash",
            "source_id",
            "external_id",
            "content_hash",
            unique=True,
            postgresql_where=text("external_id IS NOT NULL"),
        ),
        Index(
            "uq_raw_items_source_hash_without_external",
            "source_id",
            "content_hash",
            unique=True,
            postgresql_where=text("external_id IS NULL"),
        ),
    )


_IMMUTABLE_CONTENT_FIELDS = (
    "source_id",
    "external_id",
    "native_title",
    "canonical_url",
    "content_kind",
    "author_name",
    "language",
    "content_blocks",
    "content_hash",
    "content_hash_version",
    "revision",
    "supersedes_raw_item_id",
    "published_at",
    "ingested_at",
)


@event.listens_for(RawItem, "before_update")
def _prevent_raw_content_updates(_mapper: object, _connection: object, item: RawItem) -> None:
    state = inspect(item)
    changed = [
        field for field in _IMMUTABLE_CONTENT_FIELDS if state.attrs[field].history.has_changes()
    ]
    if changed:
        raise ValueError(
            "RawItem content is immutable; use an explicit data migration to correct: "
            + ", ".join(changed)
        )
