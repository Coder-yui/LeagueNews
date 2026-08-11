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
                    client, "https://cdn.example.test/image.png"
                )
            )
    finally:
        asyncio.run(client.aclose())
    assert requested == ["https://cdn.example.test/image.png"]


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
