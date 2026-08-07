from __future__ import annotations

import argparse

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.database import engine
from app.models.event import (
    Event,
    EventAggregationRun,
    EventMessage,
    EventReviewTask,
    EventRevision,
)
from app.models.intelligence import Claim, EventClaim
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun, ReviewTask

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _count(db: Session, model: type[object]) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _counts(db: Session) -> dict[str, int]:
    return {
        "raw_items": _count(db, RawItem),
        "normalized_items": _count(db, NormalizedItem),
        "processing_runs": _count(db, ProcessingRun),
        "review_tasks": _count(db, ReviewTask),
        "claims": _count(db, Claim),
        "pipeline_jobs": _count(db, PipelineJob),
        "pipeline_corrections": _count(db, PipelineCorrection),
        "events": _count(db, Event),
        "event_messages": _count(db, EventMessage),
        "event_revisions": _count(db, EventRevision),
        "event_claims": _count(db, EventClaim),
        "event_aggregation_runs": _count(db, EventAggregationRun),
        "event_review_tasks": _count(db, EventReviewTask),
        "event_checkpoints": int(
            db.scalar(
                select(func.count())
                .select_from(ProcessingCheckpoint)
                .where(ProcessingCheckpoint.stage == "event_decision")
            )
            or 0
        ),
        "active_pipeline_jobs": int(
            db.scalar(
                select(func.count())
                .select_from(PipelineJob)
                .where(PipelineJob.status.in_(["queued", "running"]))
            )
            or 0
        ),
        "active_event_runs": int(
            db.scalar(
                select(func.count())
                .select_from(EventAggregationRun)
                .where(EventAggregationRun.status.in_(["running", "awaiting_review"]))
            )
            or 0
        ),
    }


def _validate_target(
    *,
    database_name: str | None,
    database_host: str | None,
    raw_item_count: int,
    published_item_count: int,
    expected_database: str,
    expected_raw_items: int,
    expected_published_items: int,
) -> None:
    if database_name != expected_database:
        raise RuntimeError(
            f"refusing event reset: database is {database_name!r}, "
            f"expected {expected_database!r}"
        )
    if database_host not in _LOCAL_HOSTS:
        raise RuntimeError(
            f"refusing event reset: database host {database_host!r} is not local"
        )
    if raw_item_count != expected_raw_items:
        raise RuntimeError(
            f"refusing event reset: raw_items={raw_item_count}, "
            f"expected {expected_raw_items}"
        )
    if published_item_count != expected_published_items:
        raise RuntimeError(
            f"refusing event reset: published_items={published_item_count}, "
            f"expected {expected_published_items}"
        )


def _delete_event_layer(db: Session) -> None:
    db.execute(
        text(
            """
            UPDATE pipeline_jobs
            SET last_checkpoint_id = NULL
            WHERE last_checkpoint_id IN (
                SELECT id FROM processing_checkpoints WHERE stage = 'event_decision'
            )
            """
        )
    )
    db.execute(
        text(
            """
            UPDATE pipeline_corrections
            SET checkpoint_id = NULL
            WHERE checkpoint_id IN (
                SELECT id FROM processing_checkpoints WHERE stage = 'event_decision'
            )
            """
        )
    )
    db.execute(text("DELETE FROM processing_checkpoints WHERE stage = 'event_decision'"))
    db.execute(text("UPDATE pipeline_jobs SET event_aggregation_run_id = NULL"))
    db.execute(text("UPDATE pipeline_corrections SET source_event_run_id = NULL"))
    for table in (
        "event_claims",
        "event_review_tasks",
        "event_aggregation_runs",
        "event_revisions",
        "event_messages",
        "events",
    ):
        db.execute(text(f"DELETE FROM {table}"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Delete only local event aggregation data while preserving RawItem, "
            "NormalizedItem, message processing, claims, rules, and media. "
            "Dry-run by default."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--confirm",
        choices=["delete-event-data"],
        help="required together with --apply",
    )
    parser.add_argument("--expected-database", default="lol_daily_intel")
    parser.add_argument("--expected-raw-items", type=int, default=737)
    parser.add_argument("--expected-published-items", type=int, default=64)
    args = parser.parse_args()

    with Session(engine) as db:
        before = _counts(db)
        published_items = int(
            db.scalar(
                select(func.count())
                .select_from(NormalizedItem)
                .where(NormalizedItem.publication_status == "published")
            )
            or 0
        )
        print(
            {
                "database": engine.url.database,
                "database_host": engine.url.host,
                "published_items": published_items,
                "before": before,
            }
        )
        if not args.apply:
            print(
                "Dry run only. Add --apply --confirm delete-event-data "
                "after checking the target and counts."
            )
            return
        if args.confirm != "delete-event-data":
            raise RuntimeError("--apply requires --confirm delete-event-data")
        _validate_target(
            database_name=engine.url.database,
            database_host=engine.url.host,
            raw_item_count=before["raw_items"],
            published_item_count=published_items,
            expected_database=args.expected_database,
            expected_raw_items=args.expected_raw_items,
            expected_published_items=args.expected_published_items,
        )
        if before["active_pipeline_jobs"] or before["active_event_runs"]:
            raise RuntimeError(
                "refusing event reset while pipeline jobs or event runs are active"
            )
        preserved = {
            key: before[key]
            for key in (
                "raw_items",
                "normalized_items",
                "processing_runs",
                "review_tasks",
                "claims",
                "pipeline_jobs",
                "pipeline_corrections",
            )
        }
        _delete_event_layer(db)
        db.commit()
        after = _counts(db)
        changed_preserved = {
            key: (expected, after[key])
            for key, expected in preserved.items()
            if after[key] != expected
        }
        if changed_preserved:
            raise RuntimeError(
                f"event reset changed preserved table counts: {changed_preserved}"
            )
        remaining = {
            key: after[key]
            for key in (
                "events",
                "event_messages",
                "event_revisions",
                "event_claims",
                "event_aggregation_runs",
                "event_review_tasks",
                "event_checkpoints",
            )
            if after[key]
        }
        if remaining:
            raise RuntimeError(f"event reset left event-layer rows: {remaining}")
        print({"after": after})


if __name__ == "__main__":
    main()
