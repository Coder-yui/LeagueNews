import argparse

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import engine
from app.models.event import Event, EventAggregationRun
from app.models.intelligence import Claim, Digest
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.normalized_item import NormalizedItem
from app.models.ocr_lab import OCRProfile, OCRTestRun
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import GlossaryTerm, KnowledgeRule, ProcessingRun, ReviewTask


DERIVED_TABLES = (
    "event_claims",
    "claims",
    "digest_revisions",
    "digests",
    "event_review_tasks",
    "event_aggregation_runs",
    "event_revisions",
    "event_messages",
    "events",
    "normalized_item_media_extractions",
    "normalized_item_revisions",
    "pipeline_jobs",
    "processing_checkpoints",
    "pipeline_corrections",
    "review_tasks",
    "processing_runs",
    "media_extractions",
    "normalized_items",
)


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
        OCRTestRun,
        Event,
        EventAggregationRun,
        Claim,
        Digest,
        KnowledgeRule,
        GlossaryTerm,
        OCRProfile,
    )
    counts = {
        model.__tablename__: int(db.scalar(select(func.count()).select_from(model)) or 0)
        for model in models
    }
    counts["media_assets_with_ocr"] = int(
        db.scalar(
            select(func.count()).select_from(MediaAsset).where(MediaAsset.ocr_text.is_not(None))
        )
        or 0
    )
    counts["published_media_assets"] = int(
        db.scalar(
            select(func.count())
            .select_from(MediaAsset)
            .where(
                (MediaAsset.public_path.is_not(None))
                | (MediaAsset.visibility != "private")
                | (MediaAsset.published_at.is_not(None))
            )
        )
        or 0
    )
    return counts


def _validate_target(
    *,
    database_name: str | None,
    raw_item_count: int,
    expected_database: str,
    expected_raw_items: int,
) -> None:
    if database_name != expected_database:
        raise RuntimeError(
            f"refusing reset: database is {database_name!r}, "
            f"expected {expected_database!r}"
        )
    if raw_item_count != expected_raw_items:
        raise RuntimeError(
            f"refusing reset: raw_items={raw_item_count}, "
            f"expected {expected_raw_items}"
        )


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
    parser.add_argument(
        "--expected-database",
        default="lol_daily_intel",
        help="hard safety check before destructive execution",
    )
    parser.add_argument(
        "--expected-raw-items",
        type=int,
        default=737,
        help="hard safety check before destructive execution",
    )
    args = parser.parse_args()
    with Session(engine) as db:
        before = _counts(db)
        print(
            {
                "database": engine.url.database,
                "before": before,
            }
        )
        if not args.apply:
            print(
                "Dry run only. Use --apply --confirm delete-derived-data "
                "after confirming a database backup."
            )
            return
        if args.confirm != "delete-derived-data":
            raise RuntimeError("--apply requires --confirm delete-derived-data")
        _validate_target(
            database_name=engine.url.database,
            raw_item_count=before["raw_items"],
            expected_database=args.expected_database,
            expected_raw_items=args.expected_raw_items,
        )
        # OCR profiles, rules, and glossary terms are reusable configuration.
        # Test runs, extracted text, publication state, and every message-level
        # projection are derived data and must be rebuilt from RawItem evidence.
        db.execute(text("DELETE FROM ocr_test_runs"))
        for table in DERIVED_TABLES:
            db.execute(text(f"DELETE FROM {table}"))
        db.execute(
            text(
                """
                UPDATE media_assets
                SET ocr_text = NULL,
                    public_path = NULL,
                    visibility = 'private',
                    published_at = NULL
                WHERE ocr_text IS NOT NULL
                   OR public_path IS NOT NULL
                   OR visibility <> 'private'
                   OR published_at IS NOT NULL
                """
            )
        )
        db.commit()
        print({"after": _counts(db)})


if __name__ == "__main__":
    main()
