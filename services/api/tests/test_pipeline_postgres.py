import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.models.event import (
    Event,
    EventAggregationRun,
    EventMessage,
    EventReviewTask,
    EventRevision,
)
from app.models.intelligence import Claim, EventClaim
from app.models.normalized_item import NormalizedItem, NormalizedItemRevision
from app.models.pipeline import PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun, ReviewTask
from app.services.automatic_pipeline import _claim_next_job
from app.services.pipeline_execution import (
    PipelineExecutionGuard,
    PipelineLeaseLost,
)
from app.workflows.event_aggregation import approve_event_review
from app.workflows.reviewed_pipeline import approve_review, start_item_processing

pytestmark = pytest.mark.postgres


@pytest.mark.skipif(
    not os.getenv("EVENT_TEST_DATABASE_URL"),
    reason="EVENT_TEST_DATABASE_URL is not configured",
)
def test_workers_claim_one_job_once_and_manual_auto_share_active_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(os.environ["EVENT_TEST_DATABASE_URL"], pool_pre_ping=True)
    suffix = uuid4().hex
    source_id = raw_item_id = job_id = None
    try:
        with Session(engine, expire_on_commit=False) as db:
            source = Source(
                name=f"pipeline-concurrency-{suffix}",
                connector_type="manual",
            )
            db.add(source)
            db.flush()
            source_id = source.id
            raw = RawItem(
                source_id=source.id,
                external_id=suffix,
                content_blocks=[
                    {"id": "b0001", "type": "paragraph", "text": "test"}
                ],
                published_at=datetime(2026, 8, 3, tzinfo=UTC),
            )
            db.add(raw)
            db.flush()
            raw_item_id = raw.id
            job = PipelineJob(raw_item_id=raw.id, status="queued")
            db.add(job)
            db.commit()
            job_id = job.id

        barrier = Barrier(2)

        def claim(worker: str) -> int | None:
            with Session(engine, expire_on_commit=False) as db:
                barrier.wait()
                job = _claim_next_job(db, worker_id=worker)
                return job.id if job else None

        with ThreadPoolExecutor(max_workers=2) as executor:
            claims = list(executor.map(claim, ["worker-a", "worker-b"]))
        assert sorted(value for value in claims if value is not None) == [job_id]

        async def no_generation(_db: Session, run: ProcessingRun) -> None:
            return None

        monkeypatch.setattr(
            "app.workflows.reviewed_pipeline._generate_relevance_review",
            no_generation,
        )
        start_barrier = Barrier(2)

        def start(mode: str) -> int:
            with Session(engine, expire_on_commit=False) as db:
                raw = db.get(RawItem, raw_item_id)
                start_barrier.wait()
                run = asyncio.run(
                    start_item_processing(db, raw, execution_mode=mode)
                )
                return run.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            run_ids = list(executor.map(start, ["manual", "automatic"]))
        assert len(set(run_ids)) == 1
    finally:
        with Session(engine) as db:
            if raw_item_id is not None:
                db.execute(
                    delete(ProcessingRun).where(
                        ProcessingRun.raw_item_id == raw_item_id
                    )
                )
                db.execute(
                    delete(PipelineJob).where(
                        PipelineJob.raw_item_id == raw_item_id
                    )
                )
                db.execute(delete(RawItem).where(RawItem.id == raw_item_id))
            if source_id is not None:
                db.execute(delete(Source).where(Source.id == source_id))
            db.commit()
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("EVENT_TEST_DATABASE_URL"),
    reason="EVENT_TEST_DATABASE_URL is not configured",
)
def test_reclaimed_worker_fences_stale_business_writes() -> None:
    engine = create_engine(os.environ["EVENT_TEST_DATABASE_URL"], pool_pre_ping=True)
    suffix = uuid4().hex
    source_id = raw_item_id = job_id = None
    try:
        with Session(engine, expire_on_commit=False) as setup:
            source = Source(
                name=f"pipeline-fencing-{suffix}",
                connector_type="manual",
            )
            setup.add(source)
            setup.flush()
            source_id = source.id
            raw = RawItem(
                source_id=source.id,
                external_id=f"fencing-{suffix}",
                content_blocks=[
                    {
                        "id": "b0001",
                        "type": "paragraph",
                        "text": "fencing test",
                    }
                ],
                published_at=datetime(2026, 8, 3, tzinfo=UTC),
            )
            setup.add(raw)
            setup.flush()
            raw_item_id = raw.id
            setup.add(PipelineJob(raw_item_id=raw.id, status="queued"))
            setup.commit()

        with Session(engine, expire_on_commit=False) as worker_a:
            claimed_a = _claim_next_job(worker_a, worker_id="worker-a")
            assert claimed_a is not None
            job_id = claimed_a.id
            token_a = claimed_a.lease_token

        with Session(engine, expire_on_commit=False) as setup:
            expiring = setup.get(PipelineJob, job_id)
            assert expiring is not None
            expiring.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
            setup.commit()

        with Session(engine, expire_on_commit=False) as worker_b:
            claimed_b = _claim_next_job(worker_b, worker_id="worker-b")
            assert claimed_b is not None
            token_b = claimed_b.lease_token
            assert token_b != token_a
            guard_b = PipelineExecutionGuard(
                job_id=claimed_b.id,
                lease_token=token_b,
                lease_lost=asyncio.Event(),
            )
            raw = worker_b.get(RawItem, raw_item_id)
            assert raw is not None
            run = ProcessingRun(
                raw_item_id=raw.id,
                workflow_type="item",
                status="awaiting_review",
                current_stage="item_analysis",
                execution_mode="automatic",
            )
            worker_b.add(run)
            worker_b.flush()
            item_review = ReviewTask(
                processing_run_id=run.id,
                stage="item_analysis",
                status="pending",
                decision_source="automatic",
                policy_version="auto-approve-v1",
                proposal={
                    "normalized_title": "Fenced publication",
                    "normalized_text": "Fenced publication",
                    "summary": "Only worker B may publish this item.",
                    "category": "news",
                    "entities": [],
                    "importance_score": 0.5,
                    "credibility": "official",
                    "credibility_score": 1,
                    "credibility_evidence": [],
                    "analysis_model": "postgres-fencing-test",
                },
            )
            worker_b.add(item_review)
            guard_b.assert_owned(worker_b)
            worker_b.commit()
            asyncio.run(
                approve_review(
                    worker_b,
                    item_review,
                    note="worker B",
                    execution_guard=guard_b,
                )
            )
            item = worker_b.scalar(
                select(NormalizedItem).where(
                    NormalizedItem.raw_item_id == raw_item_id
                )
            )
            assert item is not None
            event_run = EventAggregationRun(
                normalized_item_id=item.id,
                status="awaiting_review",
                current_stage="event_decision",
                execution_mode="automatic",
                candidate_snapshot=[],
                decision_draft={
                    "decision": "create",
                    "reason": "new event",
                    "title": "Fenced event",
                    "summary": "Only worker B may create this event.",
                    "category": "news",
                },
            )
            worker_b.add(event_run)
            worker_b.flush()
            event_review = EventReviewTask(
                event_aggregation_run_id=event_run.id,
                status="pending",
                decision_source="automatic",
                policy_version="auto-approve-v1",
                proposal={"decision": event_run.decision_draft},
            )
            worker_b.add(event_review)
            guard_b.assert_owned(worker_b)
            worker_b.commit()
            approve_event_review(
                worker_b,
                event_review,
                note="worker B",
                execution_guard=guard_b,
            )
            guard_b.assert_owned(worker_b)
            claimed_b.status = "completed"
            claimed_b.error_message = "worker-b-final-state"
            claimed_b.completed_at = datetime.now(UTC)
            claimed_b.lease_token = None
            claimed_b.lease_expires_at = None
            claimed_b.worker_id = None
            worker_b.commit()

        with Session(engine, expire_on_commit=False) as stale_worker_a:
            stale_job = stale_worker_a.get(PipelineJob, job_id)
            assert stale_job is not None
            stale_job.status = "failed"
            stale_job.error_message = "worker-a-must-not-commit"
            stale_worker_a.add(
                ProcessingCheckpoint(
                    raw_item_id=raw_item_id,
                    stage="event_decision",
                    output_snapshot={"worker": "a"},
                    artifact_references={},
                    knowledge_snapshot={},
                    decision_source="automatic",
                )
            )
            guard_a = PipelineExecutionGuard(
                job_id=job_id,
                lease_token=token_a,
                lease_lost=asyncio.Event(),
            )
            with pytest.raises(PipelineLeaseLost):
                guard_a.assert_owned(stale_worker_a)
            stale_worker_a.rollback()

        with Session(engine) as verify:
            job = verify.get(PipelineJob, job_id)
            assert job is not None
            assert job.status == "completed"
            assert job.error_message == "worker-b-final-state"
            assert verify.scalar(
                select(func.count(NormalizedItem.id)).where(
                    NormalizedItem.raw_item_id == raw_item_id
                )
            ) == 1
            item_id = verify.scalar(
                select(NormalizedItem.id).where(
                    NormalizedItem.raw_item_id == raw_item_id
                )
            )
            event_id = verify.scalar(
                select(EventMessage.event_id).where(
                    EventMessage.normalized_item_id == item_id
                )
            )
            assert event_id is not None
            assert verify.scalar(
                select(func.count(Event.id)).where(Event.id == event_id)
            ) == 1
            assert verify.scalar(
                select(func.count(EventMessage.event_id)).where(
                    EventMessage.normalized_item_id == item_id
                )
            ) == 1
            assert verify.scalar(
                select(func.count(EventRevision.id)).where(
                    EventRevision.event_id == event_id
                )
            ) == 1
            assert verify.scalar(
                select(func.count(ProcessingCheckpoint.id)).where(
                    ProcessingCheckpoint.raw_item_id == raw_item_id,
                    ProcessingCheckpoint.stage == "item_analysis",
                )
            ) == 1
            assert verify.scalar(
                select(func.count(ProcessingCheckpoint.id)).where(
                    ProcessingCheckpoint.raw_item_id == raw_item_id,
                    ProcessingCheckpoint.stage == "event_decision",
                )
            ) == 1
    finally:
        with Session(engine) as cleanup:
            if raw_item_id is not None:
                item_ids = list(
                    cleanup.scalars(
                        select(NormalizedItem.id).where(
                            NormalizedItem.raw_item_id == raw_item_id
                        )
                    )
                )
                event_ids = list(
                    cleanup.scalars(
                        select(EventMessage.event_id).where(
                            EventMessage.normalized_item_id.in_(item_ids)
                        )
                    )
                )
                claim_ids = select(Claim.id).where(
                    Claim.normalized_item_id.in_(item_ids)
                )
                cleanup.execute(
                    delete(EventClaim).where(EventClaim.claim_id.in_(claim_ids))
                )
                cleanup.execute(
                    delete(EventRevision).where(
                        EventRevision.event_id.in_(event_ids)
                    )
                )
                cleanup.execute(
                    delete(EventMessage).where(
                        EventMessage.normalized_item_id.in_(item_ids)
                    )
                )
                cleanup.execute(delete(Event).where(Event.id.in_(event_ids)))
                cleanup.execute(
                    delete(ProcessingCheckpoint).where(
                        ProcessingCheckpoint.raw_item_id == raw_item_id
                    )
                )
                cleanup.execute(
                    delete(EventReviewTask).where(
                        EventReviewTask.event_aggregation_run_id.in_(
                            select(EventAggregationRun.id).where(
                                EventAggregationRun.normalized_item_id.in_(
                                    item_ids
                                )
                            )
                        )
                    )
                )
                cleanup.execute(
                    delete(EventAggregationRun).where(
                        EventAggregationRun.normalized_item_id.in_(item_ids)
                    )
                )
                cleanup.execute(
                    delete(NormalizedItemRevision).where(
                        NormalizedItemRevision.normalized_item_id.in_(item_ids)
                    )
                )
                cleanup.execute(
                    delete(Claim).where(Claim.normalized_item_id.in_(item_ids))
                )
                cleanup.execute(delete(NormalizedItem).where(NormalizedItem.id.in_(item_ids)))
                cleanup.execute(
                    delete(ReviewTask).where(
                        ReviewTask.processing_run_id.in_(
                            select(ProcessingRun.id).where(
                                ProcessingRun.raw_item_id == raw_item_id
                            )
                        )
                    )
                )
                cleanup.execute(
                    delete(ProcessingRun).where(
                        ProcessingRun.raw_item_id == raw_item_id
                    )
                )
                cleanup.execute(
                    delete(PipelineJob).where(
                        PipelineJob.raw_item_id == raw_item_id
                    )
                )
                cleanup.execute(delete(RawItem).where(RawItem.id == raw_item_id))
            if source_id is not None:
                cleanup.execute(delete(Source).where(Source.id == source_id))
            cleanup.commit()
        engine.dispose()
