import asyncio
import ipaddress
from pathlib import Path

import httpx
import pytest

import app.services.media_storage as media_module
from app.services.media_storage import MediaStorage, MediaStorageError
from app.core.config import settings
from app.models.media_asset import MediaAsset
from app.models.raw_item import RawItem
from app.services.media_publication import (
    publish_raw_item_media,
    withdraw_raw_item_media,
)


def test_media_rejects_non_public_literal_addresses(tmp_path: Path) -> None:
    for url in (
        "http://127.0.0.1/a.png",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/a.png",
        "http://[fc00::1]/a.png",
    ):
        with pytest.raises(MediaStorageError, match="private or local"):
            asyncio.run(MediaStorage(tmp_path)._validate_public_url(url))


def test_media_rejects_hostname_when_any_resolution_is_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_resolve(_hostname: str, _port: int):
        return {
            ipaddress.ip_address("93.184.216.34"),
            ipaddress.ip_address("10.0.0.8"),
        }

    monkeypatch.setattr(media_module, "_resolve_host_addresses", fake_resolve)
    with pytest.raises(MediaStorageError, match="private or local"):
        asyncio.run(
            MediaStorage(tmp_path)._validate_public_url(
                "https://cdn.example.test/image.png"
            )
        )


def test_media_validates_malicious_redirect_before_following(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
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
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        follow_redirects=False,
    )
    try:
        with pytest.raises(MediaStorageError, match="private or local"):
            asyncio.run(
                MediaStorage(tmp_path)._fetch_image(
                    client, client, "https://cdn.example.test/image.png"
                )
            )
    finally:
        asyncio.run(client.aclose())
    assert requested == ["https://cdn.example.test/image.png"]


@pytest.mark.parametrize("url", ["http://127.0.0.1/a.png", "http://169.254.169.254/latest/meta-data"])
def test_proxy_never_allows_private_literal_addresses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, url: str
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    with pytest.raises(MediaStorageError, match="private or local"):
        asyncio.run(MediaStorage(tmp_path)._validate_media_url(url))


def test_proxy_rejects_unknown_hostname_without_dns_bypass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    with pytest.raises(MediaStorageError, match="not allowlisted"):
        asyncio.run(MediaStorage(tmp_path)._validate_media_url("https://unknown.example.test/a.png"))


def test_proxy_allows_known_media_cdn_without_trusting_fake_dns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")

    async def fake_resolve(_hostname: str, _port: int):
        raise AssertionError("proxied fake-IP DNS must not be used as an allow decision")

    monkeypatch.setattr(media_module, "_resolve_host_addresses", fake_resolve)
    assert asyncio.run(MediaStorage(tmp_path)._validate_media_url("https://pbs.twimg.com/media/a.jpg")) is True


def test_proxy_redirect_revalidates_target_before_second_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        return httpx.Response(302, headers={"location": "http://127.0.0.1/private.png"}, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    try:
        with pytest.raises(MediaStorageError, match="private or local"):
            asyncio.run(MediaStorage(tmp_path)._fetch_image(client, client, "https://pbs.twimg.com/media/a.jpg"))
    finally:
        asyncio.run(client.aclose())
    assert requested == ["https://pbs.twimg.com/media/a.jpg"]


def test_redirect_selects_a_client_for_each_hop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")

    async def fake_resolve(_hostname: str, _port: int):
        return {ipaddress.ip_address("93.184.216.34")}

    monkeypatch.setattr(media_module, "_resolve_host_addresses", fake_resolve)
    direct_requests: list[str] = []
    proxy_requests: list[str] = []

    def proxy_handler(request: httpx.Request) -> httpx.Response:
        proxy_requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://tiebapic.baidu.com/a.png"}, request=request)

    def direct_handler(request: httpx.Request) -> httpx.Response:
        direct_requests.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png", request=request)

    proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(proxy_handler), follow_redirects=False)
    direct_client = httpx.AsyncClient(transport=httpx.MockTransport(direct_handler), follow_redirects=False)
    try:
        payload, mime_type = asyncio.run(MediaStorage(tmp_path)._fetch_image(direct_client, proxy_client, "https://pbs.twimg.com/media/a.jpg"))
    finally:
        asyncio.run(proxy_client.aclose())
        asyncio.run(direct_client.aclose())
    assert payload == b"png"
    assert mime_type == "image/png"
    assert proxy_requests == ["https://pbs.twimg.com/media/a.jpg"]
    assert direct_requests == ["https://tiebapic.baidu.com/a.png"]


def test_direct_redirect_to_proxy_host_reselects_proxy_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")

    async def fake_resolve(_hostname: str, _port: int):
        return {ipaddress.ip_address("93.184.216.34")}

    monkeypatch.setattr(media_module, "_resolve_host_addresses", fake_resolve)
    direct_requests: list[str] = []
    proxy_requests: list[str] = []

    def direct_handler(request: httpx.Request) -> httpx.Response:
        direct_requests.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://pbs.twimg.com/media/a.jpg"}, request=request)

    def proxy_handler(request: httpx.Request) -> httpx.Response:
        proxy_requests.append(str(request.url))
        return httpx.Response(200, headers={"content-type": "image/png"}, content=b"png", request=request)

    direct_client = httpx.AsyncClient(transport=httpx.MockTransport(direct_handler), follow_redirects=False)
    proxy_client = httpx.AsyncClient(transport=httpx.MockTransport(proxy_handler), follow_redirects=False)
    try:
        asyncio.run(MediaStorage(tmp_path)._fetch_image(direct_client, proxy_client, "https://tiebapic.baidu.com/a.jpg"))
    finally:
        asyncio.run(direct_client.aclose())
        asyncio.run(proxy_client.aclose())
    assert direct_requests == ["https://tiebapic.baidu.com/a.jpg"]
    assert proxy_requests == ["https://pbs.twimg.com/media/a.jpg"]


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
