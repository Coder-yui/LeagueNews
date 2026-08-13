from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select, text
from sqlalchemy.orm import Session

from app.models.daily_report import DailyReport, DailyReportItem
from app.models.event import EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.repositories.events import current_event_mention_conditions
from app.services.raw_item_versions import latest_normalized_item_condition

DAILY_REPORT_TIMEZONE = ZoneInfo("Asia/Shanghai")
DAILY_REPORT_MIN_IMPORTANCE = 0.60
DAILY_REPORT_SECTION_LIMITS = {"lolpc": 5, "esports": 3, "tft": 3, "other": 3}


@dataclass(frozen=True, slots=True)
class DailyReportCandidate:
    message_id: int
    importance_score: float
    published_at: datetime
    content_form: str
    products: tuple[str, ...]
    event_ids: tuple[int, ...] = ()


def daily_report_window(report_date: date) -> tuple[datetime, datetime]:
    """Return the UTC half-open window for a Shanghai calendar day."""
    start = datetime.combine(report_date, time.min, tzinfo=DAILY_REPORT_TIMEZONE)
    return start.astimezone(UTC), (start + timedelta(days=1)).astimezone(UTC)


def daily_report_section(products: tuple[str, ...] | list[str]) -> str:
    """Assign one stable section to a message with possibly multiple products."""
    product_set = set(products)
    if "lol_esports" in product_set:
        return "esports"
    if "lol_pc" in product_set:
        return "lolpc"
    if "tft" in product_set:
        return "tft"
    return "other"


def daily_report_eligibility_conditions():
    """Return the shared current-projection eligibility contract for V1 reports."""

    return (
        latest_normalized_item_condition(),
        NormalizedItem.publication_status == "published",
        NormalizedItem.content_form == "original",
        NormalizedItem.importance_score >= DAILY_REPORT_MIN_IMPORTANCE,
    )


def select_daily_sections(
    candidates: list[DailyReportCandidate],
) -> dict[str, list[DailyReportCandidate]]:
    """Apply V1 eligibility, event deduplication, ranking, and section limits."""
    eligible = [
        candidate
        for candidate in candidates
        if candidate.content_form == "original"
        and candidate.importance_score >= DAILY_REPORT_MIN_IMPORTANCE
    ]
    eligible.sort(
        key=lambda candidate: (
            candidate.importance_score,
            _as_utc(candidate.published_at),
            candidate.message_id,
        ),
        reverse=True,
    )

    seen_event_ids: set[int] = set()
    deduplicated: list[DailyReportCandidate] = []
    for candidate in eligible:
        event_ids = set(candidate.event_ids)
        if event_ids and event_ids & seen_event_ids:
            continue
        seen_event_ids.update(event_ids)
        deduplicated.append(candidate)

    sections = {name: [] for name in DAILY_REPORT_SECTION_LIMITS}
    for candidate in deduplicated:
        section = daily_report_section(candidate.products)
        if len(sections[section]) < DAILY_REPORT_SECTION_LIMITS[section]:
            sections[section].append(candidate)
    return sections


def generate_daily_report(db: Session, report_date: date) -> DailyReport:
    """Generate or replace one persisted report for a Shanghai calendar day."""
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"daily-report:{report_date.isoformat()}"},
        )
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
    sections = select_daily_sections(candidates)

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
