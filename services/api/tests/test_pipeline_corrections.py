from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
import app.services.pipeline_corrections as correction_service
from app.core.database import Base
from app.models.event import EventAggregationRun, EventMessage, EventReviewTask
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun, ReviewTask
from app.schemas.pipeline import PipelineCorrectionCreate
from app.services.automatic_pipeline import (
    _claim_next_job,
    enqueue_pipeline_job,
    execute_pipeline_job,
)
from app.services.event_aggregation import create_event


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def _published_item(db: Session) -> NormalizedItem:
    source = Source(name="Correction Source", connector_type="manual")
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
        category="news",
        entities=[],
        importance_score=0.5,
        credibility="official",
        credibility_score=1,
        credibility_evidence=[],
        target_language="zh-CN",
        translated_content_blocks=[],
        translation_status="not_required",
        analysis_model="test",
    )
    db.add(item)
    db.commit()
    return item


@pytest.mark.anyio
async def test_event_only_correction_withdraws_membership_but_keeps_message_published(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = _published_item(db)
    event = create_event(
        db,
        normalized_item_id=item.id,
        title="Existing event",
        summary="Existing event summary",
        category="news",
    )
    started: dict[str, object] = {}

    async def fake_start_event(_db: Session, started_item: NormalizedItem, **kwargs: object):
        started["item_id"] = started_item.id
        started.update(kwargs)
        return object()

    monkeypatch.setattr(correction_service, "start_event_aggregation", fake_start_event)

    correction = await correction_service.create_and_start_correction(
        db,
        item=item,
        payload=PipelineCorrectionCreate(
            restart_from_stage="event_decision",
            resume_mode="manual",
            reason="事件归属不正确",
        ),
    )

    membership = db.scalar(
        select(EventMessage).where(EventMessage.normalized_item_id == item.id)
    )
    assert item.publication_status == "published"
    assert membership is not None
    assert membership.membership_status == "withdrawn"
    assert membership.source_correction_id == correction.id
    assert event.status == "withdrawn"
    assert started == {
        "item_id": item.id,
        "supersedes_run_id": None,
        "execution_mode": "manual",
        "correction_id": correction.id,
    }


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
        current_stage="item_analysis",
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


def test_pipeline_job_enqueue_is_idempotent_per_active_raw_item(db: Session) -> None:
    item = _published_item(db)
    first = enqueue_pipeline_job(db, raw_item_id=item.raw_item_id)
    db.flush()
    second = enqueue_pipeline_job(db, raw_item_id=item.raw_item_id)

    assert first is not None
    assert second is first
    assert len(list(db.scalars(select(PipelineJob)))) == 1
    assert db.scalar(select(PipelineCorrection)) is None


def test_pipeline_job_stale_lease_is_reclaimed_with_provenance(
    db: Session,
) -> None:
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


@pytest.mark.anyio
async def test_automatic_job_accepts_relevance_and_records_checkpoint(
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
        proposal={"is_lol_relevant": False, "reason": "not relevant"},
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
        select(ProcessingCheckpoint).where(
            ProcessingCheckpoint.processing_run_id == run.id
        )
    )
    assert checkpoint is not None
    assert checkpoint.stage == "relevance"
    assert checkpoint.decision_source == "automatic"


@pytest.mark.anyio
async def test_automatic_job_completes_not_event_decision(db: Session) -> None:
    item = _published_item(db)
    event_run = EventAggregationRun(
        normalized_item_id=item.id,
        status="awaiting_review",
        current_stage="event_decision",
        execution_mode="automatic",
        candidate_snapshot=[],
        decision_draft={
            "decision": "not_event",
            "reason": "single message is not an evolving event",
        },
    )
    db.add(event_run)
    db.flush()
    review = EventReviewTask(
        event_aggregation_run_id=event_run.id,
        status="pending",
        proposal={"decision": event_run.decision_draft},
    )
    job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="running",
        current_stage="event_decision",
    )
    db.add_all([review, job])
    db.commit()

    await execute_pipeline_job(db, job)

    assert event_run.status == "completed"
    assert event_run.outcome == "not_event"
    assert review.status == "approved"
    assert review.decision_source == "automatic"
    checkpoint = db.scalar(
        select(ProcessingCheckpoint).where(
            ProcessingCheckpoint.event_aggregation_run_id == event_run.id
        )
    )
    assert checkpoint is not None
    assert checkpoint.stage == "event_decision"
