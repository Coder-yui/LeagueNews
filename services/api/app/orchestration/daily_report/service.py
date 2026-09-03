from collections.abc import Callable
from datetime import date, datetime
from secrets import randbelow
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import SessionLocal
from app.models.daily_report import DailyReport
from app.orchestration.checkpointing import open_postgres_checkpointer
from app.orchestration.contracts import ReviewMode, RunMode
from app.orchestration.daily_report.graph import DailyReportRequest
from app.orchestration.runtime import LeagueNewsWorkflowRuntime


SessionFactory = Callable[[], Session]


def _run_id() -> int:
    return int(datetime.now().timestamp() * 1_000_000) * 1000 + randbelow(1000)


async def generate_daily_report(
    db: Session,
    report_date: date,
    *,
    workflow_run_id: int | None = None,
    session_factory: SessionFactory = SessionLocal,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> DailyReport:
    if db.bind is not None and db.bind.dialect.name != "postgresql":
        session_factory = sessionmaker(
            bind=db.bind, autoflush=False, expire_on_commit=False
        )
        checkpointer = checkpointer or InMemorySaver()
    request = DailyReportRequest(
        workflow_run_id=workflow_run_id or _run_id(),
        report_date=report_date,
        run_mode=RunMode.PRODUCTION,
        review_mode=ReviewMode.AUTOMATIC,
    )
    if checkpointer is not None:
        runtime = LeagueNewsWorkflowRuntime(
            session_factory, checkpointer=checkpointer
        )
        result = await runtime.invoke_daily_report(request)
    else:
        async with open_postgres_checkpointer() as saver:
            runtime = LeagueNewsWorkflowRuntime(
                session_factory, checkpointer=saver
            )
            result = await runtime.invoke_daily_report(request)
    if result.get("outcome") != "published":
        raise RuntimeError("daily report graph did not publish a report")
    publication = result.get("publication")
    if not isinstance(publication, dict) or not isinstance(
        publication.get("daily_report_id"), int
    ):
        raise RuntimeError("daily report graph returned an invalid publication")
    db.expire_all()
    report = db.get(DailyReport, publication["daily_report_id"])
    if report is None:
        raise RuntimeError("published daily report no longer exists")
    return report
