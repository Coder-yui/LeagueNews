from datetime import UTC, datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun


def supersede_previous_raw_revision(
    db: Session,
    *,
    previous: RawItem,
    successor: RawItem,
) -> list[int]:
    """Move the previous raw revision and its projections into history."""
    now = datetime.now(UTC)
    reason = f"raw item superseded by revision {successor.id}"

    for job in db.scalars(
        select(PipelineJob).where(
            PipelineJob.raw_item_id == previous.id,
            or_(
                PipelineJob.status.in_(["queued", "running"]),
                and_(
                    PipelineJob.status == "failed",
                    PipelineJob.next_attempt_at.is_not(None),
                ),
            ),
        )
    ):
        job.status = "cancelled"
        job.error_message = reason
        job.completed_at = now
        job.worker_id = None
        job.lease_token = None
        job.lease_expires_at = None
        job.heartbeat_at = None
        job.next_attempt_at = None

    for run in db.scalars(
        select(ProcessingRun).where(
            ProcessingRun.raw_item_id == previous.id,
            ProcessingRun.status.in_(["running", "awaiting_review"]),
        )
    ):
        run.status = "superseded"
        run.outcome = "raw_item_superseded"
        run.completed_at = now
        for review in run.reviews:
            if review.status == "pending":
                review.status = "superseded"
                review.resolved_at = now

    for checkpoint in db.scalars(
        select(ProcessingCheckpoint).where(
            ProcessingCheckpoint.raw_item_id == previous.id,
            ProcessingCheckpoint.invalidated_at.is_(None),
        )
    ):
        checkpoint.invalidated_at = now
        checkpoint.invalidation_reason = reason

    item = db.scalar(select(NormalizedItem).where(NormalizedItem.raw_item_id == previous.id))
    if item is None:
        return []

    item.publication_status = "superseded"
    item.withdrawn_at = now
    item.withdrawal_reason = reason
    return []
