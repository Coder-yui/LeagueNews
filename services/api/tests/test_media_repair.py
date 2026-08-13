import asyncio
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.core.config import settings
from app.models.media_asset import MediaAsset
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.services.media_repair import (
    project_media_storage_paths,
    repair_raw_item_media,
)


class FileCreatingMediaStorage:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def materialize_blocks(
        self, blocks: list[dict[str, object]], *, namespace: str
    ) -> list[dict[str, object]]:
        digest = "b" * 64
        path = self.root / "private" / namespace / f"{digest}.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"repaired image")
        return [
            {
                **block,
                "storage_path": f"/api/v1/media-assets/files/{namespace}/{digest}.jpg",
                "mime_type": "image/jpeg",
            }
            for block in blocks
        ]


def test_repair_publishes_media_without_mutating_raw_blocks(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "media_root", str(tmp_path))
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Repair source", connector_type="test_web")
        raw = RawItem(
            source=source,
            external_id="repair-1",
            content_blocks=[
                {
                    "id": "b0001",
                    "type": "image",
                    "source_url": "https://cdn.example.com/image.jpg",
                    "mime_type": "image/jpeg",
                }
            ],
        )
        asset = MediaAsset(
            raw_item=raw,
            block_index=0,
            source_url="https://cdn.example.com/image.jpg",
            mime_type="image/jpeg",
        )
        normalized = NormalizedItem(
            raw_item=raw,
            normalized_title="Repair",
            normalized_text="Repair",
            summary="Repair",
            importance_score=0.5,
            analysis_model="test",
            publication_status="published",
        )
        db.add_all([source, raw, asset, normalized])
        db.commit()

        result = asyncio.run(
            repair_raw_item_media(
                db,
                raw_item=raw,
                namespace="test_web",
                media_storage=FileCreatingMediaStorage(tmp_path),
            )
        )
        db.commit()

        assert [value.id for value in result.repaired_assets] == [asset.id]
        assert result.failed_asset_ids == []
        assert "storage_path" not in raw.content_blocks[0]
        assert asset.storage_path == (
            "/api/v1/media-assets/files/test_web/" + "b" * 64 + ".jpg"
        )
        assert asset.sha256 == "b" * 64
        assert asset.public_path == "/media/published/test_web/" + "b" * 64 + ".jpg"
        assert asset.visibility == "published"
        assert (tmp_path / "published" / "test_web" / ("b" * 64 + ".jpg")).is_file()
        assert project_media_storage_paths(raw)[0]["storage_path"] == asset.storage_path


def test_project_media_storage_paths_does_not_mutate_raw_evidence() -> None:
    raw = RawItem(
        source_id=1,
        content_blocks=[
            {
                "id": "b0001",
                "type": "image",
                "source_url": "https://cdn.example.com/image.jpg",
            }
        ],
        media_assets=[
            MediaAsset(
                raw_item_id=1,
                block_index=0,
                storage_path="/api/v1/media-assets/files/test/image.jpg",
            )
        ],
    )

    projected = project_media_storage_paths(raw)

    assert projected[0]["storage_path"] == (
        "/api/v1/media-assets/files/test/image.jpg"
    )
    assert "storage_path" not in raw.content_blocks[0]
