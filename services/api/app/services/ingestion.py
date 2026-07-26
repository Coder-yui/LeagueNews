from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.connectors.base import RawItemCandidate
from app.content_blocks import (
    content_hash as hash_content_blocks,
    normalize_content_blocks,
)
from app.models.media_asset import MediaAsset
from app.models.raw_item import RawItem
from app.models.raw_item_source_payload import RawItemSourcePayload
from app.models.source import Source
from app.services.media_storage import MediaStorage


@dataclass(slots=True)
class IngestionResult:
    created: list[RawItem] = field(default_factory=list)
    revised: list[RawItem] = field(default_factory=list)
    skipped: list[RawItem] = field(default_factory=list)


class MediaStorageProtocol(Protocol):
    async def materialize_blocks(
        self, blocks: list[dict[str, object]], *, namespace: str
    ) -> tuple[list[dict[str, object]], list[Path]]: ...

    def remove_files(self, paths: list[Path]) -> None: ...


async def ingest_connector_items(
    db: Session,
    *,
    source: Source,
    items: list[RawItemCandidate],
    media_storage: MediaStorageProtocol | None = None,
) -> IngestionResult:
    """Persist canonical connector items through one source-independent path."""
    storage = media_storage or MediaStorage()
    result = IngestionResult()
    created_files: list[Path] = []

    try:
        for item in items:
            if source.connector_type != "manual" and not item.external_id:
                raise ValueError(
                    f"{source.connector_type} candidate has no external_id"
                )
            blocks = normalize_content_blocks(item.content_blocks)
            if not blocks:
                raise ValueError("connector item has no content blocks")
            content_hash = hash_content_blocks(blocks)

            existing, latest_revision = _find_existing(
                db,
                source_id=source.id,
                external_id=item.external_id,
                content_hash=content_hash,
            )
            if existing:
                result.skipped.append(existing)
                continue

            stored_blocks, new_files = await storage.materialize_blocks(
                blocks, namespace=source.connector_type
            )
            stored_blocks = normalize_content_blocks(stored_blocks)
            created_files.extend(new_files)
            raw_item = RawItem(
                source_id=source.id,
                external_id=item.external_id,
                native_title=item.native_title,
                canonical_url=item.canonical_url,
                content_kind=item.content_kind,
                author_name=item.author_name,
                language=item.language,
                content_blocks=stored_blocks,
                content_hash=content_hash,
                content_hash_version=2,
                revision=(latest_revision.revision + 1 if latest_revision else 1),
                supersedes_raw_item_id=latest_revision.id if latest_revision else None,
                published_at=item.published_at,
            )
            db.add(raw_item)
            db.flush()
            db.add(
                RawItemSourcePayload(
                    raw_item_id=raw_item.id,
                    provider=source.connector_type,
                    payload=item.provenance,
                )
            )
            for block_index, block in enumerate(stored_blocks):
                if block.get("type") != "image":
                    continue
                db.add(
                    MediaAsset(
                        raw_item_id=raw_item.id,
                        block_index=block_index,
                        source_url=block.get("source_url"),
                        storage_path=block.get("storage_path"),
                        mime_type=block.get("mime_type"),
                        alt_text=block.get("alt_text"),
                        caption=block.get("caption"),
                    )
                )
            result.created.append(raw_item)
            if latest_revision:
                result.revised.append(raw_item)
        db.commit()
    except BaseException:
        db.rollback()
        storage.remove_files(created_files)
        raise

    for raw_item in result.created:
        db.refresh(raw_item)
    return result


def _find_existing(
    db: Session,
    *,
    source_id: int,
    external_id: str | None,
    content_hash: str,
) -> tuple[RawItem | None, RawItem | None]:
    if external_id:
        revisions = list(
            db.scalars(
                select(RawItem).where(
                    RawItem.source_id == source_id,
                    RawItem.external_id == external_id,
                ).order_by(RawItem.revision.desc(), RawItem.id.desc())
            )
        )
        for existing in revisions:
            if hash_content_blocks(existing.content_blocks) == content_hash:
                return existing, revisions[0]
        return None, revisions[0] if revisions else None

    candidates = list(
        db.scalars(
            select(RawItem).where(
                RawItem.source_id == source_id,
                RawItem.external_id.is_(None),
            )
        )
    )
    for existing in candidates:
        if hash_content_blocks(existing.content_blocks) == content_hash:
            return existing, existing
    return None, None
