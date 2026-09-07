"""Opt-in Phase 2 acceptance checks against disposable PostgreSQL.

These tests intentionally use fixtures/mocks for the model call and require a
caller-provided disposable database.  They never fall back to the developer
database.
"""

import asyncio
import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.models.pipeline import PipelineJob, ProcessingCheckpoint
from app.models.event import EventAggregationRun
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ReviewTask
from app.orchestration.checkpointing import open_postgres_checkpointer
from app.orchestration.item_processing.service import approve_review, start_item_processing
from app.services.llm import (
    MessageClassificationImportanceResult,
    MessageContentAnalysisResult,
    RelevanceResult,
)
from app.schemas.event_aggregation import EventAggregationResult
from app.services.automatic_pipeline import execute_pipeline_job


pytestmark = pytest.mark.postgres


class AcceptanceLLM:
    async def judge_relevance(self, **_kwargs: object) -> RelevanceResult:
        return RelevanceResult(decision="relevant", confidence=0.99, reason="fixture")

    async def analyze_message_content(
        self, **_kwargs: object
    ) -> MessageContentAnalysisResult:
        return MessageContentAnalysisResult(
            title="PostgreSQL fixture",
            summary="fixture summary",
            entities=[],
            products=["lol_pc"],
            content_form="original",
        )

    async def classify_and_score_importance(
        self, **_kwargs: object
    ) -> MessageClassificationImportanceResult:
        return MessageClassificationImportanceResult(
            message_type="game_announcement",
            topics=["balance_gameplay"],
            scale="major",
            audience_region="global",
            competition_region="none",
            prominence="normal",
            skin_tier="none",
            is_bulk_update=True,
            evidence=["fixture"],
        )

    async def aggregate_events(self, **_kwargs: object) -> EventAggregationResult:
        return EventAggregationResult(mentions=[])


@pytest.mark.skipif(
    not os.getenv("PIPELINE_TEST_DATABASE_URL"),
    reason="PIPELINE_TEST_DATABASE_URL is not configured",
)
def test_postgres_checkpointer_manual_flow_and_independent_event_job() -> None:
    database_url = os.environ["PIPELINE_TEST_DATABASE_URL"]
    engine = create_engine(database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    suffix = os.urandom(8).hex()
    llm = AcceptanceLLM()

    async def run_flow(raw_item_id: int) -> tuple[int, int]:
        async with open_postgres_checkpointer(database_url) as saver:
            with factory() as db:
                raw_item = db.get(RawItem, raw_item_id)
                assert raw_item is not None
                run = await start_item_processing(
                    db,
                    raw_item,
                    execution_mode="manual",
                    session_factory=factory,
                    llm_factory=lambda: llm,
                    checkpointer=saver,
                )
                while run.status == "awaiting_review":
                    review = db.scalar(
                        select(ReviewTask).where(
                            ReviewTask.processing_run_id == run.id,
                            ReviewTask.status == "pending",
                        )
                    )
                    assert review is not None
                    run = await approve_review(
                        db,
                        review,
                        note="fixture approval",
                        session_factory=factory,
                        llm_factory=lambda: llm,
                        checkpointer=saver,
                    )
                assert run.status == "completed"
                checkpoint_count = len(
                    list(
                        db.scalars(
                            select(ProcessingCheckpoint).where(
                                ProcessingCheckpoint.processing_run_id == run.id
                            )
                        )
                    )
                )
                return run.id, checkpoint_count

    try:
        with Session(engine, expire_on_commit=False) as db:
            source = Source(
                name=f"phase2-postgres-{suffix}", connector_type="manual"
            )
            db.add(source)
            db.flush()
            raw_item = RawItem(
                source_id=source.id,
                external_id=f"phase2-{suffix}",
                language="zh-CN",
                content_blocks=[{"type": "paragraph", "text": "测试公告"}],
            )
            db.add(raw_item)
            db.commit()
            raw_item_id = raw_item.id

        run_id, checkpoint_count = asyncio.run(run_flow(raw_item_id))
        with Session(engine) as db:
            raw_item = db.get(RawItem, raw_item_id)
            assert raw_item is not None and raw_item.normalized_item is not None
            jobs = list(
                db.scalars(select(PipelineJob).where(PipelineJob.raw_item_id == raw_item_id))
            )
            assert len(jobs) == 1
            assert jobs[0].job_type == "event"
            assert checkpoint_count == 8
            # The LangGraph technical checkpoint is persisted separately from
            # the business checkpoints and can be read by the PostgreSQL saver.
        async def run_event_job() -> None:
            async with open_postgres_checkpointer(database_url) as saver:
                with factory() as db:
                    job = db.scalar(
                        select(PipelineJob).where(
                            PipelineJob.raw_item_id == raw_item_id,
                            PipelineJob.job_type == "event",
                        )
                    )
                    assert job is not None
                    await execute_pipeline_job(
                        db,
                        job,
                        session_factory=factory,
                        llm_factory=lambda: llm,
                        checkpointer=saver,
                    )
                    job.status = "completed"
                    job.completed_at = datetime.now(UTC)
                    db.commit()

        asyncio.run(run_event_job())
        with Session(engine) as db:
            assert db.scalar(select(EventAggregationRun)) is not None
        async def verify_saver() -> None:
            async with open_postgres_checkpointer(database_url) as saver:
                rows = [
                    item async for item in saver.alist(
                        {"configurable": {"thread_id": f"item_processing:v3.0.0-dev2:production:live:run:{run_id}:raw:{raw_item_id}:revision:1"}}
                    )
                ]
                assert rows

        asyncio.run(verify_saver())
    finally:
        with Session(engine) as db:
            raw_item = db.scalar(select(RawItem).where(RawItem.external_id == f"phase2-{suffix}"))
            if raw_item is not None:
                normalized = raw_item.normalized_item
                if normalized is not None:
                    db.query(EventAggregationRun).filter(
                        EventAggregationRun.normalized_item_id == normalized.id
                    ).delete()
                db.query(PipelineJob).filter(PipelineJob.raw_item_id == raw_item.id).delete()
                db.query(ProcessingCheckpoint).filter(
                    ProcessingCheckpoint.raw_item_id == raw_item.id
                ).delete()
                db.query(RawItem).filter(RawItem.id == raw_item.id).delete()
                db.query(Source).filter(Source.id == raw_item.source_id).delete()
                db.commit()
        engine.dispose()
