import asyncio
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.connectors.base import RawItemCandidate
from app.content_blocks import content_hash, text_from_content_blocks
from app.core.database import Base
from app.models.media_asset import MediaAsset
from app.models.source import Source
from app.services.ingestion import ingest_connector_items


class PassThroughMediaStorage:
    async def materialize_blocks(
        self, blocks: list[dict[str, object]], *, namespace: str
    ) -> tuple[list[dict[str, object]], list[Path]]:
        assert namespace == "test_web"
        return [dict(block) for block in blocks], []

    def remove_files(self, paths: list[Path]) -> None:
        assert paths == []


def test_all_connectors_share_idempotent_ingestion() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Test source", connector_type="test_web")
        db.add(source)
        db.commit()
        item = RawItemCandidate(
            external_id="article-1",
            native_title="Article",
            canonical_url="https://example.com/article-1",
            content_kind="article",
            author_name="Author",
            language="en",
            published_at=None,
            content_blocks=[
                {"type": "paragraph", "text": "Before image"},
                {
                    "type": "image",
                    "source_url": "https://cdn.example.com/image.jpg",
                    "storage_path": "/media/test/image.jpg",
                    "mime_type": "image/jpeg",
                },
                {"type": "paragraph", "text": "After image"},
            ],
            provenance={"fixture": "test"},
        )

        first = asyncio.run(
            ingest_connector_items(
                db,
                source=source,
                items=[item],
                media_storage=PassThroughMediaStorage(),
            )
        )
        second = asyncio.run(
            ingest_connector_items(
                db,
                source=source,
                items=[item],
                media_storage=PassThroughMediaStorage(),
            )
        )

        assert len(first.created) == 1
        assert first.created[0].processing_status == "pending"
        assert (
            text_from_content_blocks(first.created[0].content_blocks)
            == "Before image\n\nAfter image"
        )
        assert first.created[0].source_payload is not None
        assert first.created[0].source_payload.payload["fixture"] == "test"
        assert first.created[0].content_blocks[0]["id"] == "b0001"
        assert len(second.created) == 0
        assert second.skipped[0].id == first.created[0].id
        media = db.scalar(select(MediaAsset))
        assert media is not None
        assert media.block_index == 1
        assert media.storage_path == "/media/test/image.jpg"
        assert first.created[0].content_hash == content_hash(
            first.created[0].content_blocks
        )

        revised_item = item.model_copy(
            update={
                "content_blocks": [
                    {"type": "paragraph", "text": "Edited source content"},
                ]
            }
        )
        revised = asyncio.run(
            ingest_connector_items(
                db,
                source=source,
                items=[revised_item],
                media_storage=PassThroughMediaStorage(),
            )
        )
        assert len(revised.created) == 1
        assert len(revised.revised) == 1
        assert revised.created[0].revision == 2
        assert revised.created[0].supersedes_raw_item_id == first.created[0].id

        first.created[0].native_title = "Mutated title"
        with pytest.raises(ValueError, match="RawItem content is immutable"):
            db.commit()
