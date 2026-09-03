from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.pipeline import PipelineCorrection
from app.models.raw_item import RawItem
from app.models.workflow import ProcessingRun, ReviewTask
from app.orchestration.checkpointing import open_postgres_checkpointer
from app.orchestration.contracts import (
    ITEM_PROCESSING_GRAPH,
    ITEM_PROCESSING_GRAPH_VERSION,
    ITEM_PROCESSING_STATE_VERSION,
    ItemProcessingRequest,
    ProcessingStage,
    ReviewDecision,
    ReviewMode,
    RunMode,
)
from app.orchestration.item_processing.v2_compat import create_item_processing_run
from app.orchestration.runtime import LeagueNewsWorkflowRuntime
from app.services.llm import LLMClient
from app.services.pipeline_execution import PipelineExecutionGuard


SessionFactory = Callable[[], Session]
LLMFactory = Callable[[], LLMClient]
CheckpointerContextFactory = Callable[[], AbstractAsyncContextManager[BaseCheckpointSaver[Any]]]


def _stage(value: str | None) -> ProcessingStage:
    if value == "image_ocr":
        return ProcessingStage.MEDIA
    return ProcessingStage(value or ProcessingStage.EVIDENCE.value)


def request_for_processing_run(db: Session, run: ProcessingRun) -> ItemProcessingRequest:
    if run.workflow_type != "item":
        raise ValueError("processing run is not an item workflow")
    if run.graph_name != ITEM_PROCESSING_GRAPH:
        raise ValueError("processing run was not created by the V3 item graph")
    if run.graph_version != ITEM_PROCESSING_GRAPH_VERSION:
        raise ValueError("processing run graph version is not supported")
    if run.state_version != ITEM_PROCESSING_STATE_VERSION:
        raise ValueError("processing run state version is not supported")
    raw_item = db.get(RawItem, run.raw_item_id)
    if raw_item is None:
        raise ValueError("processing run raw item no longer exists")
    restart_from_stage = _stage(run.restart_from_stage)
    replay_from_run_id = (
        run.supersedes_run_id
        if restart_from_stage != ProcessingStage.EVIDENCE
        else None
    )
    request = ItemProcessingRequest(
        workflow_run_id=run.id,
        raw_item_id=run.raw_item_id,
        raw_item_revision=raw_item.revision,
        run_mode=RunMode.PRODUCTION,
        review_mode=ReviewMode(run.execution_mode),
        restart_from_stage=restart_from_stage,
        replay_from_run_id=replay_from_run_id,
        graph_version=run.graph_version,
        state_version=run.state_version,
    )
    if run.thread_id != request.thread_id:
        raise ValueError("processing run thread id does not match its V3 request")
    return request


def _interrupt_value(result: dict[str, Any]) -> dict[str, Any] | None:
    interrupts = result.get("__interrupt__")
    if not interrupts:
        return None
    value = getattr(interrupts[0], "value", None)
    if not isinstance(value, dict):
        raise ValueError("item graph returned an invalid review interrupt")
    return value


def _review_proposal(stage: str, proposal: dict[str, Any]) -> dict[str, Any]:
    value = dict(proposal)
    if stage == ProcessingStage.MEDIA.value:
        value.setdefault("approved_media_extraction_ids", value.get("extraction_ids", []))
    if stage == ProcessingStage.IMPORTANCE.value:
        value.setdefault("importance_calculation", value.get("calculation", {}))
    return value


def _sync_graph_result(
    session_factory: SessionFactory,
    request: ItemProcessingRequest,
    result: dict[str, Any],
) -> None:
    interrupt_value = _interrupt_value(result)
    with session_factory() as db:
        run = db.get(ProcessingRun, request.workflow_run_id)
        if run is None:
            raise ValueError("processing run disappeared while graph was running")
        if interrupt_value is None:
            return
        stage = str(interrupt_value.get("stage") or "")
        proposal = interrupt_value.get("proposal")
        if stage not in {
            ProcessingStage.RELEVANCE.value,
            ProcessingStage.MEDIA.value,
            ProcessingStage.TRANSLATION.value,
            ProcessingStage.MESSAGE_ANALYSIS.value,
            ProcessingStage.IMPORTANCE.value,
            ProcessingStage.EVIDENCE_GATE.value,
        } or not isinstance(proposal, dict):
            raise ValueError("item graph review interrupt has invalid stage or proposal")
        pending = db.scalar(
            select(ReviewTask).where(
                ReviewTask.processing_run_id == run.id,
                ReviewTask.status == "pending",
            )
        )
        if pending is None:
            db.add(
                ReviewTask(
                    processing_run_id=run.id,
                    stage=stage,
                    status="pending",
                    proposal=_review_proposal(stage, proposal),
                    decision_source="manual",
                )
            )
        elif pending.stage != stage:
            raise RuntimeError("processing run already has a different pending review")
        run.status = "awaiting_review"
        run.current_stage = stage
        db.commit()


def _mark_failed(
    session_factory: SessionFactory,
    request: ItemProcessingRequest,
    exc: Exception,
) -> None:
    with session_factory() as db:
        run = db.get(ProcessingRun, request.workflow_run_id)
        if run is None or run.status in {"completed", "rejected", "superseded"}:
            return
        now = datetime.now(UTC)
        run.status = "failed"
        run.outcome = "system_error"
        run.error_message = str(exc)[:4000]
        run.completed_at = now
        if run.correction_id:
            correction = db.get(PipelineCorrection, run.correction_id)
            if correction is not None:
                correction.status = "failed"
                correction.error_message = run.error_message
                correction.completed_at = now
        db.commit()


async def invoke_item_processing(
    request: ItemProcessingRequest,
    *,
    command: Command | None = None,
    resume_existing: bool = False,
    session_factory: SessionFactory = SessionLocal,
    llm_factory: LLMFactory = LLMClient,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    checkpointer_context_factory: CheckpointerContextFactory = open_postgres_checkpointer,
    execution_guard: PipelineExecutionGuard | None = None,
) -> dict[str, Any]:
    async def invoke(active_checkpointer: BaseCheckpointSaver[Any]) -> dict[str, Any]:
        runtime = LeagueNewsWorkflowRuntime(
            session_factory,
            llm_factory=llm_factory,
            checkpointer=active_checkpointer,
            execution_guard=execution_guard,
        )
        return await runtime.invoke_item(
            request,
            command=command,
            resume_existing=resume_existing,
        )

    try:
        if checkpointer is not None:
            result = await invoke(checkpointer)
        else:
            async with checkpointer_context_factory() as active_checkpointer:
                result = await invoke(active_checkpointer)
        _sync_graph_result(session_factory, request, result)
        return result
    except Exception as exc:
        _mark_failed(session_factory, request, exc)
        raise


async def start_item_processing(
    db: Session,
    raw_item: RawItem,
    *,
    execution_mode: str = ReviewMode.MANUAL.value,
    supersedes_run_id: int | None = None,
    correction_id: int | None = None,
    restart_from_stage: str = ProcessingStage.EVIDENCE.value,
    replay_from_run_id: int | None = None,
    allow_existing_projection: bool = False,
    context: dict[str, Any] | None = None,
    defer_execution: bool = False,
    execution_guard: PipelineExecutionGuard | None = None,
    session_factory: SessionFactory = SessionLocal,
    llm_factory: LLMFactory = LLMClient,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> ProcessingRun:
    del context  # Accepted while V2 callers migrate; V3 restores typed checkpoints.
    stage = _stage(restart_from_stage)
    if stage != ProcessingStage.EVIDENCE and replay_from_run_id is None:
        stage = ProcessingStage.EVIDENCE
    request = create_item_processing_run(
        db,
        raw_item_id=raw_item.id,
        review_mode=ReviewMode(execution_mode),
        supersedes_run_id=supersedes_run_id,
        correction_id=correction_id,
        restart_from_stage=stage,
        replay_from_run_id=replay_from_run_id,
        allow_existing_projection=allow_existing_projection,
    )
    if defer_execution:
        run = db.get(ProcessingRun, request.workflow_run_id)
        if run is None:
            raise RuntimeError("processing run disappeared before queueing")
        run.context = {**run.context, "graph_started": False}
        db.commit()
        return run
    await invoke_item_processing(
        request,
        session_factory=session_factory,
        llm_factory=llm_factory,
        checkpointer=checkpointer,
        execution_guard=execution_guard,
    )
    db.expire_all()
    run = db.get(ProcessingRun, request.workflow_run_id)
    if run is None:
        raise RuntimeError("processing run disappeared after graph invocation")
    return run


async def resume_item_processing(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
    session_factory: SessionFactory = SessionLocal,
    llm_factory: LLMFactory = LLMClient,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> ProcessingRun:
    request = request_for_processing_run(db, run)
    graph_started = run.context.get("graph_started", True) is not False
    if not graph_started:
        run.context = {**run.context, "graph_started": True}
        db.commit()
    await invoke_item_processing(
        request,
        resume_existing=graph_started,
        session_factory=session_factory,
        llm_factory=llm_factory,
        checkpointer=checkpointer,
        execution_guard=execution_guard,
    )
    db.expire_all()
    return db.get(ProcessingRun, run.id)  # type: ignore[return-value]


async def approve_review(
    db: Session,
    review: ReviewTask,
    *,
    note: str | None,
    execution_guard: PipelineExecutionGuard | None = None,
    session_factory: SessionFactory = SessionLocal,
    llm_factory: LLMFactory = LLMClient,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> ProcessingRun:
    if review.status != "pending":
        raise ValueError(f"review task cannot be resolved from status={review.status}")
    run = review.processing_run
    request = request_for_processing_run(db, run)
    review.status = "approved"
    review.feedback = {"note": note} if note else {}
    review.resolved_at = datetime.now(UTC)
    run.status = "running"
    run.error_message = None
    run.completed_at = None
    db.commit()
    await invoke_item_processing(
        request,
        command=Command(
            resume=ReviewDecision(
                action="approve",
                note=note,
                replacement=dict(review.proposal),
            ).model_dump(mode="json")
        ),
        session_factory=session_factory,
        llm_factory=llm_factory,
        checkpointer=checkpointer,
        execution_guard=execution_guard,
    )
    db.expire_all()
    return db.get(ProcessingRun, run.id)  # type: ignore[return-value]


async def resume_rejected_review(
    db: Session,
    review: ReviewTask,
    *,
    session_factory: SessionFactory = SessionLocal,
    llm_factory: LLMFactory = LLMClient,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> ProcessingRun:
    if review.status != "rejected":
        raise ValueError("review task has not been rejected")
    run = review.processing_run
    request = request_for_processing_run(db, run)
    note = str(review.feedback.get("reason") or "review rejected")
    await invoke_item_processing(
        request,
        command=Command(
            resume=ReviewDecision(action="reject", note=note).model_dump(mode="json")
        ),
        session_factory=session_factory,
        llm_factory=llm_factory,
        checkpointer=checkpointer,
    )
    db.expire_all()
    return db.get(ProcessingRun, run.id)  # type: ignore[return-value]


async def retry_processing_run(
    db: Session,
    run: ProcessingRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
    session_factory: SessionFactory = SessionLocal,
    llm_factory: LLMFactory = LLMClient,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
) -> ProcessingRun:
    if run.status != "failed":
        raise ValueError(f"processing run cannot retry from status={run.status}")
    request = request_for_processing_run(db, run)
    run.status = "running"
    run.outcome = None
    run.error_message = None
    run.completed_at = None
    db.commit()
    await invoke_item_processing(
        request,
        resume_existing=True,
        session_factory=session_factory,
        llm_factory=llm_factory,
        checkpointer=checkpointer,
        execution_guard=execution_guard,
    )
    db.expire_all()
    return db.get(ProcessingRun, run.id)  # type: ignore[return-value]
