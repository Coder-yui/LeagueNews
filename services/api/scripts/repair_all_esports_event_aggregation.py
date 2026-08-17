"""Reaggregate every current published message routed to the esports-match space.

This operator tool is dry-run by default and never edits RawItem or
NormalizedItem rows. It intentionally reuses the bounded repair's rollback,
reaggregation, and report regeneration implementations.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.domain.event_admission import minimal_event_filter
from app.domain.event_families import possible_event_families
from app.models.daily_report import DailyReport
from app.models.event import Event, EventAggregationRun, EventMention
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.services.raw_item_versions import latest_normalized_item_condition
from scripts.repair_recent_esports_event_aggregation import (
    RepairSelection,
    audit_esports_match_events,
    post_repair_audit,
    regenerate_published_reports,
    reaggregate_items,
    repair_failure_reason,
    rollback_selection,
    selection_payload,
)


CONFIRMATION = "reaggregate-all-esports-messages"
SHANGHAI = ZoneInfo("Asia/Shanghai")


def all_esports_item_ids(db: Session) -> list[int]:
    """Select every current published item routed to the esports_match space.

    The selection is upstream-routing based (products + topics -> event space
    contains ``esports_match``), never membership based: messages that were
    previously mis-ignored, failed with model errors, or never produced an
    EventMention are still selected and re-examined.
    """
    routing_rows = db.execute(
        select(NormalizedItem.id, NormalizedItem.products, NormalizedItem.topics)
        .join(RawItem, RawItem.id == NormalizedItem.raw_item_id)
        .where(
            NormalizedItem.publication_status == "published",
            latest_normalized_item_condition(),
        )
    )
    routed_ids = [
        int(row[0])
        for row in routing_rows
        if "esports_match"
        in possible_event_families(row[1] or [], row[2] or [])
    ]

    observed_at = func.coalesce(RawItem.published_at, RawItem.ingested_at)
    rows = db.execute(
        select(NormalizedItem.id, observed_at.label("observed_at"), RawItem.id)
        .join(RawItem, RawItem.id == NormalizedItem.raw_item_id)
        .where(NormalizedItem.id.in_(routed_ids))
        .order_by(observed_at.desc(), RawItem.id.desc(), NormalizedItem.id.desc())
    )
    ordered_ids = [int(row[0]) for row in rows]

    # Keep only items the minimal filter would actually process (published and
    # semantically non-empty), so the repair never re-runs skip-only messages.
    processable: list[int] = []
    for item in db.scalars(
        select(NormalizedItem)
        .where(NormalizedItem.id.in_(ordered_ids))
        .options(selectinload(NormalizedItem.raw_item))
    ):
        admission = minimal_event_filter(item)
        if admission.decision == "process":
            processable.append(item.id)
    processable_set = set(processable)
    return [item_id for item_id in ordered_ids if item_id in processable_set]


def _shanghai_date(value: datetime) -> date:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return normalized.astimezone(SHANGHAI).date()


def inspect_all_selection(db: Session) -> RepairSelection:
    item_ids = all_esports_item_ids(db)
    if not item_ids:
        return RepairSelection((), (), (), (), (), (), (), ())

    items = list(
        db.scalars(
            select(NormalizedItem)
            .where(NormalizedItem.id.in_(item_ids))
            .options(selectinload(NormalizedItem.raw_item))
        )
    )
    item_by_id = {item.id: item for item in items}
    item_revisions = tuple(
        (item_id, item_by_id[item_id].current_revision) for item_id in item_ids
    )
    current_revision_by_id = dict(item_revisions)
    mentions = list(
        db.scalars(
            select(EventMention)
            .join(Event, Event.id == EventMention.event_id)
            .where(
                EventMention.normalized_item_id.in_(item_ids),
                Event.event_family == "esports_match",
            )
        )
    )
    current_mentions = [
        mention
        for mention in mentions
        if mention.normalized_item_revision
        == current_revision_by_id[mention.normalized_item_id]
    ]
    runs = list(
        db.scalars(
            select(EventAggregationRun).where(
                EventAggregationRun.normalized_item_id.in_(item_ids)
            )
        )
    )
    current_runs = [
        run
        for run in runs
        if run.normalized_item_revision == current_revision_by_id[run.normalized_item_id]
    ]
    raw_item_ids = [item_by_id[item_id].raw_item_id for item_id in item_ids]
    active_job_ids = tuple(
        int(value)
        for value in db.scalars(
            select(PipelineJob.id).where(
                PipelineJob.raw_item_id.in_(raw_item_ids),
                PipelineJob.status.in_(("queued", "running")),
            )
        )
    )
    report_dates = tuple(
        sorted(
            {
                _shanghai_date(item_by_id[item_id].raw_item.published_at)
                for item_id in item_ids
                if item_by_id[item_id].raw_item.published_at is not None
            }
        )
    )
    published_report_dates = (
        tuple(
            sorted(
                db.scalars(
                    select(DailyReport.report_date).where(
                        DailyReport.report_date.in_(report_dates),
                        DailyReport.status == "published",
                    )
                )
            )
        )
        if report_dates
        else ()
    )
    return RepairSelection(
        item_ids_newest_first=tuple(item_ids),
        item_revisions=item_revisions,
        mention_ids=tuple(sorted(mention.id for mention in current_mentions)),
        event_ids=tuple(sorted({mention.event_id for mention in current_mentions})),
        run_ids=tuple(sorted(run.id for run in current_runs)),
        report_dates=report_dates,
        published_report_dates=published_report_dates,
        active_job_ids=active_job_ids,
    )


def _validate_args(args: argparse.Namespace) -> None:
    if engine.url.database != args.expected_database:
        raise RuntimeError(
            f"refusing database {engine.url.database!r}; expected {args.expected_database!r}"
        )
    if args.apply:
        if args.confirm != CONFIRMATION:
            raise RuntimeError(f"--confirm must equal {CONFIRMATION!r}")
        if not args.workers_stopped:
            raise RuntimeError("--workers-stopped is required after stopping both schedulers")
        if not args.selection_token:
            raise RuntimeError("--selection-token from the dry run is required")
        if not settings.openai_api_key.strip():
            raise RuntimeError("OPENAI_API_KEY is not configured")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--selection-token")
    parser.add_argument("--workers-stopped", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    _validate_args(args)

    with SessionLocal() as db:
        selection = inspect_all_selection(db)
        payload = selection_payload(selection, database=engine.url.database)
        payload["identity_audit"] = audit_esports_match_events(
            db,
            event_ids=set(selection.event_ids) or None,
        )
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    if not selection.item_ids_newest_first:
        raise RuntimeError("no current published esports-match messages were selected")
    if not args.apply:
        print("Dry run only. Stop workers, take a backup, then apply with this selection token.")
        return
    if selection.token != args.selection_token:
        raise RuntimeError("selection changed after dry run; inspect again before applying")
    if selection.active_job_ids:
        raise RuntimeError(
            f"selected items still have active pipeline jobs: {selection.active_job_ids}"
        )

    with SessionLocal() as db:
        current = inspect_all_selection(db)
        if current.item_revisions != selection.item_revisions or current.token != selection.token:
            raise RuntimeError("selection changed before rollback transaction")
        rollback = rollback_selection(db, selection)
        db.commit()
    print(json.dumps({"rollback": rollback}), flush=True)

    outcomes = await reaggregate_items(selection.item_ids_newest_first)
    regenerated = regenerate_published_reports(selection.published_report_dates)
    # A full repair selects every esports-routed message, so the post-repair
    # audit covers the whole esports_match family: false_merge = 0,
    # strong_same_occurrence_duplicate = 0 and invalid_event = 0 are required
    # for the repair to count as successful.
    with SessionLocal() as db:
        post_audit = post_repair_audit(
            db, reaggregated_item_ids=set(selection.item_ids_newest_first)
        )
    result = {
        "reaggregation": dict(outcomes),
        "daily_reports_regenerated": regenerated,
        "post_repair_audit": post_audit,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    failure = repair_failure_reason(outcomes, post_audit)
    if failure:
        raise RuntimeError(failure)


if __name__ == "__main__":
    asyncio.run(main())
