import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.connectors.base import RawItemCandidate
from app.core.database import Base
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob, ProcessingCheckpoint
from app.models.source import Source
from app.models.workflow import ProcessingRun, ReviewTask
from app.services.ingestion import ingest_connector_items


class PassThroughStorage:
    async def materialize_blocks(
        self,
        blocks: list[dict[str, object]],
        *,
        namespace: str,
    ) -> list[dict[str, object]]:
        assert namespace == "test_web"
        return [dict(block) for block in blocks]


def _candidate(text: str) -> RawItemCandidate:
    return RawItemCandidate(
        external_id="versioned-item",
        native_title="Versioned item",
        canonical_url="https://example.com/versioned-item",
        content_kind="article",
        author_name="Author",
        language="en",
        published_at=None,
        content_blocks=[{"type": "paragraph", "text": text}],
        provenance={"text": text},
    )


def test_new_raw_revision_supersedes_message_processing_projection() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="Version source", connector_type="test_web")
        db.add(source)
        db.commit()
        first = asyncio.run(
            ingest_connector_items(
                db,
                source=source,
                items=[_candidate("First version")],
                media_storage=PassThroughStorage(),
            )
        ).created[0]
        item = NormalizedItem(
            raw_item_id=first.id,
            normalized_title="First version",
            normalized_text="First version",
            summary="First version",
            entities=[],
            products=["lol_pc"],
            message_type="game_announcement",
            topics=["activities_rewards"],
            classification_version="message-taxonomy-v2",
            importance_score=0.5,
            target_language="zh-CN",
            translated_title="第一版",
            translated_content_blocks=[],
            translation_status="not_required",
            analysis_model="test",
            analysis_version="test",
        )
        db.add(item)
        item_run = ProcessingRun(
            raw_item_id=first.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="importance",
        )
        db.add(item_run)
        db.flush()
        item_review = ReviewTask(
            processing_run_id=item_run.id,
            stage="importance",
            status="pending",
        )
        job = db.scalar(select(PipelineJob).where(PipelineJob.raw_item_id == first.id))
        assert job is not None
        job.status = "queued"
        job.current_stage = "importance"
        checkpoint = ProcessingCheckpoint(
            raw_item_id=first.id,
            normalized_item_id=item.id,
            processing_run_id=item_run.id,
            stage="importance",
        )
        db.add_all([item_review, checkpoint])
        db.commit()

        successor = asyncio.run(
            ingest_connector_items(
                db,
                source=source,
                items=[_candidate("Second version")],
                media_storage=PassThroughStorage(),
            )
        ).created[0]

        assert successor.revision == 2
        assert successor.supersedes_raw_item_id == first.id
        assert item.publication_status == "superseded"
        assert job.status == "cancelled"
        assert item_run.status == "superseded"
        assert item_review.status == "superseded"
        assert checkpoint.invalidated_at is not None
        assert (
            db.scalar(select(NormalizedItem).where(NormalizedItem.raw_item_id == successor.id))
            is None
        )
