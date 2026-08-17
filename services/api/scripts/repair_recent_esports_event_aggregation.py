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
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session, selectinload

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.domain.esports_match_identity import (
    esports_match_has_subject,
    esports_match_identity_conflict,
    esports_match_same_occurrence_evidence,
    match_identity_from_anchors,
    normalized_match_participants,
    placeholder_match_participants,
)
from app.domain.event_types import AGGREGATION_POLICY_VERSION
from app.models.daily_report import DailyReport
from app.models.event import Event, EventAggregationRun, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.repositories.events import current_event_mention_conditions
from app.services.daily_reports import generate_daily_report
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


def _mention_match_identity(mention: EventMention) -> dict[str, object]:
    fact_changes = mention.structured_fact_changes
    identity = (
        fact_changes.get("match_identity") if isinstance(fact_changes, dict) else None
    )
    return identity if isinstance(identity, dict) and identity else {}


def audit_esports_match_events(
    db: Session,
    *,
    event_ids: set[int] | None = None,
) -> dict[str, object]:
    """Audit esports_match Events against the evidence identities they store.

    ``event_ids=None`` puts the whole family in scope. The three checks:

    - false_merge: a still-valid member mention's stored match identity hard
      conflicts with the Event's own identity (e.g. a WBG/IG member inside a
      JDG/LGD Event). Members without a stored identity (legacy mentions) are
      skipped, and candidates dropped by the pre-LLM identity gate are *not*
      conflicts — that is normal filtering, not a data error.
    - strong_same_occurrence_duplicate: two Events that carry strong positive
      same-occurrence evidence against each other (equal external_match_id, or
      same participants plus equal match_date / full scheduled_at /
      competition+stage+round) with no hard conflict. Participants alone are
      never a duplicate key: the same two teams can play on different days.
    - invalid_event: empty title, participants != exactly 2, member identities
      that hard conflict with each other, or no recoverable material evidence.
    """
    scope_ids = (
        None if event_ids is None else {int(value) for value in event_ids}
    )
    events = list(
        db.scalars(
            select(Event).where(Event.event_family == "esports_match").order_by(Event.id)
        )
    )
    in_scope = [event for event in events if scope_ids is None or event.id in scope_ids]

    false_merge_violations: list[dict[str, object]] = []
    invalid_events: list[dict[str, object]] = []
    for event in in_scope:
        identity = match_identity_from_anchors(event.canonical_anchors or {})
        reasons: list[str] = []
        if not (event.title or "").strip():
            reasons.append("empty title")
        participants = normalized_match_participants(identity.get("participants"))
        if len(participants) != 2:
            reasons.append(
                f"participants != exactly 2: {identity.get('participants')!r}"
            )
        placeholders = placeholder_match_participants(identity)
        if placeholders:
            reasons.append(f"placeholder participants: {placeholders}")

        mentions = list(
            db.scalars(
                select(EventMention)
                .join(EventMention.normalized_item)
                .where(
                    EventMention.event_id == event.id,
                    *current_event_mention_conditions(),
                )
                .order_by(EventMention.id)
            )
        )
        member_identities: list[tuple[EventMention, dict[str, object]]] = [
            (mention, _mention_match_identity(mention))
            for mention in mentions
        ]
        if not any(mention.materiality == "material_update" for mention in mentions):
            reasons.append("no recoverable material evidence")
        elif not _has_restorable_projection(db, event.id, mentions):
            reasons.append("material evidence has no restorable projection patch")

        for mention, member_identity in member_identities:
            if not member_identity:
                continue
            conflict = esports_match_identity_conflict(identity, member_identity)
            if conflict:
                false_merge_violations.append(
                    {
                        "event_id": event.id,
                        "normalized_item_id": mention.normalized_item_id,
                        "mention_id": mention.id,
                        "event_participants": identity.get("participants"),
                        "member_participants": member_identity.get("participants"),
                        "reason": conflict,
                    }
                )
        for index, (mention, member_identity) in enumerate(member_identities):
            if not member_identity:
                continue
            for other_mention, other_identity in member_identities[index + 1 :]:
                if not other_identity:
                    continue
                if esports_match_identity_conflict(member_identity, other_identity):
                    reasons.append(
                        "conflicting member identities: "
                        f"mention {mention.id} vs mention {other_mention.id}"
                    )
        if reasons:
            invalid_events.append({"event_id": event.id, "reasons": reasons})

    # Duplicates: pairwise strong same-occurrence evidence. A pair is reported
    # when at least one side is in scope, so a scoped audit still catches a new
    # Event duplicating a legacy one.
    all_identities = {
        event.id: match_identity_from_anchors(event.canonical_anchors or {})
        for event in events
    }
    strong_duplicate_groups: list[dict[str, object]] = []
    for index, event in enumerate(events):
        for other in events[index + 1 :]:
            if (
                scope_ids is not None
                and event.id not in scope_ids
                and other.id not in scope_ids
            ):
                continue
            evidence = esports_match_same_occurrence_evidence(
                all_identities[event.id], all_identities[other.id]
            )
            if evidence:
                strong_duplicate_groups.append(
                    {
                        "event_ids": [event.id, other.id],
                        "participants": all_identities[event.id].get("participants"),
                        "reason": evidence,
                    }
                )

    return {
        "audited_events": len(in_scope),
        "false_merge_violations": false_merge_violations,
        "strong_same_occurrence_duplicate_groups": strong_duplicate_groups,
        "invalid_events": invalid_events,
        "clean": not (
            false_merge_violations or strong_duplicate_groups or invalid_events
        ),
    }


def post_repair_audit(
    db: Session, *, reaggregated_item_ids: set[int]
) -> dict[str, object]:
    """Audit the esports_match Events the reaggregation produced or touched.

    Scoped to Events with a current mention from the reaggregated items; the
    duplicate comparison inside additionally covers the whole family so a new
    Event duplicating a legacy one is still caught.
    """
    affected = set(
        int(value)
        for value in db.scalars(
            select(EventMention.event_id)
            .join(Event, Event.id == EventMention.event_id)
            .join(EventMention.normalized_item)
            .where(
                EventMention.normalized_item_id.in_(reaggregated_item_ids),
                Event.event_family == "esports_match",
                *current_event_mention_conditions(),
            )
        )
    )
    return audit_esports_match_events(db, event_ids=affected)


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


def repair_failure_reason(
    outcomes: Counter[str], post_audit: dict[str, object]
) -> str | None:
    """A repair only succeeds when no item failed and the post-repair audit is clean.

    The post-repair audit must show false_merge = 0, strong same-occurrence
    duplicates = 0 and invalid events = 0; anything else fails loudly with the
    concrete findings instead of silently reporting success.
    """
    if outcomes.get("failed") or outcomes.get("missing"):
        return f"repair completed with reaggregation failures: {dict(outcomes)}"
    if not post_audit.get("clean"):
        return "post-repair audit failed: " + json.dumps(
            post_audit, ensure_ascii=False, default=str
        )
    return None


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
        payload["identity_audit"] = audit_esports_match_events(
            db,
            event_ids=set(selection.event_ids),
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
    # Post-repair audit: the repair only counts as successful when the events it
    # (re)produced are clean — no false merges, no strong same-occurrence
    # duplicates, no invalid Events. Fail loudly with concrete IDs instead of
    # silently reporting success.
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
