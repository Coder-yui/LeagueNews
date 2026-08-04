import argparse
import asyncio
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import app.models  # noqa: F401
from app.core.config import settings
from app.core.database import SessionLocal
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.schemas.pipeline import PipelineCorrectionCreate
from app.services.automatic_pipeline import enqueue_pipeline_job, process_next_job
from app.services.pipeline_corrections import create_and_start_correction


def _recent_items(db: Session, limit: int) -> list[RawItem]:
    newest = list(
        db.scalars(
            select(RawItem)
            .options(
                selectinload(RawItem.normalized_item),
                selectinload(RawItem.processing_runs),
            )
            .order_by(
                func.coalesce(RawItem.published_at, RawItem.ingested_at).desc(),
                RawItem.id.desc(),
            )
            .limit(limit)
        )
    )
    return list(reversed(newest))


async def _prepare(limit: int) -> tuple[list[int], int, int]:
    raw_item_ids: list[int] = []
    corrections = 0
    fresh_jobs = 0
    with SessionLocal() as db:
        items = _recent_items(db, limit)
        for index, raw_item in enumerate(items, start=1):
            raw_item_ids.append(raw_item.id)
            active_job = db.scalar(
                select(PipelineJob).where(
                    PipelineJob.raw_item_id == raw_item.id,
                    PipelineJob.status.in_(["queued", "running"]),
                )
            )
            if active_job is not None:
                print(
                    f"[{index}/{len(items)}] raw_item={raw_item.id} "
                    f"already has active job={active_job.id}"
                )
                continue
            item = raw_item.normalized_item
            if item is not None and item.publication_status == "published":
                correction = await create_and_start_correction(
                    db,
                    item=item,
                    payload=PipelineCorrectionCreate(
                        restart_from_stage="relevance",
                        resume_mode="automatic",
                        reason="local evaluation: reprocess latest batch with current pipeline",
                    ),
                )
                corrections += 1
                print(
                    f"[{index}/{len(items)}] raw_item={raw_item.id} "
                    f"correction={correction.id} queued"
                )
            else:
                job = enqueue_pipeline_job(db, raw_item_id=raw_item.id)
                db.commit()
                if job is not None:
                    fresh_jobs += 1
                    print(
                        f"[{index}/{len(items)}] raw_item={raw_item.id} "
                        f"job={job.id} queued"
                    )
    return raw_item_ids, corrections, fresh_jobs


def _batch_status(raw_item_ids: list[int]) -> dict[str, int]:
    with SessionLocal() as db:
        latest_jobs = [
            db.scalar(
                select(PipelineJob)
                .where(PipelineJob.raw_item_id == raw_item_id)
                .order_by(PipelineJob.id.desc())
                .limit(1)
            )
            for raw_item_id in raw_item_ids
        ]
        result: dict[str, int] = {}
        for job in latest_jobs:
            status = job.status if job is not None else "missing"
            result[status] = result.get(status, 0) + 1
        return result


async def _drain(raw_item_ids: list[int]) -> dict[str, int]:
    processed = 0
    while True:
        states = _batch_status(raw_item_ids)
        active = states.get("queued", 0) + states.get("running", 0)
        print(f"batch_status={states}")
        if active == 0:
            return states
        if not await process_next_job():
            return _batch_status(raw_item_ids)
        processed += 1
        print(f"processed_jobs={processed} at={datetime.now().isoformat(timespec='seconds')}")


async def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Reprocess the newest RawItems in event-time order while preserving "
            "revisions and correction provenance. Dry-run by default."
        )
    )
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--drain", action="store_true")
    args = parser.parse_args()
    limit = max(1, min(args.limit, 1000))
    with SessionLocal() as db:
        items = _recent_items(db, limit)
        published = sum(
            item.normalized_item is not None
            and item.normalized_item.publication_status == "published"
            for item in items
        )
        print(
            {
                "selected": len(items),
                "ordered": "oldest-to-newest within newest batch",
                "published_corrections": published,
                "unpublished_rechecks": len(items) - published,
                "llm_configured": bool(settings.openai_api_key.strip()),
                "model": settings.model_name,
            }
        )
    if not args.apply:
        print("Dry run only. Add --apply --drain to execute sequentially.")
        return
    if not settings.openai_api_key.strip():
        raise RuntimeError("OPENAI_API_KEY is not configured")
    raw_item_ids, corrections, fresh_jobs = await _prepare(limit)
    print({"corrections": corrections, "fresh_jobs": fresh_jobs})
    if args.drain:
        final = await _drain(raw_item_ids)
        if final.get("queued", 0) or final.get("running", 0):
            raise RuntimeError(f"batch did not drain: {final}")
        if final.get("failed", 0):
            raise RuntimeError(f"batch completed with failures: {final}")


if __name__ == "__main__":
    asyncio.run(main())
