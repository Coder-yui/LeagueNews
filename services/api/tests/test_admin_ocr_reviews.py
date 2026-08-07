from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.models.event import EventAggregationRun, EventReviewTask
from app.main import app
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun, ReviewTask


def test_ocr_review_queue_exposes_reviewable_extraction_details() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="OCR Source", connector_type="manual")
        db.add(source)
        db.flush()
        raw_item = RawItem(
            source_id=source.id,
            native_title="Patch preview",
            canonical_url="https://example.com/post/249",
            content_blocks=[
                {"type": "paragraph", "text": "Patch preview"},
                {
                    "type": "image",
                    "storage_path": "/media/test/preview.jpg",
                },
            ],
        )
        db.add(raw_item)
        db.flush()
        asset = MediaAsset(
            raw_item_id=raw_item.id,
            block_index=1,
            source_url="https://example.com/preview.jpg",
            storage_path="/media/test/preview.jpg",
            mime_type="image/jpeg",
        )
        db.add(asset)
        db.flush()
        table_data = {
            "preview_kind": "preview",
            "divider_x": None,
            "structure_confidence": 0.98,
            "sections": [
                {
                    "section_type": "champion_buff",
                    "label": "CHAMPION BUFFS",
                    "records": [
                        {
                            "target": "Aphelios",
                            "raw_changes": [],
                            "bbox": [0, 10, 100, 20],
                            "ocr_confidence": 0.99,
                        }
                    ],
                }
            ],
            "warnings": [],
            "boundaries": [10, 20],
        }
        extraction = MediaExtraction(
            media_asset_id=asset.id,
            task_type="patch_preview",
            provider="patch-table+rapidocr",
            ocr_engine="rapidocr",
            structuring_model="",
            schema_version="v2-ocr-review",
            status="processed",
            raw_ocr_text="CHAMPION BUFFS\nAphelios",
            ocr_lines=[],
            structured_data={},
            processing_config={"table_data": table_data},
            confidence=0.97,
        )
        db.add(extraction)
        db.flush()
        run = ProcessingRun(
            raw_item_id=raw_item.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="image_ocr",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="image_ocr",
            status="pending",
            proposal={
                "approved_media_extraction_ids": [extraction.id],
                "ocr_corrections": [],
            },
        )
        db.add(review)
        db.commit()
        review_id = review.id
        raw_item_id = raw_item.id
        extraction_id = extraction.id
        asset_id = asset.id

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get(
            "/api/v1/workflows/ocr-reviews?status=pending"
        )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert len(payload) == 1
    assert payload[0]["review_id"] == review_id
    assert payload[0]["raw_item_id"] == raw_item_id
    assert payload[0]["raw_title"] == "Patch preview"
    assert payload[0]["canonical_url"] == "https://example.com/post/249"
    assert payload[0]["extractions"] == [
        {
            "id": extraction_id,
            "media_asset_id": asset_id,
            "block_index": 1,
            "source_url": "https://example.com/preview.jpg",
            "storage_path": "/media/test/preview.jpg",
            "confidence": 0.97,
            "raw_ocr_text": "CHAMPION BUFFS\nAphelios",
            "table_data": table_data,
        }
    ]

    app.dependency_overrides[get_db] = override_get_db
    try:
        queue_response = TestClient(app).get("/api/v1/workflows/review-queue")
    finally:
        app.dependency_overrides.clear()

    assert queue_response.status_code == 200
    queue = queue_response.json()
    assert len(queue) == 1
    assert queue[0]["raw_item_id"] == raw_item_id
    assert queue[0]["canonical_url"] == "https://example.com/post/249"
    assert queue[0]["current_stage"] == "image_ocr"
    assert queue[0]["review_kind"] == "ocr"
    assert queue[0]["ocr_review"]["review_id"] == review_id


def test_review_queue_links_event_review_back_to_raw_item() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        source = Source(name="Event Source", connector_type="manual")
        db.add(source)
        db.flush()
        raw_item = RawItem(
            source_id=source.id,
            native_title="Event candidate",
            canonical_url="https://example.com/event-candidate",
            content_blocks=[
                {"type": "paragraph", "text": "Event candidate"}
            ],
        )
        db.add(raw_item)
        db.flush()
        item = NormalizedItem(
            raw_item_id=raw_item.id,
            normalized_title="事件候选",
            normalized_text="事件候选",
            summary="事件候选摘要",
            entities=[],
            primary_topic="patch",
            subtopic="patch_preview",
            product_scope="lol_pc",
            importance_score=0.8,
            analysis_model="test",
        )
        db.add(item)
        db.flush()
        run = EventAggregationRun(
            normalized_item_id=item.id,
            status="awaiting_review",
            current_stage="event_decision",
        )
        db.add(run)
        db.flush()
        review = EventReviewTask(
            event_aggregation_run_id=run.id,
            status="pending",
            proposal={
                "item": {"normalized_item_id": item.id, "title": "事件候选"},
                "candidates": [],
                "decision": {"memberships": [], "candidate_rejections": []},
            },
        )
        db.add(review)
        db.commit()
        raw_item_id = raw_item.id
        item_id = item.id
        review_id = review.id

    def override_get_db():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get("/api/v1/workflows/review-queue")
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    queue = response.json()
    assert len(queue) == 1
    assert queue[0]["raw_item_id"] == raw_item_id
    assert queue[0]["normalized_item_id"] == item_id
    assert queue[0]["current_stage"] == "event_decision"
    assert queue[0]["review_kind"] == "event"
    assert queue[0]["event_review"]["id"] == review_id
    assert queue[0]["canonical_url"] == "https://example.com/event-candidate"
