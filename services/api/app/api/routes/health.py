from fastapi import APIRouter, Depends
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.collection_schedule import SourceCollectionSchedule
from app.models.connector_run import ConnectorRun
from app.models.media_asset import MediaAsset
from app.models.media_extraction import MediaExtraction
from app.models.pipeline import PipelineJob, ProcessingCheckpoint
from app.models.workflow import ProcessingRun

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)) -> dict[str, str]:
    db.execute(text("SELECT 1"))
    return {"status": "ready"}


@router.get("/metrics")
def operational_metrics(db: Session = Depends(get_db)) -> dict[str, object]:
    now = datetime.now(UTC)
    job_counts = {
        status: count
        for status, count in db.execute(
            select(PipelineJob.status, func.count(PipelineJob.id)).group_by(
                PipelineJob.status
            )
        )
    }
    oldest_queued = db.scalar(
        select(func.min(PipelineJob.created_at)).where(
            PipelineJob.status == "queued"
        )
    )
    checkpoints = list(
        db.scalars(
            select(ProcessingCheckpoint)
            .order_by(ProcessingCheckpoint.id.desc())
            .limit(1000)
        )
    )
    llm_traces = [
        trace
        for checkpoint in checkpoints
        for trace in _execution_traces(
            checkpoint.output_snapshot.get("_execution_metadata")
        )
    ]
    return {
        "generated_at": now.isoformat(),
        "pipeline": {
            "counts": job_counts,
            "oldest_queued_at": (
                oldest_queued.isoformat() if oldest_queued else None
            ),
            "stale_lease_recoveries": db.scalar(
                select(func.coalesce(func.sum(PipelineJob.recovery_count), 0))
            ),
        },
        "collection": {
            "truncated_runs": db.scalar(
                select(func.count(ConnectorRun.id)).where(
                    ConnectorRun.truncated.is_(True)
                )
            ),
            "sources": [
                {
                    "source_id": schedule.source_id,
                    "last_success_at": (
                        schedule.last_success_at.isoformat()
                        if schedule.last_success_at
                        else None
                    ),
                    "consecutive_failures": schedule.consecutive_failures,
                    "last_status": schedule.last_status,
                }
                for schedule in db.scalars(
                    select(SourceCollectionSchedule).order_by(
                        SourceCollectionSchedule.source_id
                    )
                )
            ],
        },
        "processing": {
            "by_stage_status": [
                {"stage": stage, "status": status, "count": count}
                for stage, status, count in db.execute(
                    select(
                        ProcessingRun.current_stage,
                        ProcessingRun.status,
                        func.count(ProcessingRun.id),
                    ).group_by(
                        ProcessingRun.current_stage, ProcessingRun.status
                    )
                )
            ],
            "llm_calls_in_last_1000_checkpoints": len(llm_traces),
            "llm_total_tokens_in_last_1000_checkpoints": sum(
                int(trace.get("usage", {}).get("total_tokens", 0))
                for trace in llm_traces
                if isinstance(trace.get("usage"), dict)
            ),
            "llm_average_latency_ms": (
                sum(float(trace.get("latency_ms", 0)) for trace in llm_traces)
                / len(llm_traces)
                if llm_traces
                else 0
            ),
            "ocr_by_status": [
                {"status": status, "count": count}
                for status, count in db.execute(
                    select(
                        MediaExtraction.status,
                        func.count(MediaExtraction.id),
                    ).group_by(MediaExtraction.status)
                )
            ],
            "media_by_visibility": [
                {"visibility": visibility, "count": count}
                for visibility, count in db.execute(
                    select(
                        MediaAsset.visibility,
                        func.count(MediaAsset.id),
                    ).group_by(MediaAsset.visibility)
                )
            ],
        },
    }


def _execution_traces(value: object) -> list[dict[str, object]]:
    if not isinstance(value, dict):
        return []
    if "model" in value or "prompt_name" in value:
        return [value]
    return [
        nested
        for child in value.values()
        for nested in _execution_traces(child)
    ]
