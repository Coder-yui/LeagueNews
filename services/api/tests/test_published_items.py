from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.routes.normalized_items import (
    _published_payload,
    _published_statement,
    list_normalized_items,
    list_published_items,
)
from app.core.database import Base
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem, NormalizedItemMediaExtraction
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.normalized_item import PublishedItemRead


def test_published_payload_combines_reviewed_item_source_and_bilingual_ocr() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Designer X", connector_type="x_twitter")
        db.add(source)
        db.flush()
        raw = RawItem(
            source_id=source.id,
            external_id="post-1",
            native_title="Patch 26.13 Full Preview",
            canonical_url="https://x.com/example/status/1",
            author_name="Designer",
            language="en",
            content_blocks=[
                {"id": "b1", "type": "paragraph", "text": "Patch preview"},
                {
                    "id": "b2",
                    "type": "image",
                    "storage_path": "/media/patch.jpg",
                },
            ],
        )
        db.add(raw)
        db.flush()
        asset = MediaAsset(
            raw_item_id=raw.id,
            block_index=1,
            storage_path="/media/patch.jpg",
            mime_type="image/jpeg",
        )
        db.add(asset)
        db.flush()
        extraction = MediaExtraction(
            media_asset_id=asset.id,
            task_type="patch_preview",
            provider="test",
            ocr_engine="test",
            structuring_model="test",
            schema_version="v2",
            status="processed",
            raw_ocr_text="Aphelios damage",
            ocr_lines=[],
            structured_data={
                "sections": [
                    {
                        "label": "CHAMPION BUFFS",
                        "entries": [
                            {
                                "target": "Aphelios",
                                "target_type": "champion",
                                "changes": ["Damage 10 → 20"],
                            }
                        ],
                    }
                ]
            },
            processing_config={},
            confidence=0.98,
        )
        db.add(extraction)
        db.flush()
        item = NormalizedItem(
            raw_item_id=raw.id,
            normalized_title="26.13版本完整预览",
            normalized_text="Patch preview",
            summary="设计师公布版本预览。",
            category="版本更新",
            entities=[{"name": "26.13", "type": "patch"}],
            importance_score=0.8,
            language="en",
            source_language="en",
            target_language="zh-CN",
            translated_title="26.13版本完整预览",
            translated_text="版本预览",
            translated_content_blocks=[
                {"id": "b1", "type": "paragraph", "text": "版本预览"},
                {
                    "id": "b2",
                    "type": "image",
                    "storage_path": "/media/patch.jpg",
                },
            ],
            translation_status="translated",
            translation_model="test",
            analysis_model="test",
            analysis_version="test",
        )
        db.add(item)
        db.flush()
        db.add(
            NormalizedItemMediaExtraction(
                normalized_item_id=item.id,
                media_extraction_id=extraction.id,
                translated_structured_data={
                    "sections": [
                        {
                            "label": "英雄增强",
                            "entries": [
                                {
                                    "target": "厄斐琉斯",
                                    "target_type": "champion",
                                    "changes": ["伤害 10 → 20"],
                                }
                            ],
                        }
                    ]
                },
                translation_status="translated",
                translation_model="test",
            )
        )
        db.commit()

        loaded = db.scalar(
            _published_statement().where(NormalizedItem.id == item.id)
        )
        payload = PublishedItemRead.model_validate(_published_payload(loaded))

        assert payload.source_name == "Designer X"
        assert payload.original_content_blocks[0]["text"] == "Patch preview"
        assert payload.translated_content_blocks[0]["text"] == "版本预览"
        assert payload.media_extractions[0].block_index == 1
        assert (
            payload.media_extractions[0].original_data["sections"][0]["entries"][0][
                "target"
            ]
            == "Aphelios"
        )
        assert (
            payload.media_extractions[0].translated_data["sections"][0]["entries"][0][
                "target"
            ]
            == "厄斐琉斯"
        )


def test_published_items_order_by_original_publish_time_with_ingestion_fallback() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Test Source", connector_type="x_twitter")
        db.add(source)
        db.flush()

        def add_item(
            *,
            external_id: str,
            published_at: datetime | None,
            ingested_at: datetime,
        ) -> RawItem:
            raw = RawItem(
                source_id=source.id,
                external_id=external_id,
                native_title=external_id,
                content_blocks=[{"type": "paragraph", "text": external_id}],
                published_at=published_at,
                ingested_at=ingested_at,
            )
            db.add(raw)
            db.flush()
            db.add(
                NormalizedItem(
                    raw_item_id=raw.id,
                    normalized_title=external_id,
                    normalized_text=external_id,
                    summary=external_id,
                    category="测试",
                    entities=[],
                    importance_score=0.5,
                    language="zh-CN",
                    source_language="zh-CN",
                    target_language="zh-CN",
                    translated_title=external_id,
                    translated_text=external_id,
                    translated_content_blocks=[
                        {"type": "paragraph", "text": external_id}
                    ],
                    translation_status="not_required",
                    translation_model=None,
                    analysis_model="test",
                    analysis_version="test",
                )
            )
            return raw

        older = add_item(
            external_id="older",
            published_at=datetime(2026, 7, 1, tzinfo=UTC),
            ingested_at=datetime(2026, 7, 5, tzinfo=UTC),
        )
        fallback = add_item(
            external_id="fallback",
            published_at=None,
            ingested_at=datetime(2026, 7, 2, tzinfo=UTC),
        )
        newest = add_item(
            external_id="newest",
            published_at=datetime(2026, 7, 3, tzinfo=UTC),
            ingested_at=datetime(2026, 7, 4, tzinfo=UTC),
        )
        db.commit()

        payloads = list_published_items(db)

        assert [payload["raw_item_id"] for payload in payloads] == [
            newest.id,
            fallback.id,
            older.id,
        ]


def test_message_lists_only_include_latest_raw_revision() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Versioned Source", connector_type="riot_official")
        db.add(source)
        db.flush()

        def add_revision(
            *,
            external_id: str,
            revision: int,
            supersedes_raw_item_id: int | None,
        ) -> tuple[RawItem, NormalizedItem]:
            raw = RawItem(
                source_id=source.id,
                external_id=external_id,
                native_title=f"Revision {revision}",
                content_blocks=[
                    {"type": "paragraph", "text": f"Revision {revision}"}
                ],
                revision=revision,
                supersedes_raw_item_id=supersedes_raw_item_id,
            )
            db.add(raw)
            db.flush()
            item = NormalizedItem(
                raw_item_id=raw.id,
                normalized_title=f"Revision {revision}",
                normalized_text=f"Revision {revision}",
                summary=f"Revision {revision}",
                category="测试",
                entities=[],
                importance_score=0.5,
                target_language="zh-CN",
                translated_title=f"Revision {revision}",
                translated_content_blocks=[],
                translation_status="not_required",
                analysis_model="test",
                analysis_version="test",
            )
            db.add(item)
            db.commit()
            return raw, item

        old_raw, old_item = add_revision(
            external_id="same-article",
            revision=1,
            supersedes_raw_item_id=None,
        )
        _, latest_item = add_revision(
            external_id="same-article",
            revision=2,
            supersedes_raw_item_id=old_raw.id,
        )

        assert [item.id for item in list_normalized_items(db)] == [latest_item.id]
        assert [item["id"] for item in list_published_items(db)] == [
            latest_item.id
        ]
        assert old_item.id != latest_item.id
