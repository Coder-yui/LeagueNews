from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.routes.normalized_items import _published_payload, _published_statement
from app.core.database import get_db
from app.models.daily_report import DailyReport
from app.models.normalized_item import NormalizedItem
from app.schemas.daily_report import DailyReportRead, DailyReportSummaryRead
from app.services.raw_item_versions import latest_normalized_item_condition
from app.services.daily_reports import generate_daily_report

router = APIRouter()


def _daily_report_payload(db: Session, report: DailyReport) -> dict[str, Any]:
    message_ids = [item.normalized_item_id for item in report.items]
    messages = {}
    if message_ids:
        statement = _published_statement().where(
            NormalizedItem.id.in_(message_ids),
            latest_normalized_item_condition(),
            NormalizedItem.publication_status == "published",
        )
        messages = {item.id: _published_payload(item) for item in db.scalars(statement)}
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


def _load_report(db: Session, report_date: date) -> DailyReport:
    report = db.scalar(
        select(DailyReport)
        .options(selectinload(DailyReport.items))
        .where(DailyReport.report_date == report_date)
    )
    if report is None:
        raise HTTPException(status_code=404, detail="daily report not found")
    return report


def _daily_report_summary(report: DailyReport) -> dict[str, Any]:
    section_counts = {"lolpc": 0, "esports": 0, "tft": 0, "other": 0}
    for item in report.items:
        section_counts[item.section] += 1
    return {
        "id": report.id,
        "report_date": report.report_date,
        "status": report.status,
        "item_count": len(report.items),
        "section_counts": section_counts,
        "created_at": report.created_at,
        "updated_at": report.updated_at,
    }


@router.get("/daily", response_model=list[DailyReportSummaryRead])
def list_daily_reports(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    reports = db.scalars(
        select(DailyReport)
        .options(selectinload(DailyReport.items))
        .order_by(DailyReport.report_date.desc())
        .limit(90)
    )
    return [_daily_report_summary(report) for report in reports]


@router.get("/daily/{report_date}", response_model=DailyReportRead)
def get_daily_report(report_date: date, db: Session = Depends(get_db)) -> dict[str, Any]:
    report = _load_report(db, report_date)
    if report.status != "published":
        raise HTTPException(status_code=404, detail="daily report not published")
    return _daily_report_payload(db, report)


@router.post("/daily/{report_date}/generate", response_model=DailyReportRead)
def create_daily_report(report_date: date, db: Session = Depends(get_db)) -> dict[str, Any]:
    generate_daily_report(db, report_date)
    db.commit()
    return _daily_report_payload(db, _load_report(db, report_date))


@router.post("/daily/{report_date}/withdraw", response_model=DailyReportSummaryRead)
def withdraw_daily_report(
    report_date: date,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    report = _load_report(db, report_date)
    report.status = "withdrawn"
    report.updated_at = datetime.now(UTC)
    db.commit()
    db.refresh(report)
    return _daily_report_summary(report)
