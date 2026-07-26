import asyncio
import hashlib
import ipaddress
import logging
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)


class MediaStorageError(RuntimeError):
    """Raised when a remote media asset cannot be stored safely."""


class MediaStorage:
    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or settings.resolved_media_root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    async def materialize_blocks(
        self, blocks: list[dict[str, object]], *, namespace: str
    ) -> tuple[list[dict[str, object]], list[Path]]:
        safe_namespace = re.sub(r"[^a-zA-Z0-9_-]+", "-", namespace).strip("-") or "unknown"
        created_files: list[Path] = []
        try:
            async with httpx.AsyncClient(
                follow_redirects=True,
                timeout=httpx.Timeout(12, connect=5),
                headers={"User-Agent": settings.connector_user_agent},
            ) as client:
                semaphore = asyncio.Semaphore(6)

                async def materialize(block: dict[str, object]) -> dict[str, object]:
                    copied = dict(block)
                    if (
                        copied.get("type") == "image"
                        and copied.get("source_url")
                        and not copied.get("storage_path")
                    ):
                        try:
                            async with semaphore:
                                public_path, created, mime_type = await self._download_image(
                                    client, str(copied["source_url"]), safe_namespace
                                )
                            copied["storage_path"] = public_path
                            if not copied.get("mime_type"):
                                copied["mime_type"] = mime_type
                            if created:
                                created_files.append(created)
                        except MediaStorageError as exc:
                            # Keep the source URL and article position. A single
                            # unavailable image must not discard the whole batch.
                            logger.warning("keeping remote-only image %s: %s", copied["source_url"], exc)
                    return copied

                materialized = await asyncio.gather(
                    *(materialize(block) for block in blocks)
                )
            return materialized, created_files
        except BaseException:
            self.remove_files(created_files)
            raise

    async def _download_image(
        self, client: httpx.AsyncClient, url: str, namespace: str
    ) -> tuple[str, Path | None, str]:
        self._validate_public_url(url)
        try:
            payload, mime_type = await self._fetch_image(client, url)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            raise MediaStorageError(f"remote image download failed: {url}") from exc

        digest = hashlib.sha256(payload).hexdigest()
        extension = mimetypes.guess_extension(mime_type) or ".img"
        if extension == ".jpe":
            extension = ".jpg"
        relative = Path(namespace) / f"{digest}{extension}"
        destination = (self.root / relative).resolve()
        if self.root not in destination.parents:
            raise MediaStorageError("invalid media destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return f"/media/{relative.as_posix()}", None, mime_type
        destination.write_bytes(payload)
        return f"/media/{relative.as_posix()}", destination, mime_type

    async def _fetch_image(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[bytearray, str]:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            self._validate_public_url(str(response.url))
            mime_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
            if not mime_type.startswith("image/"):
                raise MediaStorageError(f"remote media is not an image: {mime_type or 'unknown'}")
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > settings.media_max_bytes:
                raise MediaStorageError("remote image exceeds MEDIA_MAX_BYTES")
            payload = bytearray()
            async for chunk in response.aiter_bytes():
                payload.extend(chunk)
                if len(payload) > settings.media_max_bytes:
                    raise MediaStorageError("remote image exceeds MEDIA_MAX_BYTES")
        return payload, mime_type

    @staticmethod
    def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise MediaStorageError("media URL must use http or https")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            return
        if not address.is_global:
            raise MediaStorageError("private or local media URLs are not allowed")

    @staticmethod
    def remove_files(paths: list[Path]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
