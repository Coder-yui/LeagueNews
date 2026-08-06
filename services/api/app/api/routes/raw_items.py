from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import Session, aliased, selectinload

from app.core.database import get_db
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun
from app.schemas.raw_item import RawItemAdminPageRead, RawItemRead
from app.schemas.workflow import ProcessingRunRead
from app.services.llm import LLMAnalysisError, LLMConfigurationError
from app.services.media_ocr import OCRProcessingError
from app.services.raw_item_versions import (
    is_latest_raw_item,
    latest_raw_item_condition,
)
from app.workflows.reviewed_pipeline import start_item_processing

router = APIRouter()


def _raw_item_payloads(
    db: Session,
    items: list[RawItem],
) -> list[dict[str, object]]:
    raw_item_ids = [item.id for item in items]
    jobs = (
        list(
            db.scalars(
                select(PipelineJob)
                .where(PipelineJob.raw_item_id.in_(raw_item_ids))
                .order_by(PipelineJob.raw_item_id, PipelineJob.id.desc())
            )
        )
        if raw_item_ids
        else []
    )
    latest_jobs: dict[int, PipelineJob] = {}
    for job in jobs:
        latest_jobs.setdefault(job.raw_item_id, job)

    payload: list[dict[str, object]] = []
    for item in items:
        normalized = (
            item.normalized_item
            if item.normalized_item is not None
            and item.normalized_item.publication_status == "published"
            else None
        )
        job = latest_jobs.get(item.id)
        payload.append(
            {
                "id": item.id,
                "source_id": item.source_id,
                "external_id": item.external_id,
                "native_title": item.native_title,
                "display_title": item.display_title,
                "content_kind": item.content_kind,
                "author_name": item.author_name,
                "language": item.language,
                "canonical_url": item.canonical_url,
                "content_blocks": item.content_blocks,
                "content_hash": item.content_hash,
                "content_hash_version": item.content_hash_version,
                "revision": item.revision,
                "supersedes_raw_item_id": item.supersedes_raw_item_id,
                "processing_status": item.processing_status,
                "published_at": item.published_at,
                "ingested_at": item.ingested_at,
                "source_name": item.source.name,
                "source_connector_type": item.source.connector_type,
                "normalized_item_id": normalized.id if normalized else None,
                "content_type": normalized.content_type if normalized else None,
                "summary": normalized.summary if normalized else None,
                "importance_score": normalized.importance_score if normalized else None,
                "current_pipeline_stage": job.current_stage if job else None,
                "current_pipeline_job_id": job.id if job else None,
                "current_pipeline_job_status": job.status if job else None,
                "processing_runs": sorted(
                    item.processing_runs, key=lambda run: run.id, reverse=True
                ),
            }
        )
    return payload


@router.get("", response_model=list[RawItemRead])
def list_raw_items(db: Session = Depends(get_db)) -> list[dict[str, object]]:
    statement = (
        select(RawItem)
        .options(
            selectinload(RawItem.source),
            selectinload(RawItem.normalized_item),
            selectinload(RawItem.processing_runs),
        )
        .where(latest_raw_item_condition())
        .order_by(RawItem.ingested_at.desc())
        .limit(100)
    )
    items = list(db.scalars(statement))
    return _raw_item_payloads(db, items)


@router.get("/admin-page", response_model=RawItemAdminPageRead)
def list_raw_items_admin_page(
    process_status: str = Query(default="failed", pattern="^(all|failed|processing|completed)$"),
    source_id: int | None = None,
    content_type: str | None = None,
    search: str | None = None,
    sort: str = Query(default="desc", pattern="^(asc|desc)$"),
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    latest_job_ids = (
        select(
            PipelineJob.raw_item_id,
            func.max(PipelineJob.id).label("latest_job_id"),
        )
        .group_by(PipelineJob.raw_item_id)
        .subquery()
    )
    latest_run_ids = (
        select(
            ProcessingRun.raw_item_id,
            func.max(ProcessingRun.id).label("latest_run_id"),
        )
        .group_by(ProcessingRun.raw_item_id)
        .subquery()
    )
    latest_job = aliased(PipelineJob)
    latest_run = aliased(ProcessingRun)
    joins = (
        select(RawItem.id)
        .join(Source, Source.id == RawItem.source_id)
        .outerjoin(NormalizedItem, NormalizedItem.raw_item_id == RawItem.id)
        .outerjoin(latest_job_ids, latest_job_ids.c.raw_item_id == RawItem.id)
        .outerjoin(latest_job, latest_job.id == latest_job_ids.c.latest_job_id)
        .outerjoin(latest_run_ids, latest_run_ids.c.raw_item_id == RawItem.id)
        .outerjoin(latest_run, latest_run.id == latest_run_ids.c.latest_run_id)
        .where(latest_raw_item_condition())
    )
    failed = or_(
        func.coalesce(latest_job.status == "failed", False),
        func.coalesce(latest_run.status == "failed", False),
    )
    processing = and_(
        not_(failed),
        or_(
            func.coalesce(latest_job.status.in_(["queued", "running"]), False),
            func.coalesce(
                latest_run.status.in_(["running", "awaiting_review"]), False
            ),
        ),
    )
    completed = and_(
        not_(failed),
        not_(processing),
        or_(
            NormalizedItem.publication_status == "published",
            func.coalesce(latest_job.status == "completed", False),
            func.coalesce(latest_run.status == "completed", False),
        ),
    )
    status_predicates = {
        "failed": failed,
        "processing": processing,
        "completed": completed,
    }

    filtered = joins
    if source_id is not None:
        filtered = filtered.where(RawItem.source_id == source_id)
    if content_type is not None:
        filtered = filtered.where(
            NormalizedItem.content_type.is_(None)
            if content_type == "null"
            else NormalizedItem.content_type == content_type
        )
    if search:
        pattern = f"%{search.strip()}%"
        filtered = filtered.where(
            or_(
                RawItem.native_title.ilike(pattern),
                NormalizedItem.summary.ilike(pattern),
            )
        )
    total_items = db.scalar(
        select(func.count(RawItem.id)).where(latest_raw_item_condition())
    ) or 0
    all_count = db.scalar(select(func.count()).select_from(filtered.subquery())) or 0
    status_counts = {
        name: db.scalar(
            select(func.count()).select_from(
                filtered.where(predicate).subquery()
            )
        )
        or 0
        for name, predicate in status_predicates.items()
    }
    if process_status != "all":
        filtered = filtered.where(status_predicates[process_status])
    total = db.scalar(select(func.count()).select_from(filtered.subquery())) or 0

    time_column = func.coalesce(RawItem.published_at, RawItem.ingested_at)
    ordering = (
        (time_column.asc(), RawItem.id.asc())
        if sort == "asc"
        else (time_column.desc(), RawItem.id.desc())
    )
    page_ids = filtered.order_by(*ordering).offset(offset).limit(limit).subquery()
    item_statement = (
        select(RawItem)
        .join(page_ids, page_ids.c.id == RawItem.id)
        .options(
            selectinload(RawItem.source),
            selectinload(RawItem.normalized_item),
            selectinload(RawItem.processing_runs),
        )
        .order_by(*ordering)
    )
    items = list(db.scalars(item_statement))
    source_options = [
        {"id": source_id_value, "name": source_name}
        for source_id_value, source_name in db.execute(
            select(Source.id, Source.name)
            .join(RawItem, RawItem.source_id == Source.id)
            .where(latest_raw_item_condition())
            .distinct()
            .order_by(Source.name)
        )
    ]
    content_type_options = [
        value
        for value in db.scalars(
            select(NormalizedItem.content_type)
            .distinct()
            .order_by(NormalizedItem.content_type)
        )
        if value
    ]
    if db.scalar(
        select(func.count(RawItem.id))
        .outerjoin(NormalizedItem, NormalizedItem.raw_item_id == RawItem.id)
        .where(latest_raw_item_condition(), NormalizedItem.id.is_(None))
    ):
        content_type_options.append("null")
    return {
        "items": _raw_item_payloads(db, items),
        "total": total,
        "total_items": total_items,
        "status_counts": {
            "all": all_count,
            **status_counts,
        },
        "source_options": source_options,
        "content_type_options": content_type_options,
    }


@router.post("/{item_id}/process", response_model=ProcessingRunRead)
async def process_raw_item(item_id: int, db: Session = Depends(get_db)) -> object:
    raw_item = db.get(RawItem, item_id)
    if not raw_item:
        raise HTTPException(status_code=404, detail="raw item not found")
    if not is_latest_raw_item(db, raw_item):
        raise HTTPException(
            status_code=409,
            detail="raw item has been superseded by a newer revision",
        )
    if (
        raw_item.normalized_item
        and raw_item.normalized_item.publication_status == "published"
    ):
        raise HTTPException(status_code=409, detail="raw item already has approved analysis")
    active = db.scalar(
        select(ProcessingRun).where(
            ProcessingRun.raw_item_id == item_id,
            ProcessingRun.status.in_(["running", "awaiting_review"]),
        )
    )
    if active:
        raise HTTPException(
            status_code=409,
            detail=f"raw item already has active processing run {active.id}",
        )
    try:
        return await start_item_processing(db, raw_item)
    except LLMConfigurationError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc
    except (LLMAnalysisError, OCRProcessingError, RuntimeError, ValueError) as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=str(exc),
        ) from exc
