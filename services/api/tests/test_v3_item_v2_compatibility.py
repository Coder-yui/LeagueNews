import asyncio

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.source import Source
from app.orchestration.contracts import ItemProcessingRequest, RunMode
from app.orchestration.catalog import create_v3_graph_registry
from app.orchestration.experiments import (
    CandidateSpec,
    ExperimentCase,
    ExperimentDataset,
    ExperimentPlan,
    ExperimentRunner,
    ExperimentTarget,
    ItemGraphExperimentExecutor,
)
from app.orchestration.item_processing import (
    V2CompatibilityItemBackend,
    build_item_processing_graph,
    create_item_processing_run,
)
from app.services.llm import (
    MessageClassificationImportanceResult,
    MessageContentAnalysisResult,
    RelevanceResult,
)


class BaselineLLM:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def judge_relevance(self, **_kwargs):
        self.calls.append("relevance")
        return RelevanceResult(
            decision="relevant",
            confidence=0.99,
            reason="英雄联盟版本公告",
        )

    async def analyze_message_content(self, **_kwargs):
        self.calls.append("message_analysis")
        return MessageContentAnalysisResult(
            title="26.18版本更新公告",
            summary="新版本将调整多名英雄。",
            entities=[],
            products=["lol_pc"],
            content_form="original",
        )

    async def classify_and_score_importance(self, **_kwargs):
        self.calls.append("importance")
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


def _database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        source = Source(
            name="V3 baseline official",
            connector_type="riot_official",
            is_official=True,
            reliability_score=1,
        )
        db.add(source)
        db.flush()
        raw_item = RawItem(
            source_id=source.id,
            external_id="v3-baseline-1",
            native_title="26.18版本更新公告",
            language="zh-CN",
            content_blocks=[
                {"type": "paragraph", "text": "新版本将调整多名英雄，详细内容即将公布。"}
            ],
        )
        db.add(raw_item)
        db.commit()
        return factory, raw_item.id


def test_v2_baseline_runs_in_experiment_mode_without_business_writes() -> None:
    factory, raw_item_id = _database()
    llm = BaselineLLM()
    backend = V2CompatibilityItemBackend(factory, llm_factory=lambda: llm)
    graph = build_item_processing_graph(backend)
    request = ItemProcessingRequest(
        workflow_run_id=101,
        raw_item_id=raw_item_id,
        raw_item_revision=1,
        run_mode=RunMode.EXPERIMENT,
        batch_id=1,
    )

    result = asyncio.run(
        graph.ainvoke({"request": request.model_dump(mode="json"), "trace": []})
    )

    assert result["outcome"] == "preview_completed"
    assert result["message_analysis"]["title"] == "26.18版本更新公告"
    assert result["importance"]["message_type"] == "game_announcement"
    assert llm.calls == ["relevance", "message_analysis", "importance"]
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(NormalizedItem)) == 0
        assert db.scalar(select(func.count()).select_from(ProcessingCheckpoint)) == 0
        assert db.scalar(select(func.count()).select_from(PipelineJob)) == 0


def test_v2_baseline_publishes_through_v3_graph_with_stage_checkpoints() -> None:
    factory, raw_item_id = _database()
    llm = BaselineLLM()
    backend = V2CompatibilityItemBackend(factory, llm_factory=lambda: llm)
    graph = build_item_processing_graph(backend)
    with factory() as db:
        request = create_item_processing_run(db, raw_item_id=raw_item_id)

    result = asyncio.run(
        graph.ainvoke({"request": request.model_dump(mode="json"), "trace": []})
    )

    assert result["outcome"] == "published"
    with factory() as db:
        item = db.scalar(select(NormalizedItem))
        assert item is not None
        assert item.normalized_title == "26.18版本更新公告"
        assert item.message_type == "game_announcement"
        checkpoints = list(
            db.scalars(
                select(ProcessingCheckpoint).order_by(ProcessingCheckpoint.id)
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
        assert all(checkpoint.graph_name == "item_processing" for checkpoint in checkpoints)
        event_job = db.scalar(
            select(PipelineJob).where(PipelineJob.current_stage == "event_aggregation")
        )
        assert event_job is not None


def test_v2_baseline_is_a_versioned_item_experiment_candidate() -> None:
    factory, raw_item_id = _database()
    llm = BaselineLLM()
    executor = ItemGraphExperimentExecutor(
        registry=create_v3_graph_registry(),
        backend=V2CompatibilityItemBackend(factory, llm_factory=lambda: llm),
    )
    plan = ExperimentPlan(
        experiment_id="item-baseline-001",
        dataset=ExperimentDataset(
            name="item-baseline",
            version="v1",
            target=ExperimentTarget.ITEM_PROCESSING,
            input_schema_version="raw-item-ref-v1",
            cases=[
                ExperimentCase(
                    case_id="official-patch",
                    input={
                        "raw_item_id": raw_item_id,
                        "raw_item_revision": 1,
                    },
                )
            ],
        ),
        candidates=[
            CandidateSpec(
                candidate_id="baseline-v2",
                target=ExperimentTarget.ITEM_PROCESSING,
                graph_name="item_processing",
                graph_version="v3.0.0-dev2",
                state_version=2,
                component_versions={
                    "message_semantics": "reviewed-pipeline-v2",
                    "importance_policy": "importance-v11-repost-weekly-rotation",
                },
            )
        ],
        max_concurrency=2,
    )

    report = asyncio.run(
        ExperimentRunner({ExperimentTarget.ITEM_PROCESSING: executor}).run(plan)
    )

    candidate = report.candidate_results[0]
    assert candidate.succeeded == 1
    assert candidate.cases[0].actual["outcome"] == "preview_completed"
    assert candidate.cases[0].actual["message_analysis"]["title"] == "26.18版本更新公告"
    with factory() as db:
        assert db.scalar(select(func.count()).select_from(NormalizedItem)) == 0
        assert db.scalar(select(func.count()).select_from(ProcessingCheckpoint)) == 0
