"""Reaggregate a bounded recent batch of esports-match messages.

This is an operator tool for a reviewed production repair. It is dry-run by
default and never edits RawItem or NormalizedItem rows.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, selectinload

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.domain.esports_match_identity import (
    esports_match_has_subject,
    esports_match_identity_conflict,
    match_identity_from_anchors,
    match_identity_from_message_entities,
)
from app.domain.event_admission import minimal_event_filter
from app.domain.event_types import AGGREGATION_POLICY_VERSION
from app.models.daily_report import DailyReport
from app.models.event import Event, EventAggregationRun, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.repositories.events import current_event_mention_conditions
from app.services.daily_reports import generate_daily_report
from app.services.event_candidates import (
    esports_match_identity_gate,
    recall_event_candidates,
)
from app.services.event_metrics import refresh_event_metrics
from app.services.raw_item_versions import latest_normalized_item_condition
from app.workflows.event_aggregation import aggregate_normalized_item


MAX_LIMIT = 100
CONFIRMATION = "reaggregate-recent-esports-messages"
SHANGHAI = ZoneInfo("Asia/Shanghai")


@dataclass(frozen=True, slots=True)
class RepairSelection:
    item_ids_newest_first: tuple[int, ...]
    item_revisions: tuple[tuple[int, int], ...]
    mention_ids: tuple[int, ...]
    event_ids: tuple[int, ...]
    run_ids: tuple[int, ...]
    report_dates: tuple[date, ...]
    published_report_dates: tuple[date, ...]
    active_job_ids: tuple[int, ...]

    @property
    def token(self) -> str:
        encoded = json.dumps(
            {
                "item_revisions": self.item_revisions,
                "mention_ids": self.mention_ids,
                "event_ids": self.event_ids,
                "run_ids": self.run_ids,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode()).hexdigest()


def recent_esports_item_ids(db: Session, *, limit: int) -> list[int]:
    observed_at = func.coalesce(RawItem.published_at, RawItem.ingested_at)
    rows = db.execute(
        select(NormalizedItem.id, observed_at.label("observed_at"), RawItem.id)
        .join(RawItem, RawItem.id == NormalizedItem.raw_item_id)
        .join(EventMention, EventMention.normalized_item_id == NormalizedItem.id)
        .join(Event, Event.id == EventMention.event_id)
        .where(
            Event.event_family == "esports_match",
            *current_event_mention_conditions(),
            latest_normalized_item_condition(),
        )
        .group_by(NormalizedItem.id, observed_at, RawItem.id)
        .order_by(observed_at.desc(), RawItem.id.desc(), NormalizedItem.id.desc())
        .limit(limit)
    )
    return [int(row[0]) for row in rows]


def inspect_selection(db: Session, *, limit: int) -> RepairSelection:
    item_ids = recent_esports_item_ids(db, limit=limit)
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
            select(EventMention).where(EventMention.normalized_item_id.in_(item_ids))
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
    published_report_dates = tuple(
        sorted(
            db.scalars(
                select(DailyReport.report_date).where(
                    DailyReport.report_date.in_(report_dates),
                    DailyReport.status == "published",
                )
            )
        )
    ) if report_dates else ()
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


def _shanghai_date(value: datetime) -> date:
    normalized = value.replace(tzinfo=UTC) if value.tzinfo is None else value
    return normalized.astimezone(SHANGHAI).date()


def selection_payload(selection: RepairSelection, *, database: str | None) -> dict[str, object]:
    return {
        "database": database,
        "policy": AGGREGATION_POLICY_VERSION,
        "selection_token": selection.token,
        "selected_items": len(selection.item_ids_newest_first),
        "item_ids_newest_first": list(selection.item_ids_newest_first),
        "current_mentions_to_remove": len(selection.mention_ids),
        "affected_events": len(selection.event_ids),
        "current_runs_to_remove": len(selection.run_ids),
        "affected_report_dates": [value.isoformat() for value in selection.report_dates],
        "published_reports_to_regenerate": [
            value.isoformat() for value in selection.published_report_dates
        ],
        "active_job_ids": list(selection.active_job_ids),
    }


def rollback_selection(db: Session, selection: RepairSelection) -> dict[str, int]:
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": "repair:recent-esports-event-aggregation"},
        )

    raw_before = int(db.scalar(select(func.count()).select_from(RawItem)) or 0)
    normalized_before = int(
        db.scalar(select(func.count()).select_from(NormalizedItem)) or 0
    )
    item_ids = list(selection.item_ids_newest_first)
    db.execute(
        select(NormalizedItem.id)
        .where(NormalizedItem.id.in_(item_ids))
        .with_for_update()
    )
    if selection.event_ids:
        db.execute(
            select(Event.id)
            .where(Event.id.in_(selection.event_ids))
            .with_for_update()
        )
    if selection.mention_ids:
        db.execute(delete(EventMention).where(EventMention.id.in_(selection.mention_ids)))
    if selection.run_ids:
        db.execute(
            delete(EventAggregationRun).where(EventAggregationRun.id.in_(selection.run_ids))
        )
    db.flush()

    orphaned: set[int] = set()
    invalid: set[int] = set()
    retained: set[int] = set()
    for event_id in selection.event_ids:
        current_mentions = list(
            db.scalars(
                select(EventMention)
                .join(EventMention.normalized_item)
                .where(
                    EventMention.event_id == event_id,
                    *current_event_mention_conditions(),
                )
            )
        )
        if not current_mentions:
            orphaned.add(event_id)
            continue
        event = db.get(Event, event_id)
        if event is None:
            orphaned.add(event_id)
            continue
        if event.event_family == "esports_match" and not _rebuilds_minimal_esports_match(
            db, event_id, current_mentions
        ):
            # The remaining still-valid evidence cannot rebuild a minimal valid
            # esports_match (a valid title and a recognizable match subject). Such
            # a shell Event has no usable baseline and must not be kept as a normal
            # Event participating in recall; delete it instead of leaving a shell.
            # Deleting the Event cascades to its dangling remaining memberships.
            invalid.add(event_id)
            continue
        if not _has_restorable_projection(db, event_id, current_mentions):
            raise RuntimeError(
                f"event {event_id} has remaining current mentions but no restorable "
                "material projection; expand or manually repair the batch"
            )
        retained.add(event_id)

    if orphaned:
        db.execute(delete(Event).where(Event.id.in_(orphaned)))
        db.flush()
    if invalid:
        db.execute(delete(Event).where(Event.id.in_(invalid)))
        db.flush()
    if retained:
        refresh_event_metrics(db, retained)

    raw_after = int(db.scalar(select(func.count()).select_from(RawItem)) or 0)
    normalized_after = int(
        db.scalar(select(func.count()).select_from(NormalizedItem)) or 0
    )
    if raw_after != raw_before or normalized_after != normalized_before:
        raise RuntimeError("repair changed RawItem or NormalizedItem row counts")
    return {
        "mentions_removed": len(selection.mention_ids),
        "runs_removed": len(selection.run_ids),
        "events_deleted": len(orphaned),
        "events_deleted_invalid_shell": len(invalid),
        "events_rebuilt": len(retained),
    }


def _rebuilds_minimal_esports_match(
    db: Session, event_id: int, mentions: list[EventMention]
) -> bool:
    """Preview whether still-valid evidence can rebuild a minimal esports_match.

    Rebuilds the projection from the remaining material mentions (the same replay
    the metrics refresh performs) and requires both a valid title and a
    recognizable match subject (participants or external_match_id). An Event that
    fails this has no usable baseline and must be deleted in repair instead of
    being left as an empty shell.
    """
    if not any(mention.materiality == "material_update" for mention in mentions):
        return False
    refresh_event_metrics(db, {event_id})
    event = db.get(Event, event_id)
    if event is None:
        return False
    if not (event.title or "").strip():
        return False
    return esports_match_has_subject(event.canonical_anchors or {})


def audit_esports_identity(
    db: Session,
    *,
    item_ids: tuple[int, ...],
    event_ids: tuple[int, ...],
) -> dict[str, object]:
    """Audit repair targets across the match-identity layers.

    Beyond strong-identity duplicates this covers the three structured identity
    checks: invalid/empty esports_match projections, missing usable match
    subjects, and the cross-participant conflicts the pre-LLM identity gate drops.
    """
    events = list(
        db.scalars(
            select(Event).where(
                Event.id.in_(event_ids), Event.event_family == "esports_match"
            )
        )
    )
    invalid_empty_projection: list[int] = []
    missing_subject: list[int] = []
    by_identity: dict[tuple[Any, ...], list[int]] = {}
    for event in events:
        identity = match_identity_from_anchors(event.canonical_anchors or {})
        if not (event.title or "").strip():
            invalid_empty_projection.append(event.id)
        if not esports_match_has_subject(identity):
            missing_subject.append(event.id)
        external = _normalized_scalar_audit(identity.get("external_match_id"))
        if external:
            key: tuple[Any, ...] = ("external", external)
        else:
            participants = _normalized_participants_audit(identity.get("participants"))
            key = ("participants", tuple(sorted(participants)))
        by_identity.setdefault(key, []).append(event.id)
    strong_identity_duplicate_groups = [
        {"identity": list(key), "event_ids": sorted(group)}
        for key, group in by_identity.items()
        if len(group) > 1
    ]

    cross_participant_conflicts: list[dict[str, object]] = []
    for item_id in item_ids:
        item = db.get(NormalizedItem, item_id)
        if item is None:
            continue
        admission = minimal_event_filter(item)
        if admission.decision == "skip":
            continue
        recalled = recall_event_candidates(
            db,
            item=item,
            possible_families=admission.event_space.possible_families,
            entity_hints=admission.entity_hints,
        )
        incoming_identity = match_identity_from_message_entities(item.entities)
        gated = esports_match_identity_gate(
            recalled, incoming_identity=incoming_identity
        )
        gated_ids = {int(candidate["event_id"]) for candidate in gated}
        for candidate in recalled:
            if candidate.get("event_family") != "esports_match":
                continue
            if int(candidate["event_id"]) in gated_ids:
                continue
            conflict = esports_match_identity_conflict(
                match_identity_from_anchors(
                    candidate.get("canonical_anchors") or {}
                ),
                incoming_identity,
            )
            if conflict and conflict.startswith("participants"):
                cross_participant_conflicts.append(
                    {
                        "item_id": item_id,
                        "event_id": candidate["event_id"],
                        "reason": conflict,
                    }
                )

    return {
        "invalid_empty_projection_events": invalid_empty_projection,
        "missing_match_subject_events": missing_subject,
        "strong_identity_duplicate_groups": strong_identity_duplicate_groups,
        "cross_participant_conflicts": cross_participant_conflicts,
    }


def _normalized_scalar_audit(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = " ".join(str(value).strip().casefold().split())
    return normalized or None


def _normalized_participants_audit(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    return {
        " ".join(str(part).strip().casefold().split())
        for part in value
        if isinstance(part, str) and part.strip()
    }


def _has_restorable_projection(
    db: Session, event_id: int, mentions: list[EventMention]
) -> bool:
    material_keys = {
        (
            mention.normalized_item_id,
            mention.normalized_item_revision,
            mention.mention_index,
            mention.aggregation_policy_version,
        )
        for mention in mentions
        if mention.materiality == "material_update"
    }
    if not material_keys:
        return False
    for snapshot in db.scalars(
        select(EventRevision.evidence_snapshot).where(EventRevision.event_id == event_id)
    ):
        evidence = snapshot or {}
        key = (
            evidence.get("normalized_item_id"),
            evidence.get("normalized_item_revision"),
            evidence.get("mention_index"),
            evidence.get("aggregation_policy_version"),
        )
        if key in material_keys and (
            isinstance(evidence.get("projection_patch"), dict)
            or isinstance(evidence.get("projection_snapshot"), dict)
        ):
            return True
    return False


async def reaggregate_items(item_ids_newest_first: tuple[int, ...]) -> Counter[str]:
    outcomes: Counter[str] = Counter()
    for index, item_id in enumerate(reversed(item_ids_newest_first), start=1):
        with SessionLocal() as db:
            item = db.scalar(
                select(NormalizedItem)
                .where(NormalizedItem.id == item_id)
                .options(
                    selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
                    selectinload(NormalizedItem.media_links),
                )
            )
            if item is None:
                outcomes["missing"] += 1
                continue
            try:
                run = await aggregate_normalized_item(db, item)
            except Exception as exc:
                outcomes["failed"] += 1
                print(
                    json.dumps(
                        {"item_id": item_id, "outcome": "failed", "error": str(exc)},
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
            else:
                outcomes[str(run.outcome or run.status)] += 1
        if index % 10 == 0 or index == len(item_ids_newest_first):
            print(
                json.dumps(
                    {
                        "processed": index,
                        "total": len(item_ids_newest_first),
                        "outcomes": dict(outcomes),
                    }
                ),
                flush=True,
            )
    return outcomes


def regenerate_published_reports(report_dates: tuple[date, ...]) -> list[str]:
    regenerated: list[str] = []
    for report_date in report_dates:
        with SessionLocal() as db:
            report = db.scalar(
                select(DailyReport).where(DailyReport.report_date == report_date)
            )
            if report is None or report.status != "published":
                continue
            generate_daily_report(db, report_date)
            db.commit()
            regenerated.append(report_date.isoformat())
    return regenerated


def _validate_args(args: argparse.Namespace) -> None:
    if args.limit < 1 or args.limit > MAX_LIMIT:
        raise RuntimeError(f"--limit must be between 1 and {MAX_LIMIT}")
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
    parser.add_argument("--limit", type=int, default=MAX_LIMIT)
    parser.add_argument("--expected-database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--selection-token")
    parser.add_argument("--workers-stopped", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    _validate_args(args)

    with SessionLocal() as db:
        selection = inspect_selection(db, limit=args.limit)
        payload = selection_payload(selection, database=engine.url.database)
        payload["identity_audit"] = audit_esports_identity(
            db,
            item_ids=selection.item_ids_newest_first,
            event_ids=selection.event_ids,
        )
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    if len(selection.item_ids_newest_first) != args.limit:
        raise RuntimeError(
            f"selected {len(selection.item_ids_newest_first)} items; expected exactly {args.limit}"
        )
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
        current = inspect_selection(db, limit=args.limit)
        if current.item_revisions != selection.item_revisions or current.token != selection.token:
            raise RuntimeError("selection changed before rollback transaction")
        rollback = rollback_selection(db, selection)
        db.commit()
    print(json.dumps({"rollback": rollback}), flush=True)

    outcomes = await reaggregate_items(selection.item_ids_newest_first)
    regenerated = regenerate_published_reports(selection.published_report_dates)
    result = {
        "reaggregation": dict(outcomes),
        "daily_reports_regenerated": regenerated,
    }
    print(json.dumps(result, ensure_ascii=False), flush=True)
    if outcomes.get("failed") or outcomes.get("missing"):
        raise RuntimeError(f"repair completed with reaggregation failures: {dict(outcomes)}")


if __name__ == "__main__":
    asyncio.run(main())
