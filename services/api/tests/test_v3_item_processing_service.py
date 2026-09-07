import asyncio

import pytest
import app.orchestration.item_processing.service as item_processing_service
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import ProcessingCheckpoint
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ReviewTask
from app.orchestration.item_processing.service import (
    approve_review,
    start_item_processing,
)
from app.methods import MethodAssemblyConfig
from app.services.llm import (
    MessageClassificationImportanceResult,
    MessageContentAnalysisResult,
    RelevanceResult,
)


class BaselineLLM:
    async def judge_relevance(self, **_kwargs):
        return RelevanceResult(
            decision="relevant", confidence=0.99, reason="英雄联盟版本公告"
        )

    async def analyze_message_content(self, **_kwargs):
        return MessageContentAnalysisResult(
            title="26.18版本更新公告",
            summary="新版本将调整多名英雄。",
            entities=[],
            products=["lol_pc"],
            content_form="original",
        )

    async def classify_and_score_importance(self, **_kwargs):
        return MessageClassificationImportanceResult(
            message_type="game_announcement",
            topics=["balance_gameplay"],
            scale="major",
            audience_region="global",
            competition_region="none",
            prominence="normal",
            skin_tier="none",
            is_bulk_update=True,
            evidence=["新版本将调整多名英雄"],
        )


def test_manual_service_persists_interrupts_and_resumes_one_graph_thread() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    saver = InMemorySaver()
    llm = BaselineLLM()
    with factory() as db:
        source = Source(
            name="V3 service source",
            connector_type="riot_official",
            is_official=True,
            reliability_score=1,
        )
        db.add(source)
        db.flush()
        raw_item = RawItem(
            source_id=source.id,
            external_id="v3-service-manual",
            native_title="26.18版本更新公告",
            language="zh-CN",
            content_blocks=[
                {"type": "paragraph", "text": "新版本将调整多名英雄，详细内容即将公布。"}
            ],
        )
        db.add(raw_item)
        db.commit()

        run = asyncio.run(
            start_item_processing(
                db,
                raw_item,
                execution_mode="manual",
                session_factory=factory,
                llm_factory=lambda: llm,
                checkpointer=saver,
            )
        )
        stages = []
        while run.status == "awaiting_review":
            review = db.scalar(
                select(ReviewTask).where(
                    ReviewTask.processing_run_id == run.id,
                    ReviewTask.status == "pending",
                )
            )
            assert review is not None
            stages.append(review.stage)
            run = asyncio.run(
                approve_review(
                    db,
                    review,
                    note="test approval",
                    session_factory=factory,
                    llm_factory=lambda: llm,
                    checkpointer=saver,
                )
            )

        assert stages == [
            "relevance",
            "media",
            "translation",
            "message_analysis",
            "importance",
            "evidence_gate",
        ]
        assert run.status == "completed"
        assert run.outcome == "approved"
        assert db.scalar(select(NormalizedItem).where(NormalizedItem.raw_item_id == raw_item.id))
        assert all(review.delivery_status == "consumed" for review in run.reviews)
        event_jobs = list(
            db.scalars(
                select(PipelineJob).where(PipelineJob.raw_item_id == raw_item.id)
            )
        )
        assert len(event_jobs) == 1
        assert event_jobs[0].job_type == "event"
        assert event_jobs[0].target_entity_type == "normalized_item"
        assert event_jobs[0].target_revision == 1
        checkpoints = list(
            db.scalars(
                select(ProcessingCheckpoint)
                .where(ProcessingCheckpoint.processing_run_id == run.id)
                .order_by(ProcessingCheckpoint.id)
            )
        )
        assert [checkpoint.stage for checkpoint in checkpoints] == [
            "evidence",
            "relevance",
            "media",
            "translation",
            "message_analysis",
            "importance",
            "evidence_gate",
            "publication",
        ]


def test_run_snapshots_method_configuration_before_execution() -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    config = MethodAssemblyConfig(
        message_analysis="baseline",
        prompt_refs={"message_analysis": "phase2-test-prompt"},
        model_parameters={"message_analysis": {"temperature": 0}},
        strategy_parameters={"importance_scoring": {"threshold": 0.8}},
    )
    with factory() as db:
        source = Source(name="V3 method config source", connector_type="manual")
        db.add(source)
        db.flush()
        raw_item = RawItem(
            source_id=source.id,
            external_id="v3-method-config",
            content_blocks=[{"type": "paragraph", "text": "test"}],
        )
        db.add(raw_item)
        db.commit()
        run = asyncio.run(
            start_item_processing(
                db,
                raw_item,
                execution_mode="automatic",
                defer_execution=True,
                method_config=config,
            )
        )
        assert run.method_config == config.model_dump(mode="json")
        assert run.context["method_config"] == config.model_dump(mode="json")


def test_review_decision_survives_crash_before_graph_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    saver = InMemorySaver()
    llm = BaselineLLM()
    with factory() as db:
        source = Source(name="V3 review crash source", connector_type="manual")
        db.add(source)
        db.flush()
        raw_item = RawItem(
            source_id=source.id,
            external_id="v3-review-crash",
            content_blocks=[{"type": "paragraph", "text": "需要审核的消息"}],
        )
        db.add(raw_item)
        db.commit()
        run = asyncio.run(
            start_item_processing(
                db,
                raw_item,
                execution_mode="manual",
                session_factory=factory,
                llm_factory=lambda: llm,
                checkpointer=saver,
            )
        )
        review = db.scalar(
            select(ReviewTask).where(
                ReviewTask.processing_run_id == run.id,
                ReviewTask.status == "pending",
            )
        )
        assert review is not None

        async def crash_before_resume(*_args: object, **_kwargs: object) -> dict[str, object]:
            raise RuntimeError("simulated crash after review commit")

        original_invoke = item_processing_service.invoke_item_processing
        monkeypatch.setattr(
            item_processing_service, "invoke_item_processing", crash_before_resume
        )
        with pytest.raises(RuntimeError, match="simulated crash"):
            asyncio.run(
                approve_review(
                    db,
                    review,
                    note="批准",
                    session_factory=factory,
                    llm_factory=lambda: llm,
                    checkpointer=saver,
                )
            )

        db.expire_all()
        recorded = db.get(ReviewTask, review.id)
        assert recorded is not None
        assert recorded.status == "approved"
        assert recorded.delivery_status == "recorded"
        command_id = recorded.command_id

        monkeypatch.setattr(
            item_processing_service,
            "invoke_item_processing",
            original_invoke,
        )
        resumed = asyncio.run(
            approve_review(
                db,
                recorded,
                note="批准",
                session_factory=factory,
                llm_factory=lambda: llm,
                checkpointer=saver,
            )
        )
        assert resumed.status == "awaiting_review"
        db.expire_all()
        delivered = db.get(ReviewTask, review.id)
        assert delivered is not None
        assert delivered.command_id == command_id
        assert delivered.delivery_status == "consumed"
        assert (
            db.scalar(
                select(ReviewTask).where(
                    ReviewTask.processing_run_id == run.id,
                    ReviewTask.status == "pending",
                )
            )
            is not None
        )
