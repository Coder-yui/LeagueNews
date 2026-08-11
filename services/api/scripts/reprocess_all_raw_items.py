from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal, engine
from app.evaluation.runner import load_jsonl
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun
from app.services.automatic_pipeline import (
    enqueue_pipeline_job,
    process_next_job,
)
from app.services.raw_item_versions import latest_raw_item_condition


@dataclass(frozen=True, slots=True)
class BatchPreflight:
    database: str | None
    raw_items: int
    normalized_items: int
    processing_runs: int
    pipeline_jobs: int
    llm_configured: bool
    automation_enabled: bool


def _preflight(db: Session) -> BatchPreflight:
    return BatchPreflight(
        database=engine.url.database,
        raw_items=int(db.scalar(select(func.count()).select_from(RawItem)) or 0),
        normalized_items=int(
            db.scalar(select(func.count()).select_from(NormalizedItem)) or 0
        ),
        processing_runs=int(
            db.scalar(select(func.count()).select_from(ProcessingRun)) or 0
        ),
        pipeline_jobs=int(
            db.scalar(select(func.count()).select_from(PipelineJob)) or 0
        ),
        llm_configured=bool(settings.openai_api_key.strip()),
        automation_enabled=settings.pipeline_automation_enabled,
    )


def _validate_preflight(
    state: BatchPreflight,
    *,
    expected_database: str,
    expected_raw_items: int,
    resume: bool,
) -> None:
    if state.database != expected_database:
        raise RuntimeError(
            f"refusing batch: database is {state.database!r}, "
            f"expected {expected_database!r}"
        )
    if state.raw_items != expected_raw_items:
        raise RuntimeError(
            f"refusing batch: raw_items={state.raw_items}, "
            f"expected {expected_raw_items}"
        )
    if not state.llm_configured:
        raise RuntimeError("OPENAI_API_KEY is not configured")
    if not state.automation_enabled:
        raise RuntimeError("PIPELINE_AUTOMATION_ENABLED is false")
    if not resume and (
        state.normalized_items
        or state.processing_runs
        or state.pipeline_jobs
    ):
        raise RuntimeError(
            "refusing fresh batch because derived pipeline data already exists; "
            "reset first or pass --resume for an interrupted batch"
        )


def _raw_item_ids(db: Session) -> list[int]:
    return list(
        db.scalars(
            select(RawItem.id)
            .where(latest_raw_item_condition())
            .order_by(
                func.coalesce(RawItem.published_at, RawItem.ingested_at), RawItem.id
            )
        )
    )


def _dataset_raw_item_ids(db: Session, paths: list[Path]) -> list[int]:
    requested = {
        int(case["input"]["raw_item_id"])
        for path in paths
        for case in load_jsonl(path)
        if case.get("task") == "message_analysis"
    }
    ordered = _raw_item_ids(db)
    selected = [raw_item_id for raw_item_id in ordered if raw_item_id in requested]
    missing = requested - set(selected)
    if missing:
        raise RuntimeError(f"dataset references missing RawItems: {sorted(missing)}")
    return selected


def _latest_jobs(
    db: Session,
    raw_item_ids: list[int],
) -> dict[int, PipelineJob]:
    latest: dict[int, PipelineJob] = {}
    for job in db.scalars(
        select(PipelineJob)
        .where(PipelineJob.raw_item_id.in_(raw_item_ids))
        .order_by(PipelineJob.raw_item_id, PipelineJob.id)
    ):
        latest[job.raw_item_id] = job
    return latest


def _batch_status(raw_item_ids: list[int]) -> dict[str, int]:
    with SessionLocal() as db:
        latest = _latest_jobs(db, raw_item_ids)
        counts = Counter(
            latest[raw_item_id].status
            if raw_item_id in latest
            else "missing"
            for raw_item_id in raw_item_ids
        )
    return dict(sorted(counts.items()))


def _enqueue(
    raw_item_ids: list[int],
    *,
    retry_failed: bool,
) -> dict[str, int]:
    counters = Counter()
    with SessionLocal() as db:
        latest = _latest_jobs(db, raw_item_ids)
        for index, raw_item_id in enumerate(raw_item_ids, start=1):
            existing = latest.get(raw_item_id)
            if existing is not None and existing.status in {
                "queued",
                "running",
                "completed",
            }:
                counters[f"kept_{existing.status}"] += 1
                continue
            if (
                existing is not None
                and existing.status == "failed"
                and not retry_failed
            ):
                counters["kept_failed"] += 1
                continue
            job = enqueue_pipeline_job(db, raw_item_id=raw_item_id)
            if job is None:
                raise RuntimeError(
                    f"failed to enqueue raw_item={raw_item_id}"
                )
            counters["enqueued"] += 1
            if index % 50 == 0:
                db.commit()
        db.commit()
    return dict(sorted(counters.items()))


def _recover_running_jobs(raw_item_ids: list[int]) -> int:
    recovered = 0
    with SessionLocal() as db:
        for job in _latest_jobs(db, raw_item_ids).values():
            if job.status != "running":
                continue
            job.status = "queued"
            job.worker_id = None
            job.lease_token = None
            job.heartbeat_at = None
            job.lease_expires_at = None
            job.error_message = None
            job.completed_at = None
            recovered += 1
        db.commit()
    return recovered


async def _run_worker(
    raw_item_ids: list[int],
    *,
    idle_poll_seconds: float,
) -> None:
    while True:
        states = _batch_status(raw_item_ids)
        if not states.get("queued", 0) and not states.get("running", 0):
            return
        if not await process_next_job():
            await asyncio.sleep(idle_poll_seconds)


async def _report_status(
    raw_item_ids: list[int],
    *,
    status_interval_seconds: float,
) -> None:
    while True:
        states = _batch_status(raw_item_ids)
        print({"batch_status": states}, flush=True)
        if not states.get("queued", 0) and not states.get("running", 0):
            return
        await asyncio.sleep(status_interval_seconds)


async def _drain(
    raw_item_ids: list[int],
    *,
    workers: int,
    idle_poll_seconds: float,
    status_interval_seconds: float,
) -> dict[str, int]:
    await asyncio.gather(
        *(
            _run_worker(
                raw_item_ids,
                idle_poll_seconds=idle_poll_seconds,
            )
            for _ in range(workers)
        ),
        _report_status(
            raw_item_ids,
            status_interval_seconds=status_interval_seconds,
        ),
    )
    return _batch_status(raw_item_ids)


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Queue every RawItem and drain the current automatic pipeline from "
            "relevance through importance and message publication. Dry-run by default."
        )
    )
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument(
        "--recover-running",
        action="store_true",
        help=(
            "requeue running jobs immediately; use only after confirming every "
            "previous worker has stopped"
        ),
    )
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--expected-database", default="lol_daily_intel")
    parser.add_argument("--expected-raw-items", type=int, default=737)
    parser.add_argument(
        "--dataset",
        type=Path,
        action="append",
        help=(
            "process only RawItem ids referenced by message_analysis cases; "
            "repeat to process the union of multiple regression datasets"
        ),
    )
    parser.add_argument("--status-interval-seconds", type=float, default=15)
    parser.add_argument("--idle-poll-seconds", type=float, default=2)
    args = parser.parse_args()
    workers = max(1, min(args.workers, 8))
    with SessionLocal() as db:
        state = _preflight(db)
        raw_item_ids = (
            _dataset_raw_item_ids(db, args.dataset)
            if args.dataset
            else _raw_item_ids(db)
        )
    print(
        {
            "preflight": {
                "database": state.database,
                "raw_items": state.raw_items,
                "normalized_items": state.normalized_items,
                "processing_runs": state.processing_runs,
                "pipeline_jobs": state.pipeline_jobs,
                "llm_configured": state.llm_configured,
                "automation_enabled": state.automation_enabled,
            },
            "order": "oldest-to-newest by published_at/ingested_at",
            "workers": workers,
            "selected_raw_items": len(raw_item_ids),
            "model": settings.model_name,
        },
        flush=True,
    )
    if not args.apply:
        print(
            "Dry run only. Add --apply after downstream reset; use --resume "
            "only for an interrupted batch.",
            flush=True,
        )
        return
    _validate_preflight(
        state,
        expected_database=args.expected_database,
        expected_raw_items=args.expected_raw_items,
        resume=args.resume,
    )
    recovered = (
        _recover_running_jobs(raw_item_ids)
        if args.recover_running
        else 0
    )
    enqueue_result = _enqueue(
        raw_item_ids,
        retry_failed=args.retry_failed,
    )
    print(
        {
            "recovered_running": recovered,
            "enqueue": enqueue_result,
            "initial_status": _batch_status(raw_item_ids),
        },
        flush=True,
    )
    final = await _drain(
        raw_item_ids,
        workers=workers,
        idle_poll_seconds=max(0.25, args.idle_poll_seconds),
        status_interval_seconds=max(1.0, args.status_interval_seconds),
    )
    print({"final_status": final}, flush=True)
    if final.get("missing", 0):
        raise RuntimeError(f"batch has missing jobs: {final}")
    if final.get("failed", 0):
        raise RuntimeError(f"batch completed with failures: {final}")
    if final.get("completed", 0) != len(raw_item_ids):
        raise RuntimeError(f"batch did not complete every RawItem: {final}")


if __name__ == "__main__":
    asyncio.run(main())
