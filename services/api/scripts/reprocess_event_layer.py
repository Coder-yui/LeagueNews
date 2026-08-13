from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.services.llm import LLMAnalysisError
from app.workflows.event_aggregation import aggregate_normalized_item


def _validate_local_database(expected_database: str) -> None:
    if engine.url.host not in {"localhost", "127.0.0.1", "::1"}:
        raise RuntimeError(f"refusing event replay for non-local host: {engine.url.host}")
    if engine.url.database != expected_database:
        raise RuntimeError(
            f"refusing event replay for database {engine.url.database!r}; "
            f"expected {expected_database!r}"
        )
    if not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY is not configured")


def _published_item_ids() -> list[int]:
    with SessionLocal() as db:
        return list(
            db.scalars(
                select(NormalizedItem.id)
                .join(RawItem, RawItem.id == NormalizedItem.raw_item_id)
                .where(NormalizedItem.publication_status == "published")
                .order_by(
                    func.coalesce(RawItem.published_at, RawItem.ingested_at),
                    RawItem.id,
                    NormalizedItem.id,
                )
            )
        )


async def _reprocess(item_ids: list[int]) -> Counter[str]:
    outcomes: Counter[str] = Counter()
    for index, item_id in enumerate(item_ids, start=1):
        with SessionLocal() as db:
            item = db.scalar(
                select(NormalizedItem)
                .where(NormalizedItem.id == item_id)
                .options(
                    selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
                    selectinload(NormalizedItem.media_links),
                )
            )
            if item is None:
                raise RuntimeError(f"NormalizedItem disappeared during replay: {item_id}")
            try:
                run = await aggregate_normalized_item(db, item)
            except LLMAnalysisError as exc:
                outcomes["failed"] += 1
                print(
                    {"item_id": item_id, "outcome": "failed", "error": str(exc)},
                    flush=True,
                )
            else:
                outcomes[str(run.outcome or run.status)] += 1
        if index % 25 == 0 or index == len(item_ids):
            print(
                {"processed": index, "total": len(item_ids), "outcomes": dict(outcomes)},
                flush=True,
            )
    return outcomes


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Replay published NormalizedItems through Event Aggregation oldest-first."
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-database", default="lol_daily_intel")
    args = parser.parse_args()
    _validate_local_database(args.expected_database)
    item_ids = _published_item_ids()
    print(
        {
            "database": engine.url.database,
            "published_items": len(item_ids),
            "order": "oldest-to-newest by published_at/ingested_at",
            "model": settings.model_name,
            "apply": args.apply,
        },
        flush=True,
    )
    if not args.apply:
        print("Dry run only. Add --apply after resetting the Event layer.", flush=True)
        return
    await _reprocess(item_ids)


if __name__ == "__main__":
    asyncio.run(main())
