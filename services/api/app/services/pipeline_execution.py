import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.pipeline import PipelineJob


class PipelineLeaseLost(RuntimeError):
    """Raised before a stale automatic worker can commit workflow side effects."""


@dataclass(slots=True)
class PipelineExecutionGuard:
    job_id: int
    lease_token: str
    lease_lost: asyncio.Event

    def assert_owned(self, db: Session) -> None:
        if self.lease_lost.is_set():
            raise PipelineLeaseLost(
                f"pipeline job {self.job_id} lease ownership was lost"
            )
        owned_job = db.scalar(
            select(PipelineJob)
            .where(
                PipelineJob.id == self.job_id,
                PipelineJob.status == "running",
                PipelineJob.lease_token == self.lease_token,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        lease_expires_at = owned_job.lease_expires_at if owned_job else None
        if lease_expires_at is not None and lease_expires_at.tzinfo is None:
            lease_expires_at = lease_expires_at.replace(tzinfo=UTC)
        if (
            owned_job is None
            or lease_expires_at is None
            or lease_expires_at <= datetime.now(UTC)
        ):
            self.lease_lost.set()
            raise PipelineLeaseLost(
                f"pipeline job {self.job_id} lease ownership was lost"
            )


def assert_execution_owned(
    db: Session,
    execution_guard: PipelineExecutionGuard | None,
) -> None:
    if execution_guard is not None:
        execution_guard.assert_owned(db)
