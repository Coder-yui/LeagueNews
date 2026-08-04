import shutil
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings
from app.models.raw_item import RawItem


def publish_raw_item_media(raw_item: RawItem) -> None:
    root = settings.resolved_media_root.resolve()
    for asset in raw_item.media_assets:
        if not asset.storage_path:
            continue
        if asset.storage_path.startswith("/media/"):
            asset.public_path = asset.storage_path
            asset.visibility = "legacy_public"
            asset.published_at = asset.published_at or datetime.now(UTC)
            continue
        parts = Path(urlparse(asset.storage_path).path).parts
        if len(parts) < 2:
            continue
        namespace, filename = parts[-2], parts[-1]
        source = (root / "private" / namespace / filename).resolve()
        destination = (root / "published" / namespace / filename).resolve()
        if root not in source.parents or root not in destination.parents:
            continue
        if not source.is_file():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not destination.exists():
            shutil.copy2(source, destination)
        asset.public_path = f"/media/published/{namespace}/{filename}"
        asset.visibility = "published"
        asset.published_at = datetime.now(UTC)


def withdraw_raw_item_media(raw_item: RawItem) -> None:
    for asset in raw_item.media_assets:
        if asset.visibility != "published" or not asset.public_path:
            continue
        # Access is revoked by the database-backed public endpoint. Keeping the
        # copy until asynchronous garbage collection avoids a file deletion
        # escaping a transaction that later rolls back.
        asset.public_path = None
        asset.visibility = "private"
        asset.published_at = None
