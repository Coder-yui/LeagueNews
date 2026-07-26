from sqlalchemy import create_engine
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.api.routes.normalized_items import _published_payload, _published_statement
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
            credibility="official",
            credibility_score=1.0,
            credibility_evidence=["官方设计师"],
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
