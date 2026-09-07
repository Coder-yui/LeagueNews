from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.config import settings
from app.models.event import EventAggregationRun
from app.models.normalized_item import NormalizedItem
from app.orchestration.checkpointing import open_postgres_checkpointer
from app.orchestration.event_aggregation.graph import (
    EventAggregationRequest,
)
from app.orchestration.event_aggregation.backend import create_event_aggregation_run
from app.orchestration.runtime import LeagueNewsWorkflowRuntime
from app.services.llm import LLMClient
from app.services.pipeline_execution import PipelineExecutionGuard
from app.methods import MethodAssemblyConfig


SessionFactory = Callable[[], Session]
LLMFactory = Callable[[], LLMClient]


async def invoke_event_aggregation(
    request: EventAggregationRequest,
    *,
    resume_existing: bool = False,
    session_factory: SessionFactory = SessionLocal,
    llm_factory: LLMFactory = LLMClient,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    execution_guard: PipelineExecutionGuard | None = None,
    method_config: MethodAssemblyConfig | None = None,
) -> dict[str, Any]:
    try:
        if checkpointer is not None:
            runtime = LeagueNewsWorkflowRuntime(
                session_factory,
                llm_factory=llm_factory,
                checkpointer=checkpointer,
                execution_guard=execution_guard,
                method_config=method_config,
            )
            return await runtime.invoke_event(
                request, resume_existing=resume_existing
            )
        async with open_postgres_checkpointer() as saver:
            runtime = LeagueNewsWorkflowRuntime(
                session_factory,
                llm_factory=llm_factory,
                checkpointer=saver,
                execution_guard=execution_guard,
                method_config=method_config,
            )
            return await runtime.invoke_event(
                request, resume_existing=resume_existing
            )
    except Exception as exc:
        with session_factory() as db:
            run = db.get(EventAggregationRun, request.workflow_run_id)
            if run is not None and run.status != "completed":
                run.status = "failed"
                run.outcome = "apply_error"
                run.error_message = str(exc)[:4000]
                run.completed_at = datetime.now(UTC)
                try:
                    if execution_guard is not None:
                        execution_guard.assert_owned(db)
                    db.commit()
                except Exception:
                    db.rollback()
        raise


async def publish_normalized_item_downstream(
    db: Session,
    item: NormalizedItem,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
    session_factory: SessionFactory = SessionLocal,
    llm_factory: LLMFactory = LLMClient,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    method_config: MethodAssemblyConfig | None = None,
) -> EventAggregationRun | None:
    if not settings.event_aggregation_enabled:
        return None
    existing = db.scalar(
        select(EventAggregationRun)
        .where(
            EventAggregationRun.normalized_item_id == item.id,
            EventAggregationRun.normalized_item_revision == item.current_revision,
        )
        .order_by(EventAggregationRun.id.desc())
        .limit(1)
    )
    if existing is not None and existing.status == "completed":
        return existing
    resolved_method_config = method_config or MethodAssemblyConfig.model_validate(
        existing.method_config if existing is not None else {}
    )
    resume_existing = existing is not None and existing.status != "completed"
    request = create_event_aggregation_run(
        session_factory,
        normalized_item_id=item.id,
        normalized_item_revision=item.current_revision,
        method_config=resolved_method_config,
        execution_guard=execution_guard,
    )
    if resume_existing:
        with session_factory() as owned_db:
            run = owned_db.get(EventAggregationRun, request.workflow_run_id)
            if run is not None:
                run.status = "running"
                run.outcome = None
                run.error_message = None
                run.completed_at = None
                owned_db.commit()
    result = await invoke_event_aggregation(
        request,
        resume_existing=resume_existing,
        session_factory=session_factory,
        llm_factory=llm_factory,
        checkpointer=checkpointer,
        execution_guard=execution_guard,
        method_config=resolved_method_config,
    )
    if result.get("__interrupt__"):
        raise RuntimeError("automatic event aggregation unexpectedly requested review")
    db.expire_all()
    run = db.get(EventAggregationRun, request.workflow_run_id)
    if run is None or run.status != "completed":
        raise RuntimeError("event graph did not produce a completed run")
    return run
