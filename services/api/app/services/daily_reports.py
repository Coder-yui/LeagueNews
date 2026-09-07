from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.methods import MethodAssembly
from app.models.daily_report import DailyReport, DailyReportItem
from app.models.event import EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.repositories.events import current_event_mention_conditions
from app.services.raw_item_versions import latest_normalized_item_condition

from app.domain.daily_report import (
    DailyReportCandidate as DailyReportCandidate,
    daily_report_window as daily_report_window,
    daily_report_section as daily_report_section,
    select_daily_sections as select_daily_sections,
    eligible_daily_candidates as eligible_daily_candidates,
    deduplicate_daily_candidates as deduplicate_daily_candidates,
    assign_daily_sections as assign_daily_sections,
    rank_daily_sections as rank_daily_sections,
    DAILY_REPORT_TIMEZONE as DAILY_REPORT_TIMEZONE,
    DAILY_REPORT_MIN_IMPORTANCE as DAILY_REPORT_MIN_IMPORTANCE,
    DAILY_REPORT_SECTION_LIMITS as DAILY_REPORT_SECTION_LIMITS,
)


def daily_report_eligibility_conditions():
    """Return the current-projection contract shared by report reads and the graph."""

    return (
        latest_normalized_item_condition(),
        NormalizedItem.publication_status == "published",
    )


def daily_report_scheduler_eligibility_conditions():
    """Return SQL-safe eligibility without running report selection methods."""

    return (
        *daily_report_eligibility_conditions(),
        NormalizedItem.content_form == "original",
        NormalizedItem.importance_score >= DAILY_REPORT_MIN_IMPORTANCE,
    )


def generate_daily_report(
    db: Session, report_date: date, *, assembly: MethodAssembly
) -> DailyReport:
    """Generate or replace one persisted report for a Shanghai calendar day."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"daily-report:{report_date.isoformat()}"},
        )
    candidates = load_daily_candidates(db, report_date)
    plan = assembly.plan_daily_report(candidates)
    by_id = {candidate.message_id: candidate for candidate in candidates}
    sections = {name: [by_id[row["message_id"]] for row in rows] for name, rows in plan.sections.items()}

    return persist_daily_report(db, report_date, sections)


def load_daily_candidates(db: Session, report_date: date) -> list[DailyReportCandidate]:
    window_start, window_end = daily_report_window(report_date)
    statement = (
        select(NormalizedItem)
        .join(NormalizedItem.raw_item)
        .where(
            *daily_report_eligibility_conditions(),
            RawItem.published_at >= window_start,
            RawItem.published_at < window_end,
        )
    )
    items = list(db.scalars(statement))
    item_ids = [item.id for item in items]
    event_ids_by_item: dict[int, set[int]] = {item_id: set() for item_id in item_ids}
    if item_ids:
        rows = db.execute(
            select(EventMention.normalized_item_id, EventMention.event_id)
            .join(EventMention.normalized_item)
            .where(
                EventMention.normalized_item_id.in_(item_ids),
                *current_event_mention_conditions(),
            )
        )
        for normalized_item_id, event_id in rows:
            event_ids_by_item[normalized_item_id].add(event_id)
    candidates = [
        DailyReportCandidate(
            message_id=item.id,
            importance_score=item.importance_score,
            published_at=item.raw_item.published_at,
            content_form=item.content_form,
            products=tuple(item.products or ()),
            event_ids=tuple(sorted(event_ids_by_item[item.id])),
        )
        for item in items
        if item.raw_item.published_at is not None
    ]
    return candidates


def selected_daily_ids(
    db: Session, report_date: date, *, assembly: MethodAssembly
) -> set[int]:
    plan = assembly.plan_daily_report(load_daily_candidates(db, report_date))
    return {row["message_id"] for rows in plan.sections.values() for row in rows}


def persist_daily_report(
    db: Session,
    report_date: date,
    sections: dict[str, list[DailyReportCandidate]],
) -> DailyReport:
    """Persist an already-reviewed selection without rerunning selection rules."""
    report = db.scalar(select(DailyReport).where(DailyReport.report_date == report_date))
    if report is None:
        report = DailyReport(report_date=report_date, status="published")
        db.add(report)
        db.flush()
    else:
        db.execute(delete(DailyReportItem).where(DailyReportItem.report_id == report.id))
        report.status = "published"
        report.updated_at = datetime.now(UTC)

    for section, section_items in sections.items():
        for position, candidate in enumerate(section_items, start=1):
            db.add(
                DailyReportItem(
                    report_id=report.id,
                    normalized_item_id=candidate.message_id,
                    section=section,
                    position=position,
                )
            )
    db.flush()
    return report


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
