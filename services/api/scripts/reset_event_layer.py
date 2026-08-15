from __future__ import annotations

import argparse

from sqlalchemy import delete, func, select

import app.models  # noqa: F401
from app.core.database import SessionLocal, engine
from app.models.event import Event, EventAggregationRun, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem


def _counts(db) -> dict[str, int]:
    return {
        "raw_items": int(db.scalar(select(func.count()).select_from(RawItem)) or 0),
        "normalized_items": int(
            db.scalar(select(func.count()).select_from(NormalizedItem)) or 0
        ),
        "events": int(db.scalar(select(func.count()).select_from(Event)) or 0),
        "event_mentions": int(
            db.scalar(select(func.count()).select_from(EventMention)) or 0
        ),
        "event_revisions": int(
            db.scalar(select(func.count()).select_from(EventRevision)) or 0
        ),
        "event_aggregation_runs": int(
            db.scalar(select(func.count()).select_from(EventAggregationRun)) or 0
        ),
    }


def _validate_local_database() -> None:
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"refusing reset for non-local database host: {engine.url.host}")
    if engine.url.database != "lol_daily_intel":
        raise RuntimeError(f"refusing unexpected database: {engine.url.database}")


def reset_event_layer(*, apply: bool) -> None:
    _validate_local_database()
    with SessionLocal() as db:
        before = _counts(db)
        print({"database": engine.url.database, "before": before, "apply": apply})
        if not apply:
            print("Dry run only. Add --apply to delete Event layer rows.")
            return

        try:
            # Delete children first so the operation is valid on fresh and upgraded DBs.
            db.execute(delete(EventMention))
            db.execute(delete(EventRevision))
            db.execute(delete(Event))
            db.execute(delete(EventAggregationRun))
            db.flush()
            after = _counts(db)
            if after["raw_items"] != before["raw_items"]:
                raise RuntimeError("RawItem count changed during event reset")
            if after["normalized_items"] != before["normalized_items"]:
                raise RuntimeError("NormalizedItem count changed during event reset")
            expected_zero = ("events", "event_mentions", "event_revisions", "event_aggregation_runs")
            if any(after[key] != 0 for key in expected_zero):
                raise RuntimeError(f"Event layer was not fully cleared: {after}")
            db.commit()
        except Exception:
            db.rollback()
            raise
        print({"after": after})


def main() -> None:
    parser = argparse.ArgumentParser(description="Reset local Event aggregation data only.")
    parser.add_argument("--apply", action="store_true", help="perform the reset")
    parser.add_argument("--yes", action="store_true", help="skip the exact interactive confirmation")
    args = parser.parse_args()
    if args.yes and not args.apply:
        parser.error("--yes requires --apply")
    if args.apply and not args.yes:
        expected = "yes, reset event layer"
        if input(f"Type exactly to continue: {expected}\n> ").strip() != expected:
            print("Aborted. No changes made.")
            return
    reset_event_layer(apply=args.apply)


if __name__ == "__main__":
    main()
