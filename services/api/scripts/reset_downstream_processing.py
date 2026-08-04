import argparse

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import engine
from app.models.event import Event, EventAggregationRun
from app.models.intelligence import Claim, Digest
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import KnowledgeRule, ProcessingRun, ReviewTask


def _counts(db: Session) -> dict[str, int]:
    models = (
        RawItem,
        NormalizedItem,
        ProcessingRun,
        ReviewTask,
        PipelineJob,
        PipelineCorrection,
        ProcessingCheckpoint,
        MediaExtraction,
        Event,
        EventAggregationRun,
        Claim,
        Digest,
        KnowledgeRule,
    )
    return {
        model.__tablename__: int(db.scalar(select(func.count()).select_from(model)) or 0)
        for model in models
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Delete all data derived after RawItem ingestion while preserving "
            "Sources, RawItems, raw provenance, and raw media. Dry-run by default."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm",
        choices=["delete-derived-data"],
        help="required together with --apply",
    )
    args = parser.parse_args()
    with Session(engine) as db:
        print({"before": _counts(db)})
        if not args.apply:
            print(
                "Dry run only. Use --apply --confirm delete-derived-data "
                "after confirming a database backup."
            )
            return
        if args.confirm != "delete-derived-data":
            raise RuntimeError("--apply requires --confirm delete-derived-data")
        # OCR profiles are reusable configuration and are intentionally kept.
        # A profile may reference the test run it was promoted from, so use
        # DELETE and let ON DELETE SET NULL clear that provenance pointer.
        db.execute(text("DELETE FROM ocr_test_runs"))
        db.execute(
            text(
                """
                TRUNCATE TABLE
                    event_claims,
                    claims,
                    digest_revisions,
                    digests,
                    event_review_tasks,
                    event_aggregation_runs,
                    event_revisions,
                    event_messages,
                    events,
                    normalized_item_media_extractions,
                    normalized_item_revisions,
                    pipeline_jobs,
                    processing_checkpoints,
                    pipeline_corrections,
                    review_tasks,
                    processing_runs,
                    media_extractions,
                    knowledge_rules,
                    glossary_terms,
                    normalized_items
                RESTART IDENTITY
                """
            )
        )
        db.execute(
            text(
                """
                UPDATE media_assets
                SET ocr_text = NULL,
                    public_path = NULL,
                    visibility = 'private',
                    published_at = NULL
                """
            )
        )
        db.commit()
        print({"after": _counts(db)})


if __name__ == "__main__":
    main()
