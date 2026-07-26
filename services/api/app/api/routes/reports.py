from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.report import GeneratedReport
from app.schemas.report import GeneratedReportRead, ReportGenerate, ReportReview
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.workflows.generate_report import (
    approve_report,
    generate_report_draft,
    reject_report,
)

router = APIRouter()


@router.get("", response_model=list[GeneratedReportRead])
def list_reports(
    status_filter: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
) -> list[GeneratedReport]:
    statement = select(GeneratedReport).order_by(GeneratedReport.created_at.desc()).limit(200)
    if status_filter:
        statement = statement.where(GeneratedReport.status == status_filter)
    return list(db.scalars(statement))


@router.post(
    "/generate",
    response_model=GeneratedReportRead,
    status_code=status.HTTP_201_CREATED,
)
async def generate_report(
    payload: ReportGenerate,
    db: Session = Depends(get_db),
) -> object:
    try:
        return await generate_report_draft(db, payload)
    except LLMConfigurationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    except (LLMAnalysisError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.post("/{report_id}/approve", response_model=GeneratedReportRead)
def approve_generated_report(
    report_id: int,
    payload: ReportReview,
    db: Session = Depends(get_db),
) -> GeneratedReport:
    report = db.get(GeneratedReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    try:
        return approve_report(db, report, note=payload.note)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{report_id}/reject", response_model=GeneratedReportRead)
def reject_generated_report(
    report_id: int,
    payload: ReportReview,
    db: Session = Depends(get_db),
) -> GeneratedReport:
    report = db.get(GeneratedReport, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="report not found")
    if not payload.reason:
        raise HTTPException(status_code=422, detail="reason is required when rejecting a report")
    try:
        return reject_report(db, report, reason=payload.reason)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
