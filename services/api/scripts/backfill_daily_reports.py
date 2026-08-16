"""Backfill V1 daily reports on a LOCAL dev DB.

Generates (or regenerates) the V1 daily report for every Shanghai calendar day
in [start, end] that currently has at least one eligible published message.
Days without any eligible message are skipped (no empty reports), matching the
scheduler's behavior. Existing reports for a day are overwritten, matching the
manual generate API.

- ``start`` is automatically the earliest eligible message day.
- ``--end`` defaults to the latest eligible message day.

SAFETY GATES (mirrors cleanup_crossfeed_raw_items.py):
  * Host must be localhost / 127.0.0.1 / ::1 / unix socket.
  * Database name must be exactly "lol_daily_intel" (the dev DB).
  * Default mode is DRY-RUN: pass --apply to actually generate.
  * Always prints per-day outcome and a final summary.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import date

from sqlalchemy import select

import app.models  # noqa: F401
from app.core.database import SessionLocal, engine
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.services.daily_reports import (
    DAILY_REPORT_TIMEZONE,
    daily_report_eligibility_conditions,
    daily_report_window,
    generate_daily_report,
)


# ---------------------------------------------------------------------------
# Safety gates
# ---------------------------------------------------------------------------


def _validate_local_database() -> None:
    print(f"[safety] DB URL     : {engine.url}")
    print(f"[safety] DB host    : {engine.url.host!r}")
    print(f"[safety] DB database: {engine.url.database!r}")
    if engine.url.host not in {"localhost", "127.0.0.1", "::1", None}:
        print(
            "[safety] ❌ REFUSING: non-local database host.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    if engine.url.database != "lol_daily_intel":
        print(
            f"[safety] ❌ REFUSING: unexpected database name {engine.url.database!r}.",
            "This script is locked to the local dev DB `lol_daily_intel`.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    print("[safety] ✅ local dev DB confirmed.")


# ---------------------------------------------------------------------------
# Candidate discovery
# ---------------------------------------------------------------------------


def _eligible_message_days() -> dict[date, int]:
    """Return {Shanghai calendar day: eligible message count} for all data."""
    with SessionLocal() as db:
        statement = (
            select(RawItem.published_at)
            .join(NormalizedItem, NormalizedItem.raw_item_id == RawItem.id)
            .where(
                *daily_report_eligibility_conditions(),
                RawItem.published_at.isnot(None),
            )
        )
        rows = db.execute(statement).scalars().all()
    counts: Counter = Counter()
    for published_at in rows:
        counts[published_at.astimezone(DAILY_REPORT_TIMEZONE).date()] += 1
    return dict(sorted(counts.items()))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Generate or regenerate V1 daily reports for every Shanghai day in "
            "[earliest eligible day, --end] that has at least one eligible "
            "message. Dry-run by default; pass --apply to write."
        )
    )
    parser.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="Last report date to generate (YYYY-MM-DD, Shanghai). "
        "Defaults to the latest eligible message day.",
    )
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    _validate_local_database()

    days = _eligible_message_days()
    if not days:
        print("No eligible published messages found; nothing to generate.")
        return

    start = min(days)
    end = args.end or max(days)
    if end < start:
        print(f"[error] --end {end.isoformat()} is before earliest day {start.isoformat()}.")
        raise SystemExit(2)

    selected = {day: count for day, count in days.items() if start <= day <= end}
    print(f"[range] earliest eligible day : {start.isoformat()}")
    print(f"[range] requested end         : {end.isoformat()}")
    print(f"[range] days with messages    : {len(selected)}")
    for day in selected:
        print(f"  - {day.isoformat()}  eligible_messages={selected[day]}")

    if not args.apply:
        print("\nDry run only. Add --apply to generate the reports above.")
        return

    generated: list[date] = []
    with SessionLocal() as db:
        for day in selected:
            window_start, window_end = daily_report_window(day)
            has_eligible = db.scalar(
                select(NormalizedItem.id)
                .join(NormalizedItem.raw_item)
                .where(
                    *daily_report_eligibility_conditions(),
                    RawItem.published_at >= window_start,
                    RawItem.published_at < window_end,
                )
                .limit(1)
            )
            if has_eligible is None:
                print(f"  - {day.isoformat()}  SKIP (no eligible message)")
                continue
            report = generate_daily_report(db, day)
            db.commit()
            generated.append(day)
            print(f"  - {day.isoformat()}  GENERATED items={len(report.items)}")

    print(f"\n[done] generated {len(generated)} report(s):")
    for day in generated:
        print(f"  - {day.isoformat()}")


if __name__ == "__main__":
    main()
