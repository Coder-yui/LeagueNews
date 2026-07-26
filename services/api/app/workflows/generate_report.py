from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.event_revision import EventRevision
from app.models.news_event import NewsEvent
from app.models.report import GeneratedReport
from app.schemas.report import ReportGenerate
from app.services.llm import LLMClient


async def generate_report_draft(
    db: Session,
    payload: ReportGenerate,
) -> GeneratedReport:
    events = list(
        db.scalars(
            select(NewsEvent)
            .where(
                or_(
                    NewsEvent.first_published_at.between(
                        payload.period_start, payload.period_end
                    ),
                    NewsEvent.last_activity_at.between(
                        payload.period_start, payload.period_end
                    ),
                )
            )
            .order_by(
                NewsEvent.importance_score.desc(),
                NewsEvent.last_activity_at.desc(),
            )
        )
    )
    if not events:
        raise ValueError("report period has no events or event updates")
    event_payloads: list[dict[str, Any]] = []
    revision_ids: list[int] = []
    for event in events:
        latest_revision = db.scalar(
            select(EventRevision)
            .where(EventRevision.event_id == event.id)
            .order_by(EventRevision.version.desc())
            .limit(1)
        )
        if latest_revision:
            revision_ids.append(latest_revision.id)
        event_payloads.append(
            {
                "id": event.id,
                "title": event.title,
                "summary": event.summary,
                "category": event.category,
                "event_type": event.event_type,
                "importance_score": event.importance_score,
                "credibility": event.credibility,
                "first_published_at": _iso(event.first_published_at),
                "last_activity_at": _iso(event.last_activity_at),
                "is_new_in_period": bool(
                    event.first_published_at
                    and payload.period_start
                    <= event.first_published_at
                    <= payload.period_end
                ),
            }
        )
    draft = await LLMClient().generate_report(
        report_type=payload.report_type,
        period_start=payload.period_start.isoformat(),
        period_end=payload.period_end.isoformat(),
        timezone=payload.timezone,
        events=event_payloads,
    )
    report = GeneratedReport(
        report_type=payload.report_type,
        timezone=payload.timezone,
        period_start=payload.period_start,
        period_end=payload.period_end,
        status="pending_review",
        title=draft.title,
        content=draft.content,
        source_event_ids=[event.id for event in events],
        source_revision_ids=revision_ids,
        generation_context={"event_count": len(events)},
        model_name=settings.model_name,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


def approve_report(
    db: Session,
    report: GeneratedReport,
    *,
    note: str | None,
) -> GeneratedReport:
    if report.status != "pending_review":
        raise ValueError(f"report cannot be approved from status={report.status}")
    report.status = "approved"
    report.review_feedback = {"note": note} if note else {}
    report.approved_at = datetime.now(UTC)
    db.commit()
    db.refresh(report)
    return report


def reject_report(
    db: Session,
    report: GeneratedReport,
    *,
    reason: str,
) -> GeneratedReport:
    if report.status != "pending_review":
        raise ValueError(f"report cannot be rejected from status={report.status}")
    report.status = "rejected"
    report.review_feedback = {"reason": reason}
    db.commit()
    db.refresh(report)
    return report


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None

