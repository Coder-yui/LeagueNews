import asyncio

from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ReviewTask
from app.orchestration.item_processing.service import (
    approve_review,
    start_item_processing,
)
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
