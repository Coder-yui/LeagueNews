import asyncio

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun
from app.orchestration.contracts import EvidenceSnapshot, ItemProcessingRequest, RunMode
from app.orchestration.item_processing import (
    ItemProcessingBackendV3,
    build_item_processing_graph,
    create_item_processing_run,
)
from app.services.llm import (
    MessageClassificationImportanceResult,
    MessageContentAnalysisResult,
    RelevanceResult,
)
from app.services.media_ocr import OCRResult
from app.services.patch_table import PatchTableResult


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


def _patch_database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db:
        source = Source(
            name="V3 patch fixture",
            connector_type="x_twitter",
            external_key="riotphroxzon",
            is_official=True,
            reliability_score=1,
        )
        raw_item = RawItem(
            source=source,
            external_id="v3-patch-1",
            native_title="26.18 Preview",
            content_blocks=[{"type": "paragraph", "text": "patch preview"}],
        )
        raw_item.media_assets.append(
            MediaAsset(
                block_index=0,
                storage_path="/media/private/fixture.png",
                mime_type="image/png",
            )
        )
        db.add(raw_item)
        db.flush()
        experiment_run = ProcessingRun(
            raw_item_id=raw_item.id,
            workflow_type="item",
            status="running",
            current_stage="media",
            execution_mode="manual",
        )
        db.add(experiment_run)
        db.commit()
        return factory, raw_item.id, raw_item.media_assets[0].id, experiment_run.id


def test_v2_baseline_runs_in_experiment_mode_without_business_writes() -> None:
    factory, raw_item_id = _database()
    llm = BaselineLLM()
    backend = ItemProcessingBackendV3(factory, llm_factory=lambda: llm)
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


def test_experiment_ocr_artifact_is_scoped_and_does_not_mutate_production_media(
    monkeypatch,
) -> None:
    factory, raw_item_id, asset_id, run_id = _patch_database()
    ocr = OCRResult(
        raw_text="CHAMPION BUFF\nAhri\nDamage +10",
        lines=[],
        confidence=0.95,
        sha256="experiment-sha",
        width=100,
        height=100,
        processed_width=100,
        processed_height=100,
        engine="fixture-ocr",
    )
    table = PatchTableResult(
        preview_kind="preview",
        divider_x=None,
        structure_confidence=0.95,
        sections=[
            {
                "section_type": "champion_buff",
                "records": [{"target": "Ahri", "raw_changes": ["Damage +10"]}],
            }
        ],
        warnings=[],
        boundaries=[],
    )
    monkeypatch.setattr(
        "app.orchestration.item_processing.backend.run_ocr", lambda *_args: ocr
    )
    monkeypatch.setattr(
        "app.orchestration.item_processing.backend.parse_patch_table",
        lambda *_args, **_kwargs: table,
    )

    backend = ItemProcessingBackendV3(factory)
    request = ItemProcessingRequest(
        workflow_run_id=run_id,
        raw_item_id=raw_item_id,
        raw_item_revision=1,
        run_mode=RunMode.EXPERIMENT,
        batch_id=1,
    )
    proposal = asyncio.run(
        backend.understand_media(
            EvidenceSnapshot(
                raw_item_id=raw_item_id,
                raw_item_revision=1,
                title="26.18 Preview",
                text="patch preview",
                media_asset_ids=[asset_id],
                evidence_fingerprint="fixture",
            ),
            run_mode=request.run_mode,
            workflow_run_id=run_id,
        )
    )

    assert proposal.extraction_ids
    with factory() as db:
        extraction = db.get(MediaExtraction, proposal.extraction_ids[0])
        asset = db.get(MediaAsset, asset_id)
        assert extraction is not None
        assert extraction.artifact_scope == "experiment"
        assert extraction.processing_run_id == run_id
        assert asset is not None
        assert asset.sha256 is None


def test_v2_baseline_publishes_through_v3_graph_with_stage_checkpoints() -> None:
    factory, raw_item_id = _database()
    llm = BaselineLLM()
    backend = ItemProcessingBackendV3(factory, llm_factory=lambda: llm)
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
            select(PipelineJob).where(PipelineJob.job_type == "event")
        )
        assert event_job is not None
