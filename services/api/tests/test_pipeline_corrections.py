import asyncio
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
from app.services.pipeline_corrections import recover_failed_job
from app.services.pipeline_execution import PipelineExecutionGuard, PipelineLeaseLost
from app.workflows.reviewed_pipeline import approve_review, reject_review
from app.workflows.event_aggregation import aggregate_normalized_item


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
async def test_translation_correction_hides_message_and_restores_context(
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
    assert started["context"] == {"approved_media_extraction_ids": [11]}
    assert started["correction_id"] == correction.id


@pytest.mark.anyio
async def test_importance_correction_preserves_new_analysis_context(
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

    resumed = started["context"]
    assert isinstance(resumed, dict)
    assert resumed["relevance_decision"] == {"decision": "relevant"}
    assert resumed["approved_message_analysis_proposal"] == analysis
    assert "approved_fact_proposal" not in resumed
    assert "approved_classification_proposal" not in resumed


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
    assert job.current_stage == "event_aggregation"


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
    assert job.current_stage == "event_aggregation"


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
    job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="queued",
        current_stage="event_aggregation",
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
    job = PipelineJob(raw_item_id=item.raw_item_id, status="queued", current_stage="relevance")
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
    old_job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="running",
        current_stage="event_aggregation",
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
    assert new_job.current_stage == "event_aggregation"
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
    job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="failed",
        current_stage="event_aggregation",
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
async def test_event_recovery_cancels_job_when_publication_is_withdrawn(db: Session) -> None:
    item = _published_item(db, suffix=" withdrawn event retry")
    item.publication_status = "withdrawn"
    job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="failed",
        current_stage="event_aggregation",
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
    old_event_job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="failed",
        current_stage="event_aggregation",
    )
    current_event_job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="queued",
        current_stage="event_aggregation",
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
    job = PipelineJob(
        raw_item_id=raw.id,
        status="failed",
        current_stage="relevance",
    )
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


def test_pipeline_job_stale_lease_is_reclaimed_with_provenance(db: Session) -> None:
    item = _published_item(db)
    job = PipelineJob(raw_item_id=item.raw_item_id, status="queued")
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
async def test_automatic_job_accepts_irrelevant_decision_and_records_checkpoint(
    db: Session,
) -> None:
    item = _published_item(db)
    db.delete(item)
    db.commit()
    run = ProcessingRun(
        raw_item_id=item.raw_item_id,
        workflow_type="item",
        status="awaiting_review",
        current_stage="relevance",
        execution_mode="automatic",
    )
    db.add(run)
    db.flush()
    review = ReviewTask(
        processing_run_id=run.id,
        stage="relevance",
        status="pending",
        proposal={"decision": "irrelevant", "confidence": 0.99, "reason": "not relevant"},
    )
    job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="running",
        current_stage="relevance",
    )
    db.add_all([review, job])
    db.commit()

    await execute_pipeline_job(db, job)

    assert run.status == "completed"
    assert run.outcome == "irrelevant"
    assert review.status == "approved"
    assert review.decision_source == "automatic"
    checkpoint = db.scalar(
        select(ProcessingCheckpoint).where(ProcessingCheckpoint.processing_run_id == run.id)
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
    job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="running",
        current_stage="importance",
    )
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
