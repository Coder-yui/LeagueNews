from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.daily_report import DailyReport
from app.models.normalized_item import NormalizedItem
from app.services.daily_reports import daily_report_eligibility_conditions
from app.services.published_items import published_item_payload, published_item_statement


def load_daily_report(db: Session, report_date: date) -> DailyReport | None:
    return db.scalar(
        select(DailyReport)
        .options(selectinload(DailyReport.items))
        .where(DailyReport.report_date == report_date)
    )


def daily_report_payload(db: Session, report: DailyReport) -> dict[str, Any]:
    message_ids = [item.normalized_item_id for item in report.items]
    messages: dict[int, dict[str, Any]] = {}
    if message_ids:
        statement = published_item_statement().where(
            NormalizedItem.id.in_(message_ids),
            *daily_report_eligibility_conditions(),
        )
        messages = {item.id: published_item_payload(item) for item in db.scalars(statement)}
    sections: dict[str, list[dict[str, Any]]] = {
        "lolpc": [],
        "esports": [],
        "tft": [],
        "other": [],
    }
    for report_item in report.items:
        message = messages.get(report_item.normalized_item_id)
        if message is not None:
            sections[report_item.section].append(message)
    return {
        "id": report.id,
        "report_date": report.report_date,
        "status": report.status,
        "sections": sections,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
    }


def daily_report_summary(
    report: DailyReport,
    *,
    visible_item_ids: set[int] | None = None,
) -> dict[str, Any]:
    section_counts = {"lolpc": 0, "esports": 0, "tft": 0, "other": 0}
    for item in report.items:
        if visible_item_ids is not None and item.normalized_item_id not in visible_item_ids:
            continue
        section_counts[item.section] += 1
    item_count = (
        len(report.items)
        if visible_item_ids is None
        else sum(item.normalized_item_id in visible_item_ids for item in report.items)
    )
    return {
        "id": report.id,
        "report_date": report.report_date,
        "status": report.status,
        "item_count": item_count,
        "section_counts": section_counts,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
    }


def list_daily_report_summaries(db: Session) -> list[dict[str, Any]]:
    reports = list(
        db.scalars(
            select(DailyReport)
            .options(selectinload(DailyReport.items))
            .order_by(DailyReport.report_date.desc())
            .limit(90)
        )
    )
    item_ids = {
        item.normalized_item_id
        for report in reports
        if report.status == "published"
        for item in report.items
    }
    visible_item_ids: set[int] = set()
    if item_ids:
        visible_item_ids = set(
            db.scalars(
                select(NormalizedItem.id).where(
                    NormalizedItem.id.in_(item_ids),
                    *daily_report_eligibility_conditions(),
                )
            )
        )
    return [
        daily_report_summary(
            report,
            visible_item_ids=visible_item_ids if report.status == "published" else None,
        )
        for report in reports
    ]


def get_published_daily_report(db: Session, report_date: date) -> dict[str, Any] | None:
    report = load_daily_report(db, report_date)
    if report is None or report.status != "published":
        return None
    return daily_report_payload(db, report)


def get_latest_published_daily_report(db: Session) -> dict[str, Any] | None:
    report = db.scalar(
        select(DailyReport)
        .options(selectinload(DailyReport.items))
        .where(DailyReport.status == "published")
        .order_by(DailyReport.report_date.desc())
        .limit(1)
    )
    return daily_report_payload(db, report) if report is not None else None
