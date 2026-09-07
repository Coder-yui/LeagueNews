from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

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


def select_daily_sections(
    candidates: list[DailyReportCandidate],
) -> dict[str, list[DailyReportCandidate]]:
    """Apply V1 eligibility, event deduplication, ranking, and section limits."""
    eligible = eligible_daily_candidates(candidates)
    deduplicated = deduplicate_daily_candidates(eligible)
    return rank_daily_sections(assign_daily_sections(deduplicated))


def eligible_daily_candidates(
    candidates: list[DailyReportCandidate],
) -> list[DailyReportCandidate]:
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
    return eligible


def deduplicate_daily_candidates(
    candidates: list[DailyReportCandidate],
) -> list[DailyReportCandidate]:
    """Keep the highest-ranked message for each already-aggregated event."""

    seen_event_ids: set[int] = set()
    deduplicated: list[DailyReportCandidate] = []
    for candidate in candidates:
        event_ids = set(candidate.event_ids)
        if event_ids and event_ids & seen_event_ids:
            continue
        seen_event_ids.update(event_ids)
        deduplicated.append(candidate)
    return deduplicated


def assign_daily_sections(
    candidates: list[DailyReportCandidate],
) -> dict[str, list[DailyReportCandidate]]:
    sections = {name: [] for name in DAILY_REPORT_SECTION_LIMITS}
    for candidate in candidates:
        sections[daily_report_section(candidate.products)].append(candidate)
    return sections


def rank_daily_sections(
    sections: dict[str, list[DailyReportCandidate]],
    *,
    section_limits: dict[str, int] | None = None,
) -> dict[str, list[DailyReportCandidate]]:
    """Apply the stable V2 order and per-section limits."""
    limits = {**DAILY_REPORT_SECTION_LIMITS, **(section_limits or {})}
    return {
        section: list(candidates[: limits[section]]) for section, candidates in sections.items()
    }


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
