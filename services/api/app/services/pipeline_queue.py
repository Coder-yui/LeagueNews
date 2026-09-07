from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from typing import Any

from app.core.config import settings
from app.methods import MethodAssemblyConfig
from app.models.pipeline import PipelineJob
from app.models.raw_item import RawItem
from app.orchestration.contracts import ITEM_PROCESSING_GRAPH, ITEM_PROCESSING_GRAPH_VERSION
from app.orchestration.event_aggregation.graph import (
    EVENT_AGGREGATION_GRAPH,
    EVENT_AGGREGATION_GRAPH_VERSION,
    EventAggregationStage,
)


def execution_identity_conditions(
    job: PipelineJob, *, include_self: bool = True
) -> tuple[Any, ...]:
    """Match PipelineJob executions by their authoritative target identity."""

    conditions: list[Any] = [
        PipelineJob.workflow_name == job.workflow_name,
        PipelineJob.target_entity_type == job.target_entity_type,
        PipelineJob.target_entity_id == job.target_entity_id,
        PipelineJob.target_revision == job.target_revision,
    ]
    if not include_self:
        conditions.append(PipelineJob.id != job.id)
    return tuple(conditions)


def enqueue_pipeline_job(
    db: Session,
    *,
    raw_item_id: int,
    correction_id: int | None = None,
    current_stage: str = "relevance",
    job_type: str | None = None,
    target_entity_type: str | None = None,
    target_entity_id: int | None = None,
    target_revision: int | None = None,
    workflow_name: str | None = None,
    workflow_version: str | None = None,
    method_config: Any = None,
    max_attempts: int | None = None,
) -> PipelineJob | None:
    resolved_type = job_type or "message"
    resolved_stage = current_stage
    if resolved_type == "event" and current_stage not in {
        stage.value for stage in EventAggregationStage
    }:
        resolved_stage = EventAggregationStage.LOAD_MESSAGE.value
    if not settings.pipeline_automation_enabled and resolved_type != "event":
        return None
    resolved_workflow = workflow_name or (
        EVENT_AGGREGATION_GRAPH if resolved_type == "event" else ITEM_PROCESSING_GRAPH
    )
    resolved_target_type = target_entity_type or (
        "normalized_item" if resolved_type == "event" else "raw_item"
    )
    raw_item = None
    if target_revision is None or target_entity_id is None:
        raw_item = db.get(RawItem, raw_item_id)
        if raw_item is None:
            raise ValueError(f"raw item {raw_item_id} not found")
        target = raw_item
        if resolved_target_type == "normalized_item":
            target = raw_item.normalized_item
            if target is None:
                raise ValueError(f"normalized item for raw item {raw_item_id} not found")
        if target_revision is None:
            target_revision = int(
                getattr(
                    target,
                    "current_revision" if resolved_target_type == "normalized_item" else "revision",
                    1,
                )
                or 1
            )
        if target_entity_id is None:
            target_entity_id = target.id
    if target_entity_id is None:
        raise ValueError("pipeline job target_entity_id is required")
    resolved_version = workflow_version or (
        EVENT_AGGREGATION_GRAPH_VERSION
        if resolved_workflow == EVENT_AGGREGATION_GRAPH
        else ITEM_PROCESSING_GRAPH_VERSION
    )
    if hasattr(method_config, "model_dump"):
        serialized_method_config = method_config.model_dump(mode="json")
    else:
        serialized_method_config = (
            dict(method_config)
            if method_config is not None
            else MethodAssemblyConfig.model_validate(
                settings.processing_method_config
            ).model_dump(mode="json")
        )
    active_identity = (
        PipelineJob.workflow_name == resolved_workflow,
        PipelineJob.target_entity_type == resolved_target_type,
        PipelineJob.target_entity_id == target_entity_id,
        PipelineJob.target_revision == target_revision,
        or_(
            PipelineJob.status.in_(["queued", "running"]),
            and_(
                PipelineJob.status == "failed",
                PipelineJob.next_attempt_at.is_not(None),
            ),
        ),
    )
    existing = db.scalar(select(PipelineJob).where(*active_identity))
    if existing is not None:
        return existing
    job = PipelineJob(
        raw_item_id=raw_item_id,
        correction_id=correction_id,
        status="queued",
        current_stage=resolved_stage,
        job_type=resolved_type,
        target_entity_type=resolved_target_type,
        target_entity_id=target_entity_id,
        target_revision=target_revision,
        workflow_name=resolved_workflow,
        workflow_version=resolved_version,
        method_config=serialized_method_config,
        max_attempts=max_attempts or settings.pipeline_worker_max_attempts,
    )
    try:
        with db.begin_nested():
            db.add(job)
            db.flush()
        return job
    except IntegrityError:
        return db.scalar(select(PipelineJob).where(*active_identity))
