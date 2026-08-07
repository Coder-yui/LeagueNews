from __future__ import annotations

import argparse
import asyncio
from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.models.event import Event, EventAggregationRun, EventMessage, EventReviewTask
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.services.event_candidates import aggregation_routes
from app.services.raw_item_versions import is_latest_normalized_item
from app.workflows.event_aggregation import (
    approve_event_review,
    resume_event_aggregation,
    retry_event_aggregation,
    start_event_aggregation,
)

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _published_item_ids() -> list[int]:
    with SessionLocal() as db:
        items = list(
            db.scalars(
                select(NormalizedItem)
                .join(RawItem)
                .where(NormalizedItem.publication_status == "published")
                .order_by(
                    func.coalesce(RawItem.published_at, RawItem.ingested_at),
                    RawItem.id,
                )
            )
        )
        return [item.id for item in items if is_latest_normalized_item(db, item)]


def _preflight() -> dict[str, int | str | bool | None]:
    with SessionLocal() as db:
        return {
            "database": engine.url.database,
            "database_host": engine.url.host,
            "raw_items": int(
                db.scalar(select(func.count()).select_from(RawItem)) or 0
            ),
            "published_items": int(
                db.scalar(
                    select(func.count())
                    .select_from(NormalizedItem)
                    .where(NormalizedItem.publication_status == "published")
                )
                or 0
            ),
            "events": int(db.scalar(select(func.count()).select_from(Event)) or 0),
            "event_messages": int(
                db.scalar(select(func.count()).select_from(EventMessage)) or 0
            ),
            "event_runs": int(
                db.scalar(select(func.count()).select_from(EventAggregationRun)) or 0
            ),
            "event_reviews": int(
                db.scalar(select(func.count()).select_from(EventReviewTask)) or 0
            ),
            "event_checkpoints": int(
                db.scalar(
                    select(func.count())
                    .select_from(ProcessingCheckpoint)
                    .where(ProcessingCheckpoint.stage == "event_decision")
                )
                or 0
            ),
            "active_pipeline_jobs": int(
                db.scalar(
                    select(func.count())
                    .select_from(PipelineJob)
                    .where(PipelineJob.status.in_(["queued", "running"]))
                )
                or 0
            ),
            "llm_configured": bool(settings.openai_api_key.strip()),
        }


def _validate_preflight(
    state: dict[str, int | str | bool | None],
    *,
    expected_database: str,
    expected_raw_items: int,
    expected_published_items: int,
    resume: bool,
) -> None:
    if state["database"] != expected_database:
        raise RuntimeError(
            f"refusing event batch: database is {state['database']!r}, "
            f"expected {expected_database!r}"
        )
    if state["database_host"] not in _LOCAL_HOSTS:
        raise RuntimeError(
            f"refusing event batch: database host {state['database_host']!r} is not local"
        )
    if state["raw_items"] != expected_raw_items:
        raise RuntimeError(
            f"refusing event batch: raw_items={state['raw_items']}, "
            f"expected {expected_raw_items}"
        )
    if state["published_items"] != expected_published_items:
        raise RuntimeError(
            f"refusing event batch: published_items={state['published_items']}, "
            f"expected {expected_published_items}"
        )
    if state["active_pipeline_jobs"]:
        raise RuntimeError("refusing event batch while pipeline jobs are active")
    if not state["llm_configured"]:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    if not resume and any(
        int(state[key] or 0)
        for key in (
            "events",
            "event_messages",
            "event_runs",
            "event_reviews",
            "event_checkpoints",
        )
    ):
        raise RuntimeError(
            "refusing fresh event batch because event-layer data exists; "
            "reset first or use --resume for an interrupted batch"
        )


def _route_preview(item_ids: list[int]) -> dict[str, object]:
    distribution: Counter[int] = Counter()
    route_kinds: Counter[str] = Counter()
    with SessionLocal() as db:
        items = list(
            db.scalars(
                select(NormalizedItem)
                .where(NormalizedItem.id.in_(item_ids))
                .options(
                    selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
                    selectinload(NormalizedItem.claims),
                )
            )
        )
        by_id = {item.id: item for item in items}
        for item_id in item_ids:
            routes = aggregation_routes(by_id[item_id])
            distribution[len(routes)] += 1
            route_kinds.update(route.event_kind for route in routes)
    return {
        "routes_per_item": dict(sorted(distribution.items())),
        "route_kinds": dict(sorted(route_kinds.items())),
        "total_routes": sum(count * routes for routes, count in distribution.items()),
    }


def _pending_review(db: object, run_id: int) -> EventReviewTask | None:
    return db.scalar(
        select(EventReviewTask)
        .where(
            EventReviewTask.event_aggregation_run_id == run_id,
            EventReviewTask.status == "pending",
        )
        .order_by(EventReviewTask.id.desc())
        .limit(1)
    )


async def _process_item(item_id: int) -> str:
    with SessionLocal() as db:
        if db.scalar(
            select(EventMessage).where(
                EventMessage.normalized_item_id == item_id,
                EventMessage.membership_status == "active",
            )
        ):
            return "kept_membership"
        run = db.scalar(
            select(EventAggregationRun)
            .where(EventAggregationRun.normalized_item_id == item_id)
            .order_by(EventAggregationRun.id.desc())
            .limit(1)
        )
        if run is not None and run.status == "completed":
            return "kept_completed"
        if run is not None and run.status in {"failed", "rejected"}:
            run = await retry_event_aggregation(db, run)
        elif run is not None and run.status == "running":
            run = await resume_event_aggregation(db, run)
        elif run is None:
            item = db.get(NormalizedItem, item_id)
            if item is None:
                raise RuntimeError(f"normalized item {item_id} disappeared")
            run = await start_event_aggregation(
                db,
                item,
                execution_mode="automatic",
            )
        if run.status == "awaiting_review":
            review = _pending_review(db, run.id)
            if review is None:
                raise RuntimeError(f"event run {run.id} has no pending review")
            review.decision_source = "automatic"
            review.policy_version = "topic-cluster-v1"
            db.commit()
            run = approve_event_review(
                db,
                review,
                note="full local topic-cluster reaggregation",
            )
        if run.status != "completed":
            raise RuntimeError(
                f"event run {run.id} stopped with status={run.status}"
            )
        return str(run.outcome or "completed")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Re-run only event aggregation for every latest published "
            "NormalizedItem, oldest first. Dry-run by default."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument(
        "--confirm",
        choices=["reaggregate-event-data"],
        help="required together with --apply",
    )
    parser.add_argument("--expected-database", default="lol_daily_intel")
    parser.add_argument("--expected-raw-items", type=int, default=737)
    parser.add_argument("--expected-published-items", type=int, default=64)
    args = parser.parse_args()

    state = _preflight()
    item_ids = _published_item_ids()
    preview = _route_preview(item_ids)
    print(
        {
            "preflight": state,
            "selected_latest_published_items": len(item_ids),
            "order": "oldest-to-newest by published_at/ingested_at",
            "model": settings.model_name,
            "route_preview": preview,
        },
        flush=True,
    )
    if not args.apply:
        print(
            "Dry run only. Add --apply --confirm reaggregate-event-data "
            "after the event layer has been reset.",
            flush=True,
        )
        return
    if args.confirm != "reaggregate-event-data":
        raise RuntimeError(
            "--apply requires --confirm reaggregate-event-data"
        )
    _validate_preflight(
        state,
        expected_database=args.expected_database,
        expected_raw_items=args.expected_raw_items,
        expected_published_items=args.expected_published_items,
        resume=args.resume,
    )
    outcomes: Counter[str] = Counter()
    for index, item_id in enumerate(item_ids, start=1):
        outcomes[await _process_item(item_id)] += 1
        if index % 5 == 0 or index == len(item_ids):
            print(
                {
                    "progress": f"{index}/{len(item_ids)}",
                    "outcomes": dict(sorted(outcomes.items())),
                },
                flush=True,
            )
    final = _preflight()
    with SessionLocal() as db:
        completed_items = int(
            db.scalar(
                select(func.count(func.distinct(EventAggregationRun.normalized_item_id)))
                .where(EventAggregationRun.status == "completed")
            )
            or 0
        )
    if completed_items != len(item_ids):
        raise RuntimeError(
            f"completed event items={completed_items}, expected {len(item_ids)}"
        )
    print(
        {
            "final": final,
            "completed_items": completed_items,
            "outcomes": dict(sorted(outcomes.items())),
        },
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(main())
