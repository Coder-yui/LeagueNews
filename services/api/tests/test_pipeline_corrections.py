import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
import app.services.pipeline_corrections as correction_service
from app.api.routes.mcp import _call_tool
from app.core.database import Base
from app.models.event import EventAggregationRun, EventMessage, EventReviewTask
from app.models.intelligence import EventClaim
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun, ReviewTask
from app.schemas.pipeline import PipelineCorrectionCreate
from app.services.automatic_pipeline import (
    _claim_next_job,
    _heartbeat_job,
    enqueue_pending_raw_items,
    enqueue_pipeline_job,
    execute_pipeline_job,
)
from app.services.event_aggregation import create_event
from app.services.event_aggregation import add_message_to_event
from app.services.claims import extract_traceable_claim
from app.services.pipeline_execution import (
    PipelineExecutionGuard,
    PipelineLeaseLost,
)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session


def _published_item(
    db: Session,
    *,
    suffix: str = "",
) -> NormalizedItem:
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
        category="news",
        entities=[],
        importance_score=0.5,
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
    claim = extract_traceable_claim(db, item)
    db.commit()
    event = create_event(
        db,
        normalized_item_id=item.id,
        title="Existing event",
        summary="Existing event summary",
        category="news",
    )
    other_event = create_event(
        db,
        normalized_item_id=item.id,
        title="Other event",
        summary="The claim is independently relevant here.",
        category="news",
        membership_role="component",
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

    memberships = list(db.scalars(
        select(EventMessage).where(EventMessage.normalized_item_id == item.id)
    ))
    assert item.publication_status == "published"
    assert len(memberships) == 2
    assert all(membership.membership_status == "withdrawn" for membership in memberships)
    assert all(membership.source_correction_id == correction.id for membership in memberships)
    assert set(correction.original_event_ids) == {event.id, other_event.id}
    assert event.status == "withdrawn"
    assert claim.status == "active"
    assert db.get(EventClaim, (event.id, claim.id)) is None
    assert db.get(EventClaim, (other_event.id, claim.id)) is None
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


@pytest.mark.anyio
async def test_failed_non_event_correction_keeps_membership_and_claim_projection_consistent(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = _published_item(db)
    target_claim = extract_traceable_claim(db, target)
    survivor = _published_item(db, suffix=" survivor")
    survivor_claim = extract_traceable_claim(db, survivor)
    db.commit()
    event = create_event(
        db,
        normalized_item_id=target.id,
        title="Correction event",
        summary="Two members keep the event visible.",
        category="news",
    )
    add_message_to_event(
        db,
        event_id=event.id,
        normalized_item_id=survivor.id,
    )

    async def fail_start(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("controlled restart failure")

    monkeypatch.setattr(correction_service, "start_item_processing", fail_start)

    with pytest.raises(RuntimeError, match="controlled restart failure"):
        await correction_service.create_and_start_correction(
            db,
            item=target,
            payload=PipelineCorrectionCreate(
                restart_from_stage="relevance",
                resume_mode="manual",
                reason="事实需要重跑",
            ),
        )

    db.refresh(target)
    db.refresh(target_claim)
    assert target.publication_status == "withdrawn"
    assert target_claim.status == "withdrawn"
    assert db.get(EventClaim, (event.id, target_claim.id)) is None
    assert db.get(EventClaim, (event.id, survivor_claim.id)) is not None
    timeline = _call_tool(db, "get_event_timeline", {"event_id": event.id})
    assert {
        claim_payload["normalized_item_id"]
        for claim_payload in timeline["claims"]
    } == {survivor.id}


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

    stale_guard = PipelineExecutionGuard(
        job_id=recovered.id,
        lease_token=first_token,
        lease_lost=asyncio.Event(),
    )
    with pytest.raises(PipelineLeaseLost):
        stale_guard.assert_owned(db)
    db.rollback()
    db.refresh(recovered)
    assert recovered.worker_id == "worker-b"
    assert recovered.status == "running"


@pytest.mark.anyio
async def test_heartbeat_notifies_execution_when_lease_token_is_lost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease_lost = asyncio.Event()

    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr(
        "app.services.automatic_pipeline.asyncio.sleep",
        no_wait,
    )
    monkeypatch.setattr(
        "app.services.automatic_pipeline._renew_job_lease",
        lambda _job_id, _lease_token: False,
    )
    await _heartbeat_job(1, "stale-token", lease_lost)
    assert lease_lost.is_set()


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
async def test_automatic_job_skips_event_projection_for_superseded_revision(
    db: Session,
) -> None:
    item = _published_item(db, suffix=" superseded")
    successor = RawItem(
        source_id=item.raw_item.source_id,
        native_title="Successor revision",
        content_blocks=[
            {"type": "paragraph", "text": "Successor revision"}
        ],
        published_at=datetime(2026, 7, 28, tzinfo=UTC),
        revision=2,
        supersedes_raw_item_id=item.raw_item_id,
    )
    job = PipelineJob(
        raw_item_id=item.raw_item_id,
        status="running",
        current_stage="event_decision",
    )
    db.add_all([successor, job])
    db.commit()

    await execute_pipeline_job(db, job)

    assert job.status == "cancelled"
    assert db.scalar(
        select(EventAggregationRun).where(
            EventAggregationRun.normalized_item_id == item.id
        )
    ) is None


def test_enqueue_pending_raw_items_only_enqueues_latest_revision(
    db: Session,
) -> None:
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
    assert db.scalar(
        select(PipelineJob).where(PipelineJob.raw_item_id == old.id)
    ) is None


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
