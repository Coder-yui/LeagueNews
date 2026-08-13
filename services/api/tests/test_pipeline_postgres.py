import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, delete, update
from sqlalchemy.orm import Session

from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun
from app.models.collection_schedule import SourceCollectionSchedule
from app.services.automatic_pipeline import _claim_next_job
import app.services.collection_scheduler as scheduler_service
from app.services.collection_scheduler import execute_claimed_schedule
from app.models.connector_run import ConnectorRun
from app.workflows.reviewed_pipeline import start_item_processing

pytestmark = pytest.mark.postgres


@pytest.mark.skipif(
    not os.getenv("PIPELINE_TEST_DATABASE_URL"),
    reason="PIPELINE_TEST_DATABASE_URL is not configured",
)
def test_workers_claim_one_job_once_and_manual_auto_share_active_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(os.environ["PIPELINE_TEST_DATABASE_URL"], pool_pre_ping=True)
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
                content_blocks=[{"id": "b0001", "type": "paragraph", "text": "test"}],
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
            "app.workflows.reviewed_pipeline._evaluate_relevance",
            no_generation,
        )
        start_barrier = Barrier(2)

        def start(mode: str) -> int:
            with Session(engine, expire_on_commit=False) as db:
                raw = db.get(RawItem, raw_item_id)
                start_barrier.wait()
                run = asyncio.run(start_item_processing(db, raw, execution_mode=mode))
                return run.id

        with ThreadPoolExecutor(max_workers=2) as executor:
            run_ids = list(executor.map(start, ["manual", "automatic"]))
        assert len(set(run_ids)) == 1
    finally:
        with Session(engine) as db:
            if raw_item_id is not None:
                db.execute(delete(ProcessingRun).where(ProcessingRun.raw_item_id == raw_item_id))
                db.execute(delete(PipelineJob).where(PipelineJob.raw_item_id == raw_item_id))
                db.execute(delete(RawItem).where(RawItem.id == raw_item_id))
            if source_id is not None:
                db.execute(delete(Source).where(Source.id == source_id))
            db.commit()
        engine.dispose()


@pytest.mark.skipif(
    not os.getenv("PIPELINE_TEST_DATABASE_URL"),
    reason="PIPELINE_TEST_DATABASE_URL is not configured",
)
def test_collection_lost_lease_cannot_overwrite_new_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(os.environ["PIPELINE_TEST_DATABASE_URL"], pool_pre_ping=True)
    suffix = uuid4().hex
    source_id = schedule_id = run_id = None
    old_token = "old-owner-token"
    new_token = "new-owner-token"
    try:
        with Session(engine) as db:
            source = Source(
                name=f"collection-lease-{suffix}",
                connector_type="riot_official",
            )
            db.add(source)
            db.flush()
            source_id = source.id
            schedule = SourceCollectionSchedule(
                source_id=source.id,
                enabled=True,
                last_status="running",
                lease_token=old_token,
                lease_expires_at=datetime.now(UTC) + timedelta(minutes=30),
            )
            db.add(schedule)
            db.commit()
            schedule_id = schedule.id

        async def fake_run_connector(session: Session, **_: object) -> ConnectorRun:
            run = ConnectorRun(
                source_id=source_id,
                connector_type="riot_official",
                status="completed",
                finished_at=datetime.now(UTC),
            )
            session.add(run)
            session.commit()
            nonlocal run_id
            run_id = run.id
            session.execute(
                update(SourceCollectionSchedule)
                .where(SourceCollectionSchedule.id == schedule_id)
                .values(
                    lease_token=new_token,
                    lease_expires_at=datetime.now(UTC) + timedelta(minutes=30),
                )
            )
            session.commit()
            return run

        monkeypatch.setattr(scheduler_service, "run_connector", fake_run_connector)
        with Session(engine, expire_on_commit=False) as db:
            asyncio.run(
                execute_claimed_schedule(
                    db,
                    schedule_id=schedule_id,
                    lease_token=old_token,
                )
            )

        with Session(engine) as db:
            schedule = db.get(SourceCollectionSchedule, schedule_id)
            assert schedule is not None
            assert schedule.lease_token == new_token
            assert schedule.last_status == "running"
    finally:
        with Session(engine) as db:
            if schedule_id is not None:
                db.execute(
                    delete(SourceCollectionSchedule).where(
                        SourceCollectionSchedule.id == schedule_id
                    )
                )
            if run_id is not None:
                db.execute(delete(ConnectorRun).where(ConnectorRun.id == run_id))
            if source_id is not None:
                db.execute(delete(Source).where(Source.id == source_id))
            db.commit()
        engine.dispose()
