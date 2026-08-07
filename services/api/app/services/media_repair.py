from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from app.models.media_asset import MediaAsset
from app.models.raw_item import RawItem
from app.services.media_publication import publish_raw_item_media
from app.services.media_storage import MediaStorage


class MediaStorageProtocol(Protocol):
    async def materialize_blocks(
        self, blocks: list[dict[str, object]], *, namespace: str
    ) -> tuple[list[dict[str, object]], list[Path]]: ...

    def remove_files(self, paths: list[Path]) -> None: ...


@dataclass(slots=True)
class MediaRepairResult:
    repaired_assets: list[MediaAsset] = field(default_factory=list)
    failed_asset_ids: list[int] = field(default_factory=list)
    created_files: list[Path] = field(default_factory=list)


async def repair_raw_item_media(
    db: Session,
    *,
    raw_item: RawItem,
    namespace: str,
    candidate_blocks: list[dict[str, Any]] | None = None,
    media_storage: MediaStorageProtocol | None = None,
) -> MediaRepairResult:
    """Retry missing media without mutating immutable RawItem evidence."""
    storage = media_storage or MediaStorage()
    blocks = candidate_blocks or raw_item.content_blocks
    result = MediaRepairResult()
    targets: list[MediaAsset] = []
    download_blocks: list[dict[str, object]] = []

    for asset in sorted(raw_item.media_assets, key=lambda value: (value.block_index, value.id)):
        if asset.storage_path:
            continue
        block = (
            blocks[asset.block_index]
            if 0 <= asset.block_index < len(blocks)
            else None
        )
        if not isinstance(block, dict) or block.get("type") != "image":
            if asset.id is not None:
                result.failed_asset_ids.append(asset.id)
            continue
        source_url = block.get("source_url") or asset.source_url
        if not isinstance(source_url, str) or not source_url:
            if asset.id is not None:
                result.failed_asset_ids.append(asset.id)
            continue
        targets.append(asset)
        download_blocks.append(
            {
                "type": "image",
                "source_url": source_url,
                "mime_type": block.get("mime_type") or asset.mime_type,
                "alt_text": block.get("alt_text") or asset.alt_text,
                "caption": block.get("caption") or asset.caption,
            }
        )

    if not targets:
        return result

    materialized, created_files = await storage.materialize_blocks(
        download_blocks,
        namespace=namespace,
    )
    result.created_files.extend(created_files)
    for asset, block in zip(targets, materialized, strict=True):
        storage_path = block.get("storage_path")
        if not isinstance(storage_path, str) or not storage_path:
            if asset.id is not None:
                result.failed_asset_ids.append(asset.id)
            continue
        asset.storage_path = storage_path
        asset.sha256 = storage_digest(storage_path)
        mime_type = block.get("mime_type")
        if isinstance(mime_type, str) and mime_type:
            asset.mime_type = mime_type
        result.repaired_assets.append(asset)

    if (
        result.repaired_assets
        and raw_item.normalized_item is not None
        and raw_item.normalized_item.publication_status == "published"
    ):
        db.flush()
        result.created_files.extend(publish_raw_item_media(raw_item))
    return result


def project_media_storage_paths(raw_item: RawItem) -> list[dict[str, Any]]:
    """Overlay repaired local paths in API output while preserving stored blocks."""
    storage_by_index = {
        asset.block_index: asset.storage_path
        for asset in raw_item.media_assets
        if asset.storage_path
    }
    projected: list[dict[str, Any]] = []
    for index, block in enumerate(raw_item.content_blocks):
        copied = dict(block)
        if copied.get("type") == "image" and index in storage_by_index:
            copied["storage_path"] = storage_by_index[index]
        projected.append(copied)
    return projected


def storage_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stem = Path(urlparse(value).path).stem
    return (
        stem
        if len(stem) == 64
        and all(character in "0123456789abcdef" for character in stem)
        else None
    )
