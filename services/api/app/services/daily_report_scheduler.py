import asyncio
import logging
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.daily_report import DailyReport
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.services.daily_reports import (
    DAILY_REPORT_TIMEZONE,
    daily_report_eligibility_conditions,
    daily_report_window,
    generate_daily_report,
)


logger = logging.getLogger(__name__)


def scheduled_generation_at(report_date: date) -> datetime:
    generation_date = (
        report_date + timedelta(days=1)
        if settings.daily_report_generation_hour == 0
        else report_date
    )
    return datetime.combine(
        generation_date,
        time(hour=settings.daily_report_generation_hour),
        tzinfo=DAILY_REPORT_TIMEZONE,
    )


def due_report_date(now: datetime | None = None) -> date | None:
    if not settings.daily_report_automation_enabled:
        return None
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_now = current.astimezone(DAILY_REPORT_TIMEZONE)
    if settings.daily_report_generation_hour == 0:
        report_date = local_now.date() - timedelta(days=1)
        if local_now < scheduled_generation_at(report_date):
            return None
        return report_date
    report_date = local_now.date()
    if local_now < scheduled_generation_at(report_date):
        return None
    return report_date


def generate_due_daily_report(
    db: Session,
    *,
    now: datetime | None = None,
) -> DailyReport | None:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    report_date = due_report_date(current)
    if report_date is None:
        return None

    if db.bind is not None and db.bind.dialect.name == "postgresql":
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
            {"identity": f"daily-report:{report_date.isoformat()}"},
        )

    scheduled_at_utc = scheduled_generation_at(report_date).astimezone(UTC)
    existing = db.scalar(
        select(DailyReport).where(DailyReport.report_date == report_date)
    )
    if existing is not None and existing.status == "withdrawn":
        return None
    if existing is not None and existing.updated_at is not None:
        updated_at = existing.updated_at
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=UTC)
        if updated_at.astimezone(UTC) >= scheduled_at_utc:
            grace_ends_at_utc = scheduled_at_utc + timedelta(
                minutes=settings.daily_report_generation_grace_minutes
            )
            if current.astimezone(UTC) > grace_ends_at_utc:
                return None
            window_start, window_end = daily_report_window(report_date)
            has_late_eligible_message = db.scalar(
                select(NormalizedItem.id)
                .join(NormalizedItem.raw_item)
                .where(
                    *daily_report_eligibility_conditions(),
                    NormalizedItem.updated_at > updated_at,
                    RawItem.published_at >= window_start,
                    RawItem.published_at < window_end,
                )
                .limit(1)
            )
            if has_late_eligible_message is None:
                return None

    window_start, window_end = daily_report_window(report_date)
    has_eligible_message = db.scalar(
        select(NormalizedItem.id)
        .join(NormalizedItem.raw_item)
        .where(
            *daily_report_eligibility_conditions(),
            RawItem.published_at >= window_start,
            RawItem.published_at < window_end,
        )
        .limit(1)
    )
    if has_eligible_message is None:
        return None

    report = generate_daily_report(db, report_date)
    report.updated_at = current.astimezone(UTC)
    db.commit()
    db.refresh(report)
    return report


def process_due_daily_report(now: datetime | None = None) -> bool:
    with SessionLocal() as db:
        return generate_due_daily_report(db, now=now) is not None


async def daily_report_scheduler_loop() -> None:
    while True:
        try:
            process_due_daily_report()
        except Exception:
            logger.exception("daily report scheduler iteration failed")
        await asyncio.sleep(settings.daily_report_scheduler_poll_seconds)
