from collections.abc import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.orchestration.contracts import RunMode
from app.methods import MethodAssembly, MethodAssemblyConfig
from app.orchestration.daily_report.graph import (
    DailyCandidate,
    DailyCandidateSet,
    DailyPublication,
    DailyReportRequest,
    DailySections,
    DailyWindow,
)
from app.services.daily_reports import (
    DailyReportCandidate,
    load_daily_candidates,
    daily_report_window,
    persist_daily_report,
)


SessionFactory = Callable[[], Session]


def _domain(candidate: DailyCandidate) -> DailyReportCandidate:
    return DailyReportCandidate(
        message_id=candidate.message_id,
        importance_score=candidate.importance_score,
        published_at=candidate.published_at,
        content_form=candidate.content_form,
        products=tuple(candidate.products),
        event_ids=tuple(candidate.event_ids),
    )


def _contract(candidate: DailyReportCandidate) -> DailyCandidate:
    return DailyCandidate(
        message_id=candidate.message_id,
        importance_score=candidate.importance_score,
        published_at=candidate.published_at,
        content_form=candidate.content_form,
        products=list(candidate.products),
        event_ids=list(candidate.event_ids),
    )


class DailyReportBackendV3:
    """Run the baseline daily-report rules through the canonical V3 port."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        method_assembly: MethodAssembly | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._method_assembly = method_assembly or MethodAssembly()

    def with_method_config(
        self, config: MethodAssemblyConfig
    ) -> "DailyReportBackendV3":
        return DailyReportBackendV3(
            self._session_factory,
            method_assembly=MethodAssembly(config),
        )

    async def load_window(self, request: DailyReportRequest) -> DailyWindow:
        start, end = daily_report_window(request.report_date)
        return DailyWindow(
            report_date=request.report_date,
            window_start=start,
            window_end=end,
        )

    async def select_candidates(
        self, request: DailyReportRequest, window: DailyWindow
    ) -> DailyCandidateSet:
        with self._session_factory() as db:
            candidates = load_daily_candidates(db, request.report_date)
        return DailyCandidateSet(candidates=[_contract(candidate) for candidate in candidates])

    async def plan(self, candidates: DailyCandidateSet) -> DailySections:
        plan = self._method_assembly.plan_daily_report(
            [_domain(candidate) for candidate in candidates.candidates]
        )
        return DailySections.model_validate(plan.model_dump(mode="json"))

    async def publish(
        self, request: DailyReportRequest, sections: DailySections
    ) -> DailyPublication:
        if request.run_mode != RunMode.PRODUCTION:
            raise RuntimeError("non-production daily graph attempted to publish")
        domain_sections = {
            name: [_domain(candidate) for candidate in values]
            for name, values in sections.sections.items()
        }
        with self._session_factory() as db:
            if db.bind is not None and db.bind.dialect.name == "postgresql":
                db.execute(
                    text("SELECT pg_advisory_xact_lock(hashtextextended(:identity, 0))"),
                    {"identity": f"daily-report:{request.report_date.isoformat()}"},
                )
            report = persist_daily_report(db, request.report_date, domain_sections)
            report_id = report.id
            item_count = sum(len(values) for values in domain_sections.values())
            db.commit()
        return DailyPublication(daily_report_id=report_id, item_count=item_count)

