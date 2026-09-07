import asyncio
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
import app.services.automatic_pipeline as automatic_pipeline
import app.services.pipeline_corrections as correction_service
from app.core.database import Base
from app.core.config import settings
from app.models.event import EventAggregationRun
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun, ReviewTask
from app.schemas.event_aggregation import EventAggregationResult
from app.schemas.pipeline import PipelineCorrectionCreate
from app.schemas.workflow import ReviewRejection
from app.services.automatic_pipeline import (
    _claim_next_job,
    _heartbeat_job,
    enqueue_pending_raw_items,
    enqueue_pipeline_job,
    execute_pipeline_job,
)
from app.services.pipeline_corrections import recover_failed_job, restart_raw_item_from_beginning
from app.services.pipeline_execution import PipelineExecutionGuard, PipelineLeaseLost
from app.services.review_actions import reject_review
from publication_fixture import publish_reviewed_fixture as approve_review
from test_event_aggregation_workflow import aggregate_normalized_item


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def _published_item(db: Session, *, suffix: str = "") -> NormalizedItem:
    source = Source(name=f"Correction Source{suffix}", connector_type="manual")
    db.add(source)
    db.flush()
    raw = RawItem(
        source_id=source.id,
        native_title="Correction target",
        content_blocks=[{"type": "paragraph", "text": "Correction target"}],
        published_at=datetime(2026, 7, 27, tzinfo=UTC),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title="Correction target",
        normalized_text="Correction target",
        summary="Existing summary",
        entities=[],
        products=["lol_pc"],
        message_type="game_announcement",
        topics=["activities_rewards"],
        classification_version="message-taxonomy-v2",
        importance_score=0.5,
        target_language="zh-CN",
        translated_content_blocks=[],
        translation_status="not_required",
        analysis_model="test",
    )
    db.add(item)
    db.commit()
    return item


def _job_for_item(
    item: NormalizedItem,
    *,
    job_type: str = "message",
    **kwargs: object,
) -> PipelineJob:
    is_event = job_type == "event"
    return PipelineJob(
        raw_item_id=item.raw_item_id,
        job_type=job_type,
        target_entity_type="normalized_item" if is_event else "raw_item",
        target_entity_id=item.id if is_event else item.raw_item_id,
        target_revision=item.current_revision if is_event else item.raw_item.revision,
        workflow_name="event_aggregation" if is_event else "item_processing",
        **kwargs,
    )


def _job_for_raw(raw: RawItem, **kwargs: object) -> PipelineJob:
    return PipelineJob(
        raw_item_id=raw.id,
        target_entity_type="raw_item",
        target_entity_id=raw.id,
        target_revision=raw.revision,
        workflow_name="item_processing",
        **kwargs,
    )


def _final_manual_review(
    db: Session,
    item: NormalizedItem,
    *,
    correction: PipelineCorrection | None = None,
) -> tuple[PipelineCorrection, ReviewTask]:
    if correction is None:
        correction = PipelineCorrection(
            raw_item_id=item.raw_item_id,
            normalized_item_id=item.id,
            restart_from_stage="importance",
            resume_mode="manual",
            reason="修正消息内容",
            status="running",
            started_at=datetime.now(UTC),
        )
        db.add(correction)
        item.publication_status = "withdrawn"
    db.flush()
    run = ProcessingRun(
        raw_item_id=item.raw_item_id,
        workflow_type="item",
        status="awaiting_review",
        current_stage="importance",
        execution_mode="manual",
        graph_name="item_processing",
        correction_id=correction.id,
        context={
            "approved_translation_proposal": {
                "normalized_text": "Correction target",
                "translated_title": "修正后的消息",
                "translated_text": "修正后的消息",
                "translated_content_blocks": [
                    {"type": "paragraph", "text": "修正后的消息"}
                ],
                "translation_status": "not_required",
                "translation_model": "test",
                "approved_media_extraction_ids": [],
                "translated_media_extractions": [],
            },
            "approved_message_analysis_proposal": {
                "title": "修正后的消息",
                "summary": "修正后的摘要",
                "entities": [],
                "products": ["lol_pc"],
                "content_form": "original",
                "classification_version": "message-taxonomy-v3",
            },
        },
    )
    db.add(run)
    db.flush()
    review = ReviewTask(
        processing_run_id=run.id,
        stage="importance",
        status="pending",
        proposal={
            "message_type": "game_announcement",
            "topics": ["activities_rewards"],
            "importance_score": 0.80,
            "importance_dimensions": {},
            "importance_policy_version": "test",
            "importance_calculation": {},
            "priority_score": 0.80,
            "priority_calculation": {},
        },
    )
    db.add(review)
    db.commit()
    return correction, review


class _EmptyEventClient:
    async def aggregate_events(self, **_payload: object) -> EventAggregationResult:
        return EventAggregationResult.model_validate({"mentions": []})


@pytest.mark.anyio
async def test_legacy_translation_correction_hides_message_and_rebuilds_from_evidence(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db)
    run = ProcessingRun(
        raw_item_id=item.raw_item_id,
        workflow_type="item",
        status="completed",
        outcome="approved",
        current_stage="importance",
        context={
            "approved_media_extraction_ids": [11],
            "approved_translation_proposal": {"translated_title": "old"},
        },
    )
    db.add(run)
    db.commit()
    started: dict[str, object] = {}

    async def fake_start_item(_db: Session, raw_item: RawItem, **kwargs: object):
        started["raw_item_id"] = raw_item.id
        started.update(kwargs)
        return object()

    monkeypatch.setattr(correction_service, "start_item_processing", fake_start_item)

    correction = await correction_service.create_and_start_correction(
        db,
        item=item,
        payload=PipelineCorrectionCreate(
            restart_from_stage="translation",
            resume_mode="manual",
            reason="专有名词翻译错误",
        ),
    )

    assert item.publication_status == "withdrawn"
    assert item.withdrawal_reason == "专有名词翻译错误"
    assert started["restart_from_stage"] == "translation"
    assert started["replay_from_run_id"] is None
    assert "context" not in started
    assert started["correction_id"] == correction.id


@pytest.mark.anyio
async def test_v3_importance_correction_replays_typed_checkpoints_without_legacy_context(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db)
    analysis = {
        "title": "活动",
        "summary": "活动奖励开放领取。",
        "entities": [],
        "products": ["lol_pc"],
        "content_form": "original",
        "message_type": "game_community_notice",
        "topics": ["activities_rewards"],
        "classification_version": "message-taxonomy-v2",
    }
    run = ProcessingRun(
        raw_item_id=item.raw_item_id,
        workflow_type="item",
        graph_name="item_processing",
        status="completed",
        outcome="approved",
        current_stage="importance",
        context={
            "evidence_gate": {"decision": "process"},
            "relevance_decision": {"decision": "relevant"},
            "approved_media_extraction_ids": [],
            "approved_translation_proposal": {"translated_title": "活动"},
            "approved_message_analysis_proposal": analysis,
        },
    )
    db.add(run)
    db.commit()
    started: dict[str, object] = {}

    async def fake_start_item(_db: Session, raw_item: RawItem, **kwargs: object):
        started["raw_item_id"] = raw_item.id
        started.update(kwargs)
        return object()

    monkeypatch.setattr(correction_service, "start_item_processing", fake_start_item)

    await correction_service.create_and_start_correction(
        db,
        item=item,
        payload=PipelineCorrectionCreate(
            restart_from_stage="importance",
            resume_mode="manual",
            reason="同步新的重要性政策",
        ),
    )

    assert started["replay_from_run_id"] == run.id
    assert started["restart_from_stage"] == "importance"
    assert "context" not in started


@pytest.mark.anyio
async def test_manual_publish_commits_message_and_queues_event_downstream(
    db: Session,
) -> None:
    item = _published_item(db, suffix=" manual downstream")
    correction, review = _final_manual_review(db, item)

    result = await approve_review(db, review, note="确认修正")

    assert result.status == "completed"
    assert db.get(PipelineCorrection, correction.id).status == "completed"
    job = db.scalar(
        select(PipelineJob).where(PipelineJob.raw_item_id == item.raw_item_id)
    )
    assert job is not None
    assert job.status == "queued"
    assert job.current_stage == "load_message"


@pytest.mark.anyio
async def test_manual_publish_queues_event_even_when_automatic_ingestion_is_disabled(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db, suffix=" automation disabled")
    _correction, review = _final_manual_review(db, item)
    monkeypatch.setattr(settings, "pipeline_automation_enabled", False)

    await approve_review(db, review, note="确认修正")

    job = db.scalar(
        select(PipelineJob).where(PipelineJob.raw_item_id == item.raw_item_id)
    )
    assert job is not None
    assert job.current_stage == "load_message"


@pytest.mark.anyio
async def test_pipeline_worker_processes_manual_publish_event_downstream(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db, suffix=" worker downstream")
    _correction, review = _final_manual_review(db, item)

    async def run_downstream(_db: Session, published: NormalizedItem):
        return await aggregate_normalized_item(
            _db,
            published,
            llm_client=_EmptyEventClient(),
        )

    monkeypatch.setattr(automatic_pipeline, "publish_normalized_item_downstream", run_downstream)
    await approve_review(db, review, note="确认修正")
    job = db.scalar(
        select(PipelineJob).where(PipelineJob.raw_item_id == item.raw_item_id)
    )
    assert job is not None

    await execute_pipeline_job(db, job)

    assert db.scalar(
        select(EventAggregationRun).where(
            EventAggregationRun.normalized_item_id == item.id,
        )
    ) is not None


@pytest.mark.anyio
async def test_event_failure_does_not_fail_published_message(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db, suffix=" failed downstream")
    correction, review = _final_manual_review(db, item)

    async def fail_downstream(_db: Session, _published: NormalizedItem):
        raise RuntimeError("event downstream unavailable")

    monkeypatch.setattr(automatic_pipeline, "publish_normalized_item_downstream", fail_downstream)

    await approve_review(db, review, note="确认修正")
    job = db.scalar(
        select(PipelineJob).where(PipelineJob.raw_item_id == item.raw_item_id)
    )
    assert job is not None

    with pytest.raises(RuntimeError, match="event downstream unavailable"):
        await execute_pipeline_job(db, job)

    published_run = db.scalar(select(ProcessingRun).where(ProcessingRun.correction_id == correction.id))
    completed_correction = db.get(PipelineCorrection, correction.id)
    assert published_run is not None
    assert published_run.status == "completed"
    assert completed_correction is not None
    assert completed_correction.status == "completed"
    assert completed_correction.completed_at is not None


@pytest.mark.anyio
async def test_event_job_does_not_advance_manual_processing_run(db: Session) -> None:
    item = _published_item(db, suffix=" event job manual fence")
    job = _job_for_item(
        item,
        job_type="event",
        status="queued",
        current_stage="load_message",
    )
    db.add(job)
    correction, review = _final_manual_review(db, item)
    db.commit()

    await execute_pipeline_job(db, job)

    assert job.status == "cancelled"
    assert job.error_message == "published NormalizedItem is no longer available"
    assert review.status == "pending"
    assert review.processing_run.status == "awaiting_review"
    assert db.get(PipelineCorrection, correction.id).status == "running"
    assert item.current_revision == 1


@pytest.mark.anyio
async def test_message_job_does_not_auto_approve_manual_processing_run(db: Session) -> None:
    item = _published_item(db, suffix=" message job manual fence")
    job = _job_for_item(item, status="queued", current_stage="relevance")
    db.add(job)
    _correction, review = _final_manual_review(db, item)
    db.commit()

    await execute_pipeline_job(db, job)

    assert job.status == "cancelled"
    assert job.error_message == "manual processing run owns this RawItem"
    assert review.status == "pending"
    assert review.processing_run.status == "awaiting_review"
    assert item.current_revision == 1


@pytest.mark.anyio
async def test_correction_cancels_old_event_job_and_new_publish_queues_fresh_job(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db, suffix=" event job supersession")
    old_job = _job_for_item(
        item,
        job_type="event",
        status="failed",
        current_stage="load_message",
        next_attempt_at=datetime.now(UTC) + timedelta(minutes=5),
        error_message="event downstream unavailable",
        completed_at=datetime.now(UTC),
    )
    db.add(old_job)
    db.commit()

    async def fake_start_item(_db: Session, _raw_item: RawItem, **_kwargs: object):
        return object()

    monkeypatch.setattr(correction_service, "start_item_processing", fake_start_item)
    correction = await correction_service.create_and_start_correction(
        db,
        item=item,
        payload=PipelineCorrectionCreate(
            restart_from_stage="translation",
            resume_mode="manual",
            reason="修正并重新发布",
        ),
    )
    assert old_job.status == "cancelled"
    assert old_job.error_message == "superseded by message correction"
    assert old_job.next_attempt_at is None
    assert old_job.worker_id is None
    assert old_job.lease_token is None
    assert old_job.lease_expires_at is None
    assert old_job.heartbeat_at is None

    correction, review = _final_manual_review(db, item, correction=correction)
    await approve_review(db, review, note="确认修正")

    jobs = list(
        db.scalars(
            select(PipelineJob)
            .where(PipelineJob.raw_item_id == item.raw_item_id)
            .order_by(PipelineJob.id)
        )
    )
    assert len(jobs) == 2
    assert jobs[0].id == old_job.id
    new_job = jobs[-1]
    assert new_job.id != old_job.id
    assert new_job.status == "queued"
    assert new_job.current_stage == "load_message"
    assert item.current_revision == 2

    async def run_downstream(_db: Session, published: NormalizedItem):
        return await aggregate_normalized_item(
            _db,
            published,
            llm_client=_EmptyEventClient(),
        )

    monkeypatch.setattr(automatic_pipeline, "publish_normalized_item_downstream", run_downstream)
    await execute_pipeline_job(db, new_job)

    event_run = db.scalar(
        select(EventAggregationRun).where(
            EventAggregationRun.normalized_item_id == item.id,
            EventAggregationRun.normalized_item_revision == 2,
        )
    )
    assert event_run is not None
    assert event_run.status == "completed"


@pytest.mark.anyio
async def test_failed_event_job_recovery_requeues_without_message_correction(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db, suffix=" event retry")
    job = _job_for_item(
        item,
        job_type="event",
        status="failed",
        current_stage="load_message",
        error_message="event downstream unavailable",
        completed_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()

    async def run_downstream_fail(_db: Session, _published: NormalizedItem):
        raise RuntimeError("event downstream unavailable")

    monkeypatch.setattr(automatic_pipeline, "publish_normalized_item_downstream", run_downstream_fail)
    with pytest.raises(RuntimeError, match="event downstream unavailable"):
        await execute_pipeline_job(db, job)
    job.status = "failed"
    job.error_message = "event downstream unavailable"
    job.completed_at = datetime.now(UTC)
    db.commit()

    recovered = await recover_failed_job(
        db,
        job_id=job.id,
        payload=PipelineCorrectionCreate(
            restart_from_stage="importance",
            resume_mode="automatic",
            reason="重试事件聚合",
        ),
    )

    assert recovered.id == job.id
    assert job.status == "queued"
    assert item.publication_status == "published"
    assert item.current_revision == 1
    assert db.scalar(select(PipelineCorrection)) is None
    assert db.scalar(select(ProcessingRun)) is None

    async def run_downstream(_db: Session, published: NormalizedItem):
        return await aggregate_normalized_item(
            _db,
            published,
            llm_client=_EmptyEventClient(),
        )

    monkeypatch.setattr(automatic_pipeline, "publish_normalized_item_downstream", run_downstream)
    await execute_pipeline_job(db, job)
    event_run = db.scalar(select(EventAggregationRun))
    assert event_run is not None
    assert event_run.status == "completed"
    assert item.publication_status == "published"


@pytest.mark.anyio
async def test_manual_recover_resets_exhausted_event_job_attempts(db: Session) -> None:
    item = _published_item(db, suffix=" exhausted event retry")
    job = _job_for_item(
        item,
        job_type="event",
        status="failed",
        current_stage="load_message",
        attempts=automatic_pipeline.settings.pipeline_worker_max_attempts,
        error_message="event downstream unavailable",
        completed_at=datetime.now(UTC),
    )
    db.add(job)
    db.commit()

    recovered = await recover_failed_job(
        db,
        job_id=job.id,
        payload=PipelineCorrectionCreate(
            restart_from_stage="importance",
            resume_mode="manual",
            reason="人工重试事件聚合",
        ),
    )

    assert recovered.id == job.id
    assert job.status == "queued"
    assert job.attempts == 0

    claimed = _claim_next_job(db, worker_id="manual-recovery-test")
    assert claimed is not None
    assert claimed.id == job.id
    assert claimed.status == "running"
    assert claimed.attempts == 1


@pytest.mark.anyio
async def test_manual_recover_rejects_job_already_scheduled_for_retry(db: Session) -> None:
    item = _published_item(db, suffix=" manual retry conflict")
    next_attempt_at = datetime.now(UTC) + timedelta(minutes=5)
    job = _job_for_item(
        item,
        job_type="event",
        status="failed",
        current_stage="load_message",
        next_attempt_at=next_attempt_at,
        error_message="temporary downstream failure",
    )
    db.add(job)
    db.commit()

    with pytest.raises(ValueError, match="already scheduled for automatic retry"):
        await recover_failed_job(
            db,
            job_id=job.id,
            payload=PipelineCorrectionCreate(
                restart_from_stage="importance",
                resume_mode="manual",
                reason="人工重试",
            ),
        )

    db.refresh(job)
    assert job.status == "failed"
    assert job.next_attempt_at.replace(tzinfo=UTC) == next_attempt_at
    assert db.scalar(select(PipelineCorrection)) is None


@pytest.mark.anyio
async def test_event_recovery_cancels_job_when_publication_is_withdrawn(db: Session) -> None:
    item = _published_item(db, suffix=" withdrawn event retry")
    item.publication_status = "withdrawn"
    job = _job_for_item(
        item,
        job_type="event",
        status="failed",
        current_stage="load_message",
    )
    db.add(job)
    db.commit()

    recovered = await recover_failed_job(
        db,
        job_id=job.id,
        payload=PipelineCorrectionCreate(
            restart_from_stage="importance",
            resume_mode="automatic",
            reason="重试事件聚合",
        ),
    )

    assert recovered.id == job.id
    assert job.status == "cancelled"
    assert job.error_message == "published NormalizedItem is no longer current"
    assert db.scalar(select(PipelineCorrection)) is None


@pytest.mark.anyio
async def test_event_recovery_cancels_old_job_when_active_job_exists(db: Session) -> None:
    item = _published_item(db, suffix=" active event retry")
    old_event_job = _job_for_item(
        item,
        job_type="event",
        status="failed",
        current_stage="load_message",
    )
    current_event_job = _job_for_item(
        item,
        job_type="event",
        status="queued",
        current_stage="load_message",
    )
    db.add_all([old_event_job, current_event_job])
    db.commit()

    recovered = await recover_failed_job(
        db,
        job_id=old_event_job.id,
        payload=PipelineCorrectionCreate(
            restart_from_stage="importance",
            resume_mode="automatic",
            reason="重试事件聚合",
        ),
    )

    assert recovered.id == old_event_job.id
    assert old_event_job.status == "cancelled"
    assert old_event_job.error_message == "superseded by active pipeline job"
    assert old_event_job.completed_at is not None
    assert current_event_job.status == "queued"
    assert db.scalar(select(PipelineCorrection)) is None
    assert item.publication_status == "published"


@pytest.mark.anyio
async def test_event_recovery_keeps_different_execution_identity_active_job(
    db: Session,
) -> None:
    item = _published_item(db, suffix=" different event identity")
    item.current_revision = 2
    old_event_job = PipelineJob(
        raw_item_id=item.raw_item_id,
        job_type="event",
        target_entity_type="normalized_item",
        target_entity_id=item.id,
        target_revision=1,
        workflow_name="event_aggregation",
        status="failed",
        current_stage="load_message",
    )
    current_event_job = PipelineJob(
        raw_item_id=item.raw_item_id,
        job_type="event",
        target_entity_type="normalized_item",
        target_entity_id=item.id,
        target_revision=2,
        workflow_name="event_aggregation",
        status="queued",
        current_stage="load_message",
    )
    db.add_all([old_event_job, current_event_job])
    db.commit()

    recovered = await recover_failed_job(
        db,
        job_id=old_event_job.id,
        payload=PipelineCorrectionCreate(
            restart_from_stage="importance",
            resume_mode="automatic",
            reason="按目标修订重试事件聚合",
        ),
    )

    assert recovered.id == old_event_job.id
    assert old_event_job.status == "queued"
    assert current_event_job.status == "queued"


@pytest.mark.anyio
async def test_message_job_recovery_still_creates_message_correction(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = Source(name="Message recovery source", connector_type="manual")
    db.add(source)
    db.flush()
    raw = RawItem(
        source_id=source.id,
        native_title="消息重试",
        content_blocks=[{"type": "paragraph", "text": "消息重试"}],
    )
    db.add(raw)
    db.flush()
    job = _job_for_raw(raw, status="failed", current_stage="relevance")
    db.add(job)
    db.commit()
    started: dict[str, object] = {}

    async def fake_start_item(_db: Session, _raw: RawItem, **kwargs: object):
        started.update(kwargs)
        return object()

    monkeypatch.setattr(correction_service, "start_item_processing", fake_start_item)
    correction = await recover_failed_job(
        db,
        job_id=job.id,
        payload=PipelineCorrectionCreate(
            restart_from_stage="relevance",
            resume_mode="manual",
            reason="重试消息处理",
        ),
    )

    assert isinstance(correction, PipelineCorrection)
    assert correction.status == "running"
    assert started["restart_from_stage"] == "relevance"
    assert db.scalar(select(PipelineCorrection)) is not None


@pytest.mark.anyio
async def test_restart_from_beginning_uses_fresh_automatic_recovery(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Source(name="Restart source", connector_type="manual")
    raw = RawItem(source=source, native_title="restart", content_blocks=[{"type": "paragraph", "text": "restart"}])
    db.add(raw)
    db.flush()
    old_run = ProcessingRun(
        raw_item_id=raw.id,
        workflow_type="item",
        status="failed",
        current_stage="importance",
        execution_mode="manual",
        context={
            "approved_message_analysis_proposal": {"stale": True},
            "approved_importance_proposal": {"stale": True},
        },
    )
    db.add(old_run)
    db.commit()
    started: dict[str, object] = {}

    async def fake_start_item(_db: Session, _raw: RawItem, **kwargs: object):
        started.update(kwargs)
        return object()

    monkeypatch.setattr(correction_service, "start_item_processing", fake_start_item)
    correction = await restart_raw_item_from_beginning(db, raw_item_id=raw.id)

    assert correction.restart_from_stage == "relevance"
    assert correction.resume_mode == "automatic"
    assert correction.source_processing_run_id == old_run.id
    assert started["execution_mode"] == "automatic"
    assert started["restart_from_stage"] == "relevance"
    assert "context" not in started
    job = db.scalar(select(PipelineJob).where(PipelineJob.correction_id == correction.id))
    assert job is not None
    assert job.status == "queued"
    assert job.current_stage == "relevance"


@pytest.mark.anyio
async def test_restart_from_beginning_recovers_failed_job_automatically(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Source(name="Restart job source", connector_type="manual")
    raw = RawItem(source=source, native_title="restart", content_blocks=[{"type": "paragraph", "text": "restart"}])
    db.add(raw)
    db.flush()
    old_run = ProcessingRun(
        raw_item_id=raw.id,
        workflow_type="item",
        status="failed",
        current_stage="importance",
        execution_mode="manual",
        context={"approved_message_analysis_proposal": {"stale": True}},
    )
    failed_job = _job_for_raw(raw, status="failed", current_stage="importance")
    db.add_all([old_run, failed_job])
    db.commit()
    started: dict[str, object] = {}

    async def fake_start_item(_db: Session, _raw: RawItem, **kwargs: object):
        started.update(kwargs)
        return object()

    monkeypatch.setattr(correction_service, "start_item_processing", fake_start_item)
    correction = await restart_raw_item_from_beginning(db, raw_item_id=raw.id)

    assert isinstance(correction, PipelineCorrection)
    assert correction.restart_from_stage == "relevance"
    assert correction.resume_mode == "automatic"
    assert "context" not in started
    assert started["execution_mode"] == "automatic"
    replacement = db.scalar(
        select(PipelineJob).where(PipelineJob.correction_id == correction.id)
    )
    assert replacement is not None
    assert replacement.id != failed_job.id
    assert replacement.current_stage == "relevance"


@pytest.mark.anyio
async def test_restart_from_beginning_ignores_event_job_for_same_raw_item(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db, suffix="restart ignores event job")
    failed_run = ProcessingRun(
        raw_item_id=item.raw_item_id,
        workflow_type="item",
        graph_name="item_processing",
        status="failed",
        current_stage="importance",
        execution_mode="automatic",
    )
    event_job = _job_for_item(
        item,
        job_type="event",
        status="failed",
        current_stage="load_message",
    )
    db.add_all([failed_run, event_job])
    db.commit()

    async def fake_start_item(_db: Session, _raw: RawItem, **_kwargs: object):
        return object()

    monkeypatch.setattr(correction_service, "start_item_processing", fake_start_item)
    correction = await restart_raw_item_from_beginning(
        db,
        raw_item_id=item.raw_item_id,
    )

    assert isinstance(correction, PipelineCorrection)
    assert event_job.status == "failed"
    item_job = db.scalar(
        select(PipelineJob).where(
            PipelineJob.correction_id == correction.id,
            PipelineJob.job_type == "message",
        )
    )
    assert item_job is not None
    assert item_job.workflow_name == "item_processing"
    assert item_job.target_entity_type == "raw_item"
    assert item_job.target_entity_id == item.raw_item_id
    assert item_job.target_revision == item.raw_item.revision


def test_manual_rejection_cancels_correction(db: Session) -> None:
    item = _published_item(db, suffix=" rejected correction")
    correction, review = _final_manual_review(db, item)

    result = reject_review(
        db,
        review,
        payload=ReviewRejection(
            feedback_type="analysis_correction",
            reason="人工拒绝本次修正",
        ),
    )

    assert result.status == "rejected"
    assert db.get(PipelineCorrection, correction.id).status == "cancelled"


def test_pipeline_job_enqueue_is_idempotent_per_active_raw_item(db: Session) -> None:
    item = _published_item(db)
    first = enqueue_pipeline_job(db, raw_item_id=item.raw_item_id)
    db.flush()
    second = enqueue_pipeline_job(db, raw_item_id=item.raw_item_id)

    assert first is not None
    assert second is first
    assert len(list(db.scalars(select(PipelineJob)))) == 1
    assert db.scalar(select(PipelineCorrection)) is None


def test_pipeline_job_enqueue_reuses_retry_pending_job(db: Session) -> None:
    item = _published_item(db, suffix=" retry pending enqueue")
    retry_job = PipelineJob(
        raw_item_id=item.raw_item_id,
        target_entity_type="raw_item",
        target_entity_id=item.raw_item_id,
        target_revision=item.raw_item.revision,
        status="failed",
        current_stage="relevance",
        next_attempt_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    db.add(retry_job)
    db.commit()

    enqueued = enqueue_pipeline_job(db, raw_item_id=item.raw_item_id)

    assert enqueued is retry_job
    assert enqueued.status == "failed"
    assert enqueued.next_attempt_at is not None
    assert len(list(db.scalars(select(PipelineJob)))) == 1


def test_pipeline_job_stale_lease_is_reclaimed_with_provenance(db: Session) -> None:
    item = _published_item(db)
    job = _job_for_item(item, status="queued", max_attempts=2)
    db.add(job)
    db.commit()

    first = _claim_next_job(db, worker_id="worker-a")
    assert first is not None
    first_token = first.lease_token
    assert _claim_next_job(db, worker_id="worker-b") is None

    first.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    recovered = _claim_next_job(db, worker_id="worker-b")

    assert recovered is not None
    assert recovered.id == first.id
    assert recovered.lease_token != first_token
    assert recovered.worker_id == "worker-b"
    assert recovered.attempts == 2
    assert recovered.recovery_count == 1
    assert recovered.recovery_provenance[-1]["previous_worker_id"] == "worker-a"

    stale_guard = PipelineExecutionGuard(
        job_id=recovered.id,
        lease_token=first_token,
        lease_lost=asyncio.Event(),
    )
    with pytest.raises(PipelineLeaseLost):
        stale_guard.assert_owned(db)


@pytest.mark.anyio
async def test_heartbeat_notifies_execution_when_lease_token_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease_lost = asyncio.Event()

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr("app.services.automatic_pipeline.asyncio.sleep", no_wait)
    monkeypatch.setattr(
        "app.services.automatic_pipeline._renew_job_lease",
        lambda _job_id, _lease_token: False,
    )
    await _heartbeat_job(1, "stale-token", lease_lost)
    assert lease_lost.is_set()


@pytest.mark.anyio
async def test_pipeline_job_retries_transient_failure_and_completes(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db)
    job = _job_for_item(item, status="queued")
    db.add(job)
    db.commit()
    calls = 0

    async def flaky_execute(*_args: object, **_kwargs: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TimeoutError("temporary LLM timeout")

    monkeypatch.setattr(automatic_pipeline, "execute_pipeline_job", flaky_execute)
    monkeypatch.setattr(automatic_pipeline, "SessionLocal", lambda: nullcontext(db))

    assert await automatic_pipeline.process_next_job() is True
    db.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1
    assert job.next_attempt_at is not None
    assert job.completed_at is None
    assert job.next_attempt_at.replace(tzinfo=UTC) >= datetime.now(UTC) + timedelta(
        seconds=29
    )

    job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert await automatic_pipeline.process_next_job() is True
    db.refresh(job)
    assert job.status == "completed"
    assert job.attempts == 2
    assert job.next_attempt_at is None
    assert calls == 2


@pytest.mark.anyio
async def test_pipeline_job_failure_alert_is_sent_only_after_attempts_exhausted(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db)
    job = _job_for_item(item, status="queued", max_attempts=2)
    db.add(job)
    db.commit()
    notifications: list[dict[str, object]] = []

    async def always_fails(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("temporary upstream failure")

    def record_failure(_db: Session, **kwargs: object) -> None:
        notifications.append(kwargs)

    monkeypatch.setattr(automatic_pipeline, "execute_pipeline_job", always_fails)
    monkeypatch.setattr(automatic_pipeline, "SessionLocal", lambda: nullcontext(db))
    monkeypatch.setattr(automatic_pipeline.settings, "pipeline_worker_max_attempts", 2)
    monkeypatch.setattr(automatic_pipeline, "enqueue_pipeline_failure", record_failure)

    assert await automatic_pipeline.process_next_job() is True
    db.refresh(job)
    assert job.status == "failed"
    assert job.next_attempt_at is not None
    assert notifications == []

    job.next_attempt_at = datetime.now(UTC) - timedelta(seconds=1)
    db.commit()
    assert await automatic_pipeline.process_next_job() is True
    db.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 2
    assert job.next_attempt_at is None
    assert job.completed_at is not None
    assert len(notifications) == 1
    assert _claim_next_job(db) is None


@pytest.mark.anyio
async def test_pipeline_retry_reuses_failed_processing_run_and_checkpoint(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db)
    item.publication_status = "withdrawn"
    run = ProcessingRun(graph_name="item_processing",
        raw_item_id=item.raw_item_id,
        workflow_type="item",
        status="failed",
        current_stage="translation",
        execution_mode="automatic",
        context={"relevance_decision": {"decision": "relevant"}},
    )
    db.add(run)
    db.flush()
    checkpoint = ProcessingCheckpoint(
        raw_item_id=item.raw_item_id,
        processing_run_id=run.id,
        stage="relevance",
        output_snapshot={"decision": "relevant"},
        decision_source="automatic",
    )
    db.add(checkpoint)
    db.flush()
    job = _job_for_item(
        item,
        status="failed",
        current_stage="translation",
        processing_run_id=run.id,
        last_checkpoint_id=checkpoint.id,
        attempts=1,
        next_attempt_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    db.add(job)
    db.commit()
    resumed: dict[str, int] = {}

    async def fake_resume(_db: Session, current_run: ProcessingRun, **_kwargs: object):
        resumed["run_id"] = current_run.id
        current_run.status = "completed"
        current_run.outcome = "irrelevant"
        return current_run

    monkeypatch.setattr(automatic_pipeline, "resume_item_processing", fake_resume)

    claimed = _claim_next_job(db)
    assert claimed is not None
    await execute_pipeline_job(db, claimed)

    assert resumed == {"run_id": run.id}
    persisted_run = db.scalar(
        select(ProcessingRun).where(ProcessingRun.raw_item_id == item.raw_item_id)
    )
    assert persisted_run is not None
    assert persisted_run.id == run.id
    assert len(list(db.scalars(select(ProcessingRun)))) == 1


@pytest.mark.anyio
async def test_worker_restarts_legacy_failure_with_withdrawn_projection(
    db: Session, monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db)
    item.publication_status = "withdrawn"
    legacy = ProcessingRun(
        raw_item_id=item.raw_item_id, workflow_type="item", status="failed",
        execution_mode="automatic", graph_name=None, current_stage="translation",
    )
    db.add(legacy)
    db.flush()
    job = _job_for_item(item, status="running", processing_run_id=legacy.id)
    db.add(job)
    db.commit()
    started = []

    async def start_from_evidence(current_db, raw_item, **kwargs):
        assert kwargs["allow_existing_projection"] is True
        run = ProcessingRun(
            raw_item_id=raw_item.id, workflow_type="item", graph_name="item_processing",
            execution_mode="automatic", status="completed", outcome="irrelevant", current_stage="relevance",
        )
        current_db.add(run)
        current_db.flush()
        started.append(run.id)
        return run

    monkeypatch.setattr(automatic_pipeline, "start_item_processing", start_from_evidence)
    await execute_pipeline_job(db, job)
    assert job.processing_run_id == started[0]
    assert legacy.status == "failed"
    assert item.publication_status == "withdrawn"


@pytest.mark.anyio
async def test_worker_loop_survives_maintenance_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StopLoop(Exception):
        pass

    processed = 0

    def fail_refresh(_db: Session) -> None:
        raise RuntimeError("metrics database hiccup")

    async def fake_process_next_job() -> bool:
        nonlocal processed
        processed += 1
        return False

    async def stop_sleep(_seconds: float) -> None:
        raise StopLoop

    monkeypatch.setattr(automatic_pipeline, "refresh_stale_event_metrics", fail_refresh)
    monkeypatch.setattr(automatic_pipeline, "process_next_job", fake_process_next_job)
    monkeypatch.setattr(automatic_pipeline.asyncio, "sleep", stop_sleep)
    monkeypatch.setattr(automatic_pipeline.settings, "event_aggregation_enabled", True)

    with pytest.raises(StopLoop):
        await automatic_pipeline.worker_loop()
    assert processed == 1


@pytest.mark.anyio
async def test_automatic_job_accepts_irrelevant_decision_and_records_checkpoint(
    db: Session,
) -> None:
    item = _published_item(db)
    db.delete(item)
    db.commit()
    from langgraph.checkpoint.memory import InMemorySaver
    from sqlalchemy.orm import sessionmaker
    from app.orchestration.item_processing.service import start_item_processing
    from app.services.llm import RelevanceResult
    class IrrelevantClient:
        async def judge_relevance(self, **kwargs):
            return RelevanceResult(decision="irrelevant", confidence=0.99, reason="not relevant")
    factory = sessionmaker(db.bind, expire_on_commit=False)
    saver = InMemorySaver()
    run = await start_item_processing(db, db.get(RawItem, item.raw_item_id), execution_mode="automatic",
        defer_execution=True, session_factory=factory, checkpointer=saver, llm_factory=IrrelevantClient)
    job = _job_for_item(
        item,
        status="running",
        current_stage="relevance",
        processing_run_id=run.id,
    )
    db.add(job)
    db.commit()
    await execute_pipeline_job(db, job, session_factory=factory, checkpointer=saver, llm_factory=IrrelevantClient)
    db.expire_all()
    review = db.scalar(select(ReviewTask).where(ReviewTask.processing_run_id == run.id))
    assert run.status == "completed"
    assert run.outcome == "irrelevant"
    assert review is None  # automatic graph records checkpoints without manual tasks
    checkpoint = db.scalar(
        select(ProcessingCheckpoint).where(ProcessingCheckpoint.processing_run_id == run.id, ProcessingCheckpoint.stage == "relevance")
    )
    assert checkpoint is not None
    assert checkpoint.stage == "relevance"


@pytest.mark.anyio
async def test_automatic_job_cancels_superseded_raw_revision(db: Session) -> None:
    item = _published_item(db, suffix=" superseded")
    successor = RawItem(
        source_id=item.raw_item.source_id,
        native_title="Successor revision",
        content_blocks=[{"type": "paragraph", "text": "Successor revision"}],
        published_at=datetime(2026, 7, 28, tzinfo=UTC),
        revision=2,
        supersedes_raw_item_id=item.raw_item_id,
    )
    job = _job_for_item(item, status="running", current_stage="importance")
    db.add_all([successor, job])
    db.commit()

    await execute_pipeline_job(db, job)

    assert job.status == "cancelled"


def test_enqueue_pending_raw_items_only_enqueues_latest_revision(db: Session) -> None:
    source = Source(name="Revision queue source", connector_type="manual")
    db.add(source)
    db.flush()
    old = RawItem(
        source_id=source.id,
        external_id="revision-queue-item",
        native_title="Old revision",
        content_blocks=[{"type": "paragraph", "text": "Old revision"}],
        revision=1,
    )
    db.add(old)
    db.flush()
    successor = RawItem(
        source_id=source.id,
        external_id="revision-queue-item",
        native_title="Latest revision",
        content_blocks=[{"type": "paragraph", "text": "Latest revision"}],
        revision=2,
        supersedes_raw_item_id=old.id,
    )
    db.add(successor)
    db.commit()

    jobs = enqueue_pending_raw_items(db)

    assert [job.raw_item_id for job in jobs] == [successor.id]
