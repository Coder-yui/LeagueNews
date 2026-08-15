import asyncio
import hashlib
import ipaddress
import logging
import mimetypes
import os
import re
import socket
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

from app.core.config import settings

logger = logging.getLogger(__name__)

_REFERRER_BY_HOST: dict[str, str] = {
    "tiebapic.baidu.com": "https://tieba.baidu.com/",
    "imgsa.baidu.com": "https://tieba.baidu.com/",
    "tb.himg.baidu.com": "https://tieba.baidu.com/",
    "pic.rmb.bdstatic.com": "https://tieba.baidu.com/",
    "baidu.com": "https://tieba.baidu.com/",
    "pbs.twimg.com": "https://x.com/",
    "video.twimg.com": "https://x.com/",
    "abs.twimg.com": "https://x.com/",
    "twimg.com": "https://x.com/",
    "tgl.qq.com": "https://lol.qq.com/",
    "itea-stat.qq.com": "https://lol.qq.com/",
    "qq.com": "https://lol.qq.com/",
    "akamaihd.net": "https://lol.qq.com/",
    "sinaimg.cn": "https://weibo.com/",
}

_DIRECT_CONNECT_SUFFIXES: tuple[str, ...] = (
    ".cn",
    ".qq.com",
    ".baidu.com",
    ".sinaimg.cn",
    ".sina.cn",
    ".sina.com.cn",
    ".weibo.com",
    ".weibo.cn",
    ".bdstatic.com",
)

# A proxy is allowed only for media hosts used by the X and Riot ingestion
# paths. This deliberately small list avoids treating a locally configured
# proxy as authority to fetch arbitrary internal-looking hostnames.
_PROXIED_MEDIA_SUFFIXES: tuple[str, ...] = (
    ".twimg.com",
    ".riotgames.com",
    ".leagueoflegends.com",
    ".riotcdn.net",
    ".akamaihd.net",
)


def _referrer_for_url(url: str) -> str | None:
    hostname = (urlparse(url).hostname or "").casefold()
    for domain, referrer in _REFERRER_BY_HOST.items():
        if hostname == domain or hostname.endswith("." + domain):
            return referrer
    return None


def _is_tracking_beacon_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    if hostname in {"itea-stat.qq.com"} or hostname.endswith(".itea-stat.qq.com"):
        if path.startswith("/img/stat") or path == "/img/stat":
            return True
    return False


def _should_use_proxy(hostname: str) -> bool:
    name = hostname.casefold()
    if not name:
        return False
    for suffix in _DIRECT_CONNECT_SUFFIXES:
        if name.endswith(suffix):
            return False
    return True


def _uses_proxy(url: str) -> bool:
    """True when a URL is routed through the TUN proxy (not direct)."""
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not proxy_url:
        return False
    hostname = (urlparse(url).hostname or "").casefold()
    return _should_use_proxy(hostname)


def _is_allowed_proxied_media_host(hostname: str) -> bool:
    name = hostname.casefold().rstrip(".")
    return any(name.endswith(suffix) for suffix in _PROXIED_MEDIA_SUFFIXES)


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
        proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        base_client_args = dict(
            follow_redirects=False,
            trust_env=False,
            timeout=httpx.Timeout(20, connect=10),
            headers={"User-Agent": settings.connector_user_agent},
        )
        try:
            async with (
                httpx.AsyncClient(**base_client_args) as direct_client,
                httpx.AsyncClient(**base_client_args, proxy=proxy_url) as proxy_client,
            ):
                semaphore = asyncio.Semaphore(3)

                async def materialize(block: dict[str, object]) -> dict[str, object]:
                    copied = dict(block)
                    if (
                        copied.get("type") == "image"
                        and copied.get("source_url")
                        and not copied.get("storage_path")
                    ):
                        source_url = str(copied["source_url"])
                        if _is_tracking_beacon_url(source_url):
                            logger.info(
                                "media_skip_tracking_beacon host=%s",
                                _safe_url_label(source_url),
                            )
                            return copied
                        try:
                            async with semaphore:
                                public_path, mime_type = await self._download_image(
                                    direct_client,
                                    proxy_client,
                                    source_url,
                                    safe_namespace,
                                )
                            copied["storage_path"] = public_path
                            if not copied.get("mime_type"):
                                copied["mime_type"] = mime_type
                        except MediaStorageError as exc:
                            logger.warning(
                                "media_download_rejected host=%s reason=%s",
                                _safe_url_label(source_url),
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
        self,
        direct_client: httpx.AsyncClient,
        proxy_client: httpx.AsyncClient,
        url: str,
        namespace: str,
    ) -> tuple[str, str]:
        try:
            payload, mime_type = await self._fetch_image(direct_client, proxy_client, url)
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
        self,
        direct_client: httpx.AsyncClient,
        proxy_client: httpx.AsyncClient,
        url: str,
    ) -> tuple[bytearray, str]:
        current_url = url
        for _redirect in range(6):
            use_proxy = await self._validate_media_url(current_url)
            client = proxy_client if use_proxy else direct_client
            request_headers: dict[str, str] = {}
            referrer = _referrer_for_url(current_url)
            if referrer:
                request_headers["Referer"] = referrer
            async with client.stream(
                "GET", current_url, headers=request_headers or None
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise MediaStorageError("media redirect has no Location")
                    current_url = urljoin(str(response.url), location)
                    continue
                response.raise_for_status()
                await self._validate_media_url(str(response.url))
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
        """Validate a direct URL, including every resolved address."""
        await MediaStorage._validate_media_url(url, allow_proxy=False)

    @staticmethod
    async def _validate_media_url(url: str, *, allow_proxy: bool = True) -> bool:
        """Validate one hop and return whether it must use the proxy client."""
        parsed = urlparse(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
        ):
            raise MediaStorageError("media URL must use http or https")
        try:
            literal = ipaddress.ip_address(parsed.hostname)
        except ValueError:
            literal = None
        if literal is not None:
            # Literal IPs are always checked: a proxy must never be allowed to
            # reach RFC1918/link-local/metadata addresses through us.
            if not literal.is_global:
                raise MediaStorageError("private or local media URLs are not allowed")
            return False
        proxy_route = allow_proxy and _uses_proxy(url)
        if proxy_route:
            if not _is_allowed_proxied_media_host(parsed.hostname):
                raise MediaStorageError("proxied media hostname is not allowlisted")
            # TUN/fake-IP DNS answers are intentionally not trusted here. The
            # host suffix is the fail-closed admission boundary; literal IPs
            # above are never exempted.
            return True
        addresses = await _resolve_host_addresses(
            parsed.hostname,
            parsed.port or (443 if parsed.scheme == "https" else 80),
        )
        if not addresses:
            raise MediaStorageError("media hostname did not resolve")
        if any(not address.is_global for address in addresses):
            raise MediaStorageError("private or local media URLs are not allowed")
        return False

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
