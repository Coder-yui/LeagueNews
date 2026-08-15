import asyncio
import ipaddress
from pathlib import Path

import httpx
import pytest

import app.services.media_storage as media_module
from app.core.config import settings
from app.models.media_asset import MediaAsset
from app.models.raw_item import RawItem
from app.services.media_publication import publish_raw_item_media, withdraw_raw_item_media
from app.services.media_storage import MediaStorage, MediaStorageError


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/a.png",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/a.png",
        "http://[fc00::1]/a.png",
    ],
)
def test_media_rejects_private_literal_addresses_in_all_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    for proxy_url in ("", "http://127.0.0.1:7897"):
        monkeypatch.setattr(settings, "outbound_proxy_url", proxy_url)
        with pytest.raises(MediaStorageError, match="private or local"):
            asyncio.run(MediaStorage(tmp_path)._validate_media_url(url))


def test_media_rejects_direct_hostname_with_private_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_resolve(_hostname: str, _port: int):
        return {ipaddress.ip_address("93.184.216.34"), ipaddress.ip_address("10.0.0.8")}

    monkeypatch.setattr(settings, "outbound_proxy_url", "")
    monkeypatch.setattr(media_module, "_resolve_host_addresses", fake_resolve)
    with pytest.raises(MediaStorageError, match="private or local"):
        asyncio.run(MediaStorage(tmp_path)._validate_media_url("https://cdn.example.test/image.png"))


def test_media_proxy_skips_hostname_dns_but_not_literal_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "outbound_proxy_url", "http://127.0.0.1:7897")

    async def fake_resolve(_hostname: str, _port: int):
        raise AssertionError("proxy mode must not use local hostname DNS validation")

    monkeypatch.setattr(media_module, "_resolve_host_addresses", fake_resolve)
    asyncio.run(MediaStorage(tmp_path)._validate_media_url("https://cdn.example.test/image.png"))


def test_media_uses_explicit_proxy_and_ignores_system_proxy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[dict[str, object]] = []

    class CaptureClient:
        def __init__(self, **kwargs: object) -> None:
            captured.append(kwargs)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr(media_module.httpx, "AsyncClient", CaptureClient)
    monkeypatch.setenv("HTTP_PROXY", "http://system-proxy.test:7897")
    monkeypatch.setenv("HTTPS_PROXY", "http://system-proxy.test:7897")
    monkeypatch.setattr(settings, "outbound_proxy_url", "")
    assert asyncio.run(MediaStorage(tmp_path).materialize_blocks([], namespace="test")) == []
    assert captured[-1]["proxy"] is None
    assert captured[-1]["trust_env"] is False

    monkeypatch.setattr(settings, "outbound_proxy_url", "http://127.0.0.1:7897")
    assert asyncio.run(MediaStorage(tmp_path).materialize_blocks([], namespace="test")) == []
    assert captured[-1]["proxy"] == "http://127.0.0.1:7897"


def test_media_validates_malicious_redirect_before_following(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "outbound_proxy_url", "")

    async def fake_resolve(_hostname: str, _port: int):
        return {ipaddress.ip_address("93.184.216.34")}

    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(
            302,
            headers={"location": "http://127.0.0.1/private.png"},
            request=request,
        )

    monkeypatch.setattr(media_module, "_resolve_host_addresses", fake_resolve)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        with pytest.raises(MediaStorageError, match="private or local"):
            asyncio.run(MediaStorage(tmp_path)._fetch_image(client, "https://cdn.example.test/image.png"))
    finally:
        asyncio.run(client.aclose())
    assert requested == ["https://cdn.example.test/image.png"]


def test_media_rejects_non_images_and_size_limit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "outbound_proxy_url", "http://127.0.0.1:7897")
    monkeypatch.setattr(settings, "media_max_bytes", 3)

    def non_image(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(non_image), follow_redirects=False)
    try:
        with pytest.raises(MediaStorageError, match="not an image"):
            asyncio.run(MediaStorage(tmp_path)._fetch_image(client, "https://cdn.example.test/a"))
    finally:
        asyncio.run(client.aclose())

    def oversized(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={"content-type": "image/png", "content-length": "4"},
            content=b"1234",
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(oversized), follow_redirects=False)
    try:
        with pytest.raises(MediaStorageError, match="MEDIA_MAX_BYTES"):
            asyncio.run(MediaStorage(tmp_path)._fetch_image(client, "https://cdn.example.test/a"))
    finally:
        asyncio.run(client.aclose())


def test_private_media_is_copied_on_publish_and_removed_on_withdraw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    private = tmp_path / "private" / "x_twitter" / "a.jpg"
    private.parent.mkdir(parents=True)
    private.write_bytes(b"image")
    raw = RawItem(
        source_id=1,
        content_blocks=[],
        media_assets=[
            MediaAsset(
                raw_item_id=1,
                block_index=0,
                storage_path="/api/v1/media-assets/files/x_twitter/a.jpg",
                visibility="private",
            )
        ],
    )

    publish_raw_item_media(raw)
    asset = raw.media_assets[0]
    published = tmp_path / "published" / "x_twitter" / "a.jpg"
    assert published.read_bytes() == b"image"
    assert asset.public_path == "/media/published/x_twitter/a.jpg"
    assert asset.visibility == "published"

    withdraw_raw_item_media(raw)
    assert published.exists()
    assert private.exists()
    assert asset.public_path is None
    assert asset.visibility == "private"
