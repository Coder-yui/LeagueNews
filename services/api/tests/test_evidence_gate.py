import asyncio
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
import app.workflows.reviewed_pipeline as reviewed_pipeline
from app.core.database import Base
from app.domain.evidence import evaluate_evidence_gate
from app.models.media_asset import MediaAsset
from app.models.pipeline import ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ReviewTask
from app.services.llm import MessageContentAnalysisResult, RelevanceResult


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _raw_item(
    db: Session,
    *,
    title: str | None,
    text: str | None,
    source_key: str = "ordinary",
    official: bool = False,
) -> RawItem:
    source = Source(
        name="x",
        connector_type="x_twitter",
        external_key=source_key,
        connector_config={},
        is_official=official,
    )
    db.add(source)
    db.flush()
    raw_item = RawItem(
        source_id=source.id,
        external_id=f"evidence-{source_key}",
        native_title=title,
        content_blocks=(
            [{"id": "b0001", "type": "paragraph", "text": text}]
            if text
            else [{"id": "b0001", "type": "image", "storage_path": "image.png"}]
        ),
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db.add(raw_item)
    db.flush()
    raw_item.media_assets.append(
        MediaAsset(block_index=1, mime_type="image/png", storage_path="image.png")
    )
    db.commit()
    return raw_item


def test_ordinary_images_are_ignored_when_source_text_is_sufficient() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="26.16 版本更新公告",
            text="本次更新包含英雄平衡调整和客户端问题修复。",
        )

        gate = evaluate_evidence_gate(raw_item, designer_patch_images=False)

        assert gate.decision == "process"
        assert gate.requires_manual_review is False
        assert gate.reason_code == "usable_source_evidence"
        assert gate.evidence_sources == ("source_title", "source_text")


def test_synthesized_display_title_does_not_inflate_evidence() -> None:
    with _session() as db:
        raw_item = _raw_item(db, title=None, text=None)
        raw_item.source.name = "League of Legends Official Account"

        gate = evaluate_evidence_gate(raw_item, designer_patch_images=False)

        assert raw_item.display_title == "League of Legends Official Account"
        assert gate.meaningful_text_characters == 0
        assert gate.evidence_sources == ()
        assert gate.decision == "insufficient_evidence"
        assert gate.reason_code == "source_text_too_short"


def test_ordinary_image_only_post_is_classified_and_published(monkeypatch) -> None:
    class MediaOnlyClient:
        async def judge_relevance(self, **_kwargs):
            return RelevanceResult(
                decision="uncertain",
                confidence=0.3,
                reason="只有图片，仍交给消息分析判断内容形式",
            )

        async def analyze_message_content(self, **_kwargs):
            return MessageContentAnalysisResult(
                title="纯图片消息",
                summary="",
                entities=[],
                products=["unknown"],
                content_form="media_only",
            )

    monkeypatch.setattr(reviewed_pipeline, "LLMClient", MediaOnlyClient)
    with _session() as db:
        raw_item = _raw_item(db, title=None, text=None)

        run = asyncio.run(
            reviewed_pipeline.start_item_processing(db, raw_item, execution_mode="automatic")
        )
        review = db.scalar(
            select(ReviewTask).where(
                ReviewTask.processing_run_id == run.id,
                ReviewTask.stage == "message_analysis",
                ReviewTask.status == "pending",
            )
        )
        assert review is not None

        run = asyncio.run(reviewed_pipeline.approve_review(db, review, note=None))

        checkpoint = db.scalar(
            select(ProcessingCheckpoint).where(
                ProcessingCheckpoint.processing_run_id == run.id,
                ProcessingCheckpoint.stage == "relevance",
            )
        )
        assert run.status == "completed"
        assert run.outcome == "approved"
        assert checkpoint is not None
        assert checkpoint.output_snapshot["decision"] == "uncertain"
        db.expire(raw_item, ["normalized_item"])
        assert raw_item.normalized_item.products == ["unknown"]
        assert raw_item.normalized_item.message_type == "unknown"
        assert raw_item.normalized_item.topics == ["unknown"]
        assert raw_item.normalized_item.importance_score == 0.0


def test_designer_patch_image_routes_to_ocr_review(monkeypatch) -> None:
    class RelevantClient:
        async def judge_relevance(self, **_kwargs):
            return RelevanceResult(
                decision="relevant",
                confidence=0.99,
                reason="设计师版本预览",
            )

    async def fake_ocr_review(db: Session, run, **_kwargs) -> None:
        reviewed_pipeline._replace_pending_review(
            db,
            run=run,
            stage=reviewed_pipeline.OCR_STAGE,
            proposal={"approved_media_extraction_ids": []},
        )
        db.commit()

    monkeypatch.setattr(reviewed_pipeline, "LLMClient", RelevantClient)
    monkeypatch.setattr(reviewed_pipeline, "_generate_ocr_review", fake_ocr_review)
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="Patch 26.16 preview",
            text=None,
            source_key="RiotPhroxzon",
        )

        run = asyncio.run(reviewed_pipeline.start_item_processing(db, raw_item))

        review = db.scalar(select(ReviewTask).where(ReviewTask.processing_run_id == run.id))
        assert review is not None
        assert review.stage == "image_ocr"
        assert run.context["evidence_gate"]["reason"] == "进入设计师版本改动图片提取"


def test_approved_patch_structure_becomes_usable_evidence() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="Patch 26.16 preview",
            text=None,
            source_key="RiotPhroxzon",
        )

        gate = evaluate_evidence_gate(
            raw_item,
            designer_patch_images=True,
            designer_patch_extraction_count=1,
        )

        assert gate.decision == "process"
        assert gate.reason_code == "usable_source_evidence"
        assert "designer_patch_changes" in gate.evidence_sources
        assert gate.designer_patch_extraction_count == 1
