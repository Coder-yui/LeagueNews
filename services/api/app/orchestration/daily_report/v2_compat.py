from collections.abc import Callable

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models.event import EventMention
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.orchestration.contracts import RunMode
from app.orchestration.daily_report.graph import (
    DailyCandidate,
    DailyCandidateSet,
    DailyPublication,
    DailyReportRequest,
    DailySections,
    DailyWindow,
)
from app.repositories.events import current_event_mention_conditions
from app.services.daily_reports import (
    DailyReportCandidate,
    assign_daily_sections,
    daily_report_eligibility_conditions,
    daily_report_window,
    deduplicate_daily_candidates,
    eligible_daily_candidates,
    persist_daily_report,
    rank_daily_sections,
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


class V2CompatibilityDailyReportBackend:
    """Run V2 daily-report rules as independently replaceable V3 stages."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

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
        del request
        with self._session_factory() as db:
            items = list(
                db.scalars(
                    select(NormalizedItem)
                    .join(NormalizedItem.raw_item)
                    .where(
                        *daily_report_eligibility_conditions(),
                        RawItem.published_at >= window.window_start,
                        RawItem.published_at < window.window_end,
                    )
                )
            )
            item_ids = [item.id for item in items]
            event_ids_by_item: dict[int, set[int]] = {
                item_id: set() for item_id in item_ids
            }
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
        return DailyCandidateSet(
            candidates=[
                _contract(candidate)
                for candidate in eligible_daily_candidates(candidates)
            ]
        )

    async def deduplicate_events(
        self, candidates: DailyCandidateSet
    ) -> DailyCandidateSet:
        values = deduplicate_daily_candidates(
            [_domain(candidate) for candidate in candidates.candidates]
        )
        return DailyCandidateSet(candidates=[_contract(value) for value in values])

    async def assign_sections(self, candidates: DailyCandidateSet) -> DailySections:
        sections = assign_daily_sections(
            [_domain(candidate) for candidate in candidates.candidates]
        )
        return DailySections(
            sections={
                name: [_contract(candidate) for candidate in values]
                for name, values in sections.items()
            }
        )

    async def rank_items(self, sections: DailySections) -> DailySections:
        ranked = rank_daily_sections(
            {
                name: [_domain(candidate) for candidate in values]
                for name, values in sections.sections.items()
            }
        )
        return DailySections(
            sections={
                name: [_contract(candidate) for candidate in values]
                for name, values in ranked.items()
            }
        )

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
