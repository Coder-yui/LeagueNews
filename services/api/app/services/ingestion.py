from dataclasses import dataclass, field

from sqlalchemy import select, text
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
from app.services.media_repair import (
    MediaStorageProtocol,
    repair_raw_item_media,
    storage_digest,
)
from app.services.media_storage import MediaStorage
from app.services.pipeline_queue import enqueue_pipeline_job
from app.services.raw_item_revision_lifecycle import (
    supersede_previous_raw_revision,
)


@dataclass(slots=True)
class IngestionResult:
    created: list[RawItem] = field(default_factory=list)
    revised: list[RawItem] = field(default_factory=list)
    skipped: list[RawItem] = field(default_factory=list)


async def ingest_connector_items(
    db: Session,
    *,
    source: Source,
    items: list[RawItemCandidate],
    media_storage: MediaStorageProtocol | None = None,
    enqueue_downstream: bool = True,
) -> IngestionResult:
    """Persist canonical connector items through one source-independent path."""
    storage = media_storage or MediaStorage()
    result = IngestionResult()
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
            _lock_ingestion_identity(
                db,
                source_id=source.id,
                external_id=item.external_id,
                content_hash=content_hash,
            )

            existing, latest_revision = _find_existing(
                db,
                source_id=source.id,
                external_id=item.external_id,
                content_hash=content_hash,
            )
            if existing:
                await repair_raw_item_media(
                    db,
                    raw_item=existing,
                    namespace=source.connector_type,
                    candidate_blocks=blocks,
                    media_storage=storage,
                )
                result.skipped.append(existing)
                continue

            stored_blocks = await storage.materialize_blocks(
                blocks, namespace=source.connector_type
            )
            stored_blocks = normalize_content_blocks(stored_blocks)
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
                content_hash_version=3,
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
                        sha256=storage_digest(block.get("storage_path")),
                        mime_type=block.get("mime_type"),
                        alt_text=block.get("alt_text"),
                        caption=block.get("caption"),
                    )
                )
            if latest_revision is not None:
                supersede_previous_raw_revision(
                    db,
                    previous=latest_revision,
                    successor=raw_item,
                )
            result.created.append(raw_item)
            if latest_revision:
                result.revised.append(raw_item)
            if enqueue_downstream:
                enqueue_pipeline_job(db, raw_item_id=raw_item.id)
        db.commit()
    except BaseException:
        db.rollback()
        raise

    for raw_item in result.created:
        db.refresh(raw_item)
    return result


def _lock_ingestion_identity(
    db: Session,
    *,
    source_id: int,
    external_id: str | None,
    content_hash: str,
) -> None:
    """Serialize revision allocation for one source identity on PostgreSQL."""
    if db.bind is None or db.bind.dialect.name != "postgresql":
        return
    identity = external_id or f"content:{content_hash}"
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
        {"identity": f"raw-item:{source_id}:{identity}"},
    )


def _find_existing(
    db: Session,
    *,
    source_id: int,
    external_id: str | None,
    content_hash: str,
) -> tuple[RawItem | None, RawItem | None]:
    if external_id:
        latest_revision = db.scalar(
            select(RawItem).where(
                RawItem.source_id == source_id,
                RawItem.external_id == external_id,
            ).order_by(RawItem.revision.desc(), RawItem.id.desc()).limit(1)
        )
        if (
            latest_revision is not None
            and hash_content_blocks(latest_revision.content_blocks) == content_hash
        ):
            return latest_revision, latest_revision
        return None, latest_revision

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
