from datetime import UTC, date, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.routes.normalized_items import (
    list_normalized_items,
    list_published_days,
    list_published_items,
    list_published_items_page,
)
from app.services.published_items import published_item_payload, published_item_statement
from app.core.database import Base
from app.domain.importance import is_featured_message
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem, NormalizedItemMediaExtraction
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.normalized_item import PublishedItemRead


def test_featured_message_threshold_includes_boundary() -> None:
    assert is_featured_message(0.75)
    assert not is_featured_message(0.7499)
    assert not is_featured_message(1.0, content_form="repost")
    assert is_featured_message(0.75, content_form="quote")


def test_published_payload_combines_reviewed_item_source_and_bilingual_ocr() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(
            name="Designer X",
            connector_type="x_twitter",
            reliability_score=0.85,
        )
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
            entities=[{"name": "26.13", "type": "patch"}],
            products=["lol_pc"],
            message_type="game_official_preview",
            topics=["balance_gameplay"],
            classification_version="message-taxonomy-v2",
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

        loaded = db.scalar(published_item_statement().where(NormalizedItem.id == item.id))
        payload = PublishedItemRead.model_validate(published_item_payload(loaded))

        assert payload.source_name == "Designer X"
        assert payload.source_reliability_score == 0.85
        assert payload.products == ["lol_pc"]
        assert payload.message_type == "game_official_preview"
        assert payload.topics == ["balance_gameplay"]
        assert payload.original_content_blocks[0]["text"] == "Patch preview"
        assert payload.translated_content_blocks[0]["text"] == "版本预览"
        assert payload.media_extractions[0].block_index == 1
        assert (
            payload.media_extractions[0].original_data["sections"][0]["entries"][0]["target"]
            == "Aphelios"
        )
        assert (
            payload.media_extractions[0].translated_data["sections"][0]["entries"][0]["target"]
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
                    entities=[],
                    products=["unknown"],
                    message_type="unknown",
                    topics=["unknown"],
                    classification_version="message-taxonomy-v2",
                    importance_score=0.5,
                    language="zh-CN",
                    source_language="zh-CN",
                    target_language="zh-CN",
                    translated_title=external_id,
                    translated_text=external_id,
                    translated_content_blocks=[{"type": "paragraph", "text": external_id}],
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
                content_blocks=[{"type": "paragraph", "text": f"Revision {revision}"}],
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
                entities=[],
                products=["unknown"],
                message_type="unknown",
                topics=["unknown"],
                classification_version="message-taxonomy-v2",
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
        assert [item["id"] for item in list_published_items(db)] == [latest_item.id]
        assert old_item.id != latest_item.id


def test_published_page_featured_filter_preserves_normal_list() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Featured Source", connector_type="manual")
        db.add(source)
        db.flush()

        cases = ((0.75, "original"), (0.7499, "original"), (0.9, "repost"))
        for index, (score, content_form) in enumerate(cases, start=1):
            raw = RawItem(
                source_id=source.id,
                external_id=f"featured-{index}",
                native_title=f"Featured {index}",
                content_blocks=[{"type": "paragraph", "text": f"Featured {index}"}],
            )
            db.add(raw)
            db.flush()
            db.add(
                NormalizedItem(
                    raw_item_id=raw.id,
                    normalized_title=raw.native_title,
                    normalized_text=raw.native_title,
                    summary=raw.native_title,
                    entities=[],
                    products=["unknown"],
                    message_type="unknown",
                    topics=["unknown"],
                    classification_version="message-taxonomy-v3",
                    content_form=content_form,
                    importance_score=score,
                    target_language="zh-CN",
                    translated_title=raw.native_title,
                    translated_content_blocks=[],
                    translation_status="not_required",
                    analysis_model="test",
                    analysis_version="test",
                )
            )
        db.commit()

        all_items = list_published_items_page(
            db=db, limit=100, offset=0, sort_by="time", sort="desc"
        )
        featured_items = list_published_items_page(
            db=db, featured=True, limit=100, offset=0, sort_by="time", sort="desc"
        )

        assert all_items["total"] == 3
        assert featured_items["total"] == 1
        assert featured_items["items"][0]["importance_score"] == 0.75


def test_published_page_filters_each_product_membership_and_supports_requested_sorts() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Product filter source", connector_type="manual")
        db.add(source)
        db.flush()

        def add_item(
            external_id: str,
            products: list[str],
            importance_score: float,
            published_at: datetime,
        ) -> NormalizedItem:
            raw = RawItem(
                source_id=source.id,
                external_id=external_id,
                native_title=external_id,
                content_blocks=[{"type": "paragraph", "text": external_id}],
                published_at=published_at,
            )
            db.add(raw)
            db.flush()
            item = NormalizedItem(
                raw_item_id=raw.id,
                normalized_title=external_id,
                normalized_text=external_id,
                summary=external_id,
                entities=[],
                products=products,
                message_type="game_announcement",
                topics=["gameplay"],
                classification_version="message-taxonomy-v3",
                importance_score=importance_score,
                target_language="zh-CN",
                translated_title=external_id,
                translated_content_blocks=[],
                translation_status="not_required",
                analysis_model="test",
                analysis_version="test",
            )
            db.add(item)
            db.flush()
            return item

        pc_only = add_item(
            "pc-only",
            ["lol_pc"],
            0.2,
            datetime(2026, 8, 1, tzinfo=UTC),
        )
        tft_only = add_item(
            "tft-only",
            ["tft"],
            0.5,
            datetime(2026, 8, 2, tzinfo=UTC),
        )
        shared = add_item(
            "shared",
            ["lol_pc", "tft"],
            0.9,
            datetime(2026, 8, 3, tzinfo=UTC),
        )
        db.commit()

        pc_page = list_published_items_page(
            product="lol_pc",
            sort_by="importance",
            sort="asc",
            limit=25,
            offset=0,
            db=db,
        )
        tft_page = list_published_items_page(
            product="tft",
            sort_by="importance",
            sort="desc",
            limit=25,
            offset=0,
            db=db,
        )
        oldest_first = list_published_items_page(
            sort_by="time",
            sort="asc",
            limit=25,
            offset=0,
            db=db,
        )
        newest_first = list_published_items_page(
            sort_by="time",
            sort="desc",
            limit=25,
            offset=0,
            db=db,
        )

        assert [item["id"] for item in pc_page["items"]] == [pc_only.id, shared.id]
        assert [item["id"] for item in tft_page["items"]] == [shared.id, tft_only.id]
        assert [item["id"] for item in oldest_first["items"]] == [
            pc_only.id,
            tft_only.id,
            shared.id,
        ]
        assert [item["id"] for item in newest_first["items"]] == [
            shared.id,
            tft_only.id,
            pc_only.id,
        ]


def test_published_days_and_date_filter_use_requested_civil_timezone() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Timezone source", connector_type="manual")
        db.add(source)
        db.flush()

        def add_item(external_id: str, published_at: datetime | None, ingested_at: datetime) -> int:
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
            item = NormalizedItem(
                raw_item_id=raw.id,
                normalized_title=external_id,
                normalized_text=external_id,
                summary=external_id,
                entities=[],
                products=["lol_pc"],
                message_type="game_announcement",
                topics=["gameplay"],
                classification_version="message-taxonomy-v3",
                importance_score=0.5,
                target_language="zh-CN",
                translated_title=external_id,
                translated_content_blocks=[],
                translation_status="not_required",
                analysis_model="test",
                analysis_version="test",
            )
            db.add(item)
            db.flush()
            return item.id

        previous_day = add_item(
            "before-shanghai-midnight",
            datetime(2026, 8, 12, 15, 59, tzinfo=UTC),
            datetime(2026, 8, 12, 16, 10, tzinfo=UTC),
        )
        start_of_day = add_item(
            "at-shanghai-midnight",
            datetime(2026, 8, 12, 16, 0, tzinfo=UTC),
            datetime(2026, 8, 12, 16, 10, tzinfo=UTC),
        )
        fallback_to_ingestion = add_item(
            "ingestion-fallback",
            None,
            datetime(2026, 8, 13, 2, 0, tzinfo=UTC),
        )
        next_day = add_item(
            "next-shanghai-day",
            datetime(2026, 8, 13, 16, 0, tzinfo=UTC),
            datetime(2026, 8, 13, 16, 10, tzinfo=UTC),
        )
        db.commit()

        day_list = list_published_days(timezone_name="Asia/Shanghai", limit=30, db=db)
        august_13_page = list_published_items_page(
            published_date=date(2026, 8, 13),
            timezone_name="Asia/Shanghai",
            sort_by="time",
            sort="desc",
            limit=100,
            offset=0,
            db=db,
        )

        assert [(day["date"], day["count"]) for day in day_list["days"]] == [
            (date(2026, 8, 14), 1),
            (date(2026, 8, 13), 2),
            (date(2026, 8, 12), 1),
        ]
        assert day_list["timezone"] == "Asia/Shanghai"
        assert august_13_page["total"] == 2
        assert {item["id"] for item in august_13_page["items"]} == {
            start_of_day,
            fallback_to_ingestion,
        }
        assert previous_day not in {item["id"] for item in august_13_page["items"]}
        assert next_day not in {item["id"] for item in august_13_page["items"]}


def test_published_days_search_joins_source_without_duplicate_counts() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source_a = Source(name="Source A", connector_type="manual")
        source_b = Source(name="Source B", connector_type="manual")
        db.add_all([source_a, source_b])
        db.flush()

        def add_item(source: Source, external_id: str, text: str) -> NormalizedItem:
            raw = RawItem(
                source_id=source.id,
                external_id=external_id,
                native_title=external_id,
                content_blocks=[{"type": "paragraph", "text": text}],
                published_at=datetime(2026, 8, 14, 1, tzinfo=UTC),
            )
            db.add(raw)
            db.flush()
            item = NormalizedItem(
                raw_item_id=raw.id,
                normalized_title=external_id,
                normalized_text=text,
                summary=external_id,
                entities=[],
                products=["lol_pc"],
                message_type="game_announcement",
                topics=["gameplay"],
                classification_version="message-taxonomy-v3",
                importance_score=0.5,
                target_language="zh-CN",
                translated_title=external_id,
                translated_text=text,
                translated_content_blocks=[],
                translation_status="not_required",
                analysis_model="test",
                analysis_version="test",
            )
            db.add(item)
            return item

        matching_item = add_item(source_a, "message-a", "BodyRecallTerm")
        add_item(source_b, "message-b", "Other body")
        db.commit()

        body_days = list_published_days(search="BodyRecallTerm", limit=30, db=db)
        source_days = list_published_days(search="Source A", limit=30, db=db)
        body_page = list_published_items_page(
            search="BodyRecallTerm",
            sort_by="time",
            sort="desc",
            limit=25,
            offset=0,
            db=db,
        )
        source_page = list_published_items_page(
            search="Source A",
            sort_by="time",
            sort="desc",
            limit=25,
            offset=0,
            db=db,
        )

        assert [(day["date"], day["count"]) for day in body_days["days"]] == [
            (date(2026, 8, 14), 1)
        ]
        assert [(day["date"], day["count"]) for day in source_days["days"]] == [
            (date(2026, 8, 14), 1)
        ]
        assert body_page["total"] == 1
        assert source_page["total"] == 1
        assert body_page["items"][0]["id"] == matching_item.id
        assert source_page["items"][0]["id"] == matching_item.id
