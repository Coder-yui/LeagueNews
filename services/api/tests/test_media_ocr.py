from pathlib import Path

import pytest

from app.core.config import settings
from app.services.media_ocr import OCRProcessingError, resolve_public_media_path


def test_resolve_api_media_path_uses_private_media_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    image = tmp_path / "private" / "x_twitter" / "image.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    resolved = resolve_public_media_path(
        "/api/v1/media-assets/files/x_twitter/image.jpg"
    )

    assert resolved == image


def test_resolve_static_media_path_uses_media_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    image = tmp_path / "published" / "x_twitter" / "image.jpg"
    image.parent.mkdir(parents=True)
    image.write_bytes(b"image")

    resolved = resolve_public_media_path("/media/published/x_twitter/image.jpg")

    assert resolved == image


@pytest.mark.parametrize(
    "storage_path",
    [
        "/api/v1/media-assets/files/x_twitter/../secret.jpg",
        "/media/../../secret.jpg",
    ],
)
def test_resolve_media_path_rejects_path_escape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    storage_path: str,
) -> None:
    monkeypatch.setattr(settings, "media_root", str(tmp_path))

    with pytest.raises(OCRProcessingError):
        resolve_public_media_path(storage_path)
