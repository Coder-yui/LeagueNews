import asyncio
import hashlib
import ipaddress
import logging
import mimetypes
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

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
    ) -> list[dict[str, object]]:
        safe_namespace = re.sub(r"[^a-zA-Z0-9_-]+", "-", namespace).strip("-") or "unknown"
        try:
            async with httpx.AsyncClient(
                follow_redirects=False,
                trust_env=False,
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
                                public_path, mime_type = await self._download_image(
                                    client, str(copied["source_url"]), safe_namespace
                                )
                            copied["storage_path"] = public_path
                            if not copied.get("mime_type"):
                                copied["mime_type"] = mime_type
                        except MediaStorageError as exc:
                            # Keep the source URL and article position. A single
                            # unavailable image must not discard the whole batch.
                            logger.warning(
                                "media_download_rejected host=%s reason=%s",
                                _safe_url_label(str(copied["source_url"])),
                                exc,
                            )
                    return copied

                materialized = await asyncio.gather(
                    *(materialize(block) for block in blocks)
                )
            return materialized
        except BaseException:
            # Digest-addressed files may already be referenced by another
            # concurrent transaction. Leave partial-batch orphans for a
            # reference-aware garbage collector instead of deleting eagerly.
            raise

    async def _download_image(
        self, client: httpx.AsyncClient, url: str, namespace: str
    ) -> tuple[str, str]:
        await self._validate_public_url(url)
        try:
            payload, mime_type = await self._fetch_image(client, url)
        except (httpx.TransportError, httpx.HTTPStatusError) as exc:
            raise MediaStorageError(
                f"remote image download failed for {_safe_url_label(url)}"
            ) from exc

        digest = hashlib.sha256(payload).hexdigest()
        extension = mimetypes.guess_extension(mime_type) or ".img"
        if extension == ".jpe":
            extension = ".jpg"
        relative = Path("private") / namespace / f"{digest}{extension}"
        destination = (self.root / relative).resolve()
        if self.root not in destination.parents:
            raise MediaStorageError("invalid media destination")
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            return f"/api/v1/media-assets/files/{namespace}/{relative.name}", mime_type
        destination.write_bytes(payload)
        return f"/api/v1/media-assets/files/{namespace}/{relative.name}", mime_type

    async def _fetch_image(
        self, client: httpx.AsyncClient, url: str
    ) -> tuple[bytearray, str]:
        current_url = url
        for _redirect in range(6):
            await self._validate_public_url(current_url)
            async with client.stream("GET", current_url) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise MediaStorageError("media redirect has no Location")
                    current_url = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                await self._validate_public_url(str(response.url))
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
        raise MediaStorageError("remote image exceeded redirect limit")

    @staticmethod
    async def _validate_public_url(url: str) -> None:
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise MediaStorageError("media URL must use http or https")
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            addresses = await _resolve_host_addresses(
                parsed.hostname,
                parsed.port or (443 if parsed.scheme == "https" else 80),
            )
            if not addresses:
                raise MediaStorageError("media hostname did not resolve")
        else:
            addresses = {address}
        if any(not address.is_global for address in addresses):
            raise MediaStorageError("private or local media URLs are not allowed")

async def _resolve_host_addresses(
    hostname: str, port: int
) -> set[ipaddress.IPv4Address | ipaddress.IPv6Address]:
    try:
        records = await asyncio.get_running_loop().getaddrinfo(
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exc:
        raise MediaStorageError("media hostname resolution failed") from exc
    addresses: set[ipaddress.IPv4Address | ipaddress.IPv6Address] = set()
    for record in records:
        try:
            addresses.add(ipaddress.ip_address(record[4][0]))
        except ValueError:
            continue
    return addresses


def _safe_url_label(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname or 'unknown'}{parsed.path}"
