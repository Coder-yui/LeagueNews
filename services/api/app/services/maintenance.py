from datetime import UTC, date, datetime

from app.core.database import SessionLocal
from app.services.event_aggregation import expire_stale_unconfirmed_events

_last_run_date: date | None = None


def run_daily_maintenance(*, as_of: datetime | None = None, force: bool = False) -> list[int]:
    """Run idempotent local maintenance at most once per worker process per UTC day."""
    global _last_run_date
    reference = as_of or datetime.now(UTC)
    current_date = reference.astimezone(UTC).date()
    if not force and _last_run_date == current_date:
        return []
    with SessionLocal() as db:
        affected = expire_stale_unconfirmed_events(db, as_of=reference)
    _last_run_date = current_date
    return affected
