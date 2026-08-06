from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import EventAggregationRun, EventMessage, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineJob, ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun
from app.services.claims import supersede_active_claims
from app.services.event_aggregation import refresh_event_projection


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
            PipelineJob.status.in_(["queued", "running"]),
        )
    ):
        job.status = "cancelled"
        job.error_message = reason
        job.completed_at = now
        job.worker_id = None
        job.lease_token = None
        job.lease_expires_at = None

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

    item = db.scalar(
        select(NormalizedItem).where(NormalizedItem.raw_item_id == previous.id)
    )
    if item is None:
        return []

    item.publication_status = "superseded"
    item.withdrawn_at = now
    item.withdrawal_reason = reason
    supersede_active_claims(db, normalized_item_id=item.id)

    for run in db.scalars(
        select(EventAggregationRun).where(
            EventAggregationRun.normalized_item_id == item.id,
            EventAggregationRun.status.in_(["running", "awaiting_review"]),
        )
    ):
        run.status = "superseded"
        run.outcome = "raw_item_superseded"
        run.completed_at = now
        for review in run.reviews:
            if review.status == "pending":
                review.status = "superseded"
                review.resolved_at = now

    affected_event_ids: list[int] = []
    memberships = list(
        db.scalars(
            select(EventMessage).where(
                EventMessage.normalized_item_id == item.id,
                EventMessage.membership_status == "active",
            )
        )
    )
    for membership in memberships:
        membership.membership_status = "withdrawn"
        membership.withdrawn_at = now
        membership.withdrawal_reason = reason
        event = membership.event
        event.current_revision += 1
        db.flush()
        refresh_event_projection(db, event)
        db.add(
            EventRevision(
                event_id=event.id,
                revision=event.current_revision,
                title=event.title,
                summary=event.summary,
                change_note=f"消息版本更新：{previous.id} → {successor.id}",
                evidence_snapshot={
                    "action": "supersede_raw_revision",
                    "previous_raw_item_id": previous.id,
                    "successor_raw_item_id": successor.id,
                    "normalized_item_id": item.id,
                },
            )
        )
        affected_event_ids.append(event.id)

    return sorted(set(affected_event_ids))
