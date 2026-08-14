from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.schemas.daily_report import DailyReportRead, DailyReportSummaryRead
from app.services.daily_report_read import (
    daily_report_payload,
    daily_report_summary,
    list_daily_report_summaries,
    load_daily_report,
)
from app.services.daily_reports import generate_daily_report


router = APIRouter()


def _load_report(db: Session, report_date: date):
    report = load_daily_report(db, report_date)
    if report is None:
        raise HTTPException(status_code=404, detail="daily report not found")
    return report


@router.get("/daily", response_model=list[DailyReportSummaryRead])
def list_daily_reports(db: Session = Depends(get_db)) -> list[dict[str, Any]]:
    return list_daily_report_summaries(db)


@router.get("/daily/{report_date}", response_model=DailyReportRead)
def get_daily_report(report_date: date, db: Session = Depends(get_db)) -> dict[str, Any]:
    report = _load_report(db, report_date)
    if report.status != "published":
        raise HTTPException(status_code=404, detail="daily report not published")
    return daily_report_payload(db, report)


@router.post("/daily/{report_date}/generate", response_model=DailyReportRead)
def create_daily_report(report_date: date, db: Session = Depends(get_db)) -> dict[str, Any]:
    generate_daily_report(db, report_date)
    db.commit()
    report = _load_report(db, report_date)
    return daily_report_payload(db, report)


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
    return daily_report_summary(report)
