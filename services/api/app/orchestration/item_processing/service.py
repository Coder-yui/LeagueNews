from __future__ import annotations

import secrets
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime, timedelta
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.core.config import settings
from app.models.pipeline import PipelineCorrection, PipelineJob
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
from app.orchestration.item_processing.backend import create_item_processing_run
from app.methods import MethodAssembly, MethodAssemblyConfig
from app.orchestration.runtime import LeagueNewsWorkflowRuntime
from app.services.llm import LLMClient
from app.services.pipeline_execution import PipelineExecutionGuard, assert_execution_owned


SessionFactory = Callable[[], Session]
LLMFactory = Callable[[], LLMClient]
CheckpointerContextFactory = Callable[[], AbstractAsyncContextManager[BaseCheckpointSaver[Any]]]


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
    restart_from_stage = ProcessingStage(
        run.restart_from_stage or ProcessingStage.EVIDENCE.value
    )
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


def method_config_for_run(run: ProcessingRun) -> MethodAssemblyConfig:
    return MethodAssemblyConfig.model_validate(run.method_config or {})


def claim_review_delivery(
    session_factory: SessionFactory,
    review_id: int,
) -> str | None:
    """Claim one persisted review decision for LangGraph delivery.

    ``None`` means the command was already consumed.  A live claim is never
    stolen; an expired claim is recoverable by a later request.
    """

    token = secrets.token_hex(24)
    with session_factory() as db:
        review = db.scalar(
            select(ReviewTask)
            .where(ReviewTask.id == review_id)
            .with_for_update()
        )
        if review is None:
            raise ValueError("review task not found")
        if review.delivery_status == "consumed":
            return None
        if review.status not in {"approved", "rejected"}:
            raise ValueError("review decision has not been recorded")
        now = datetime.now(UTC)
        expires_at = review.delivery_claim_expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at is not None and expires_at > now:
            raise ValueError("review decision delivery is already in progress")
        review.delivery_status = "recorded"
        review.delivery_attempts += 1
        review.delivery_claim_token = token
        review.delivery_claimed_at = now
        review.delivery_claim_expires_at = now + timedelta(
            seconds=settings.review_delivery_lease_seconds
        )
        db.commit()
    return token


def _sync_pipeline_job_after_review(session_factory: SessionFactory, run_id: int) -> None:
    """Finish or pause an automatic job after an HTTP review delivery.

    A manual run has no PipelineJob.  For an automatic run the job remains the
    lifecycle owner, so a review endpoint only mirrors the graph result into
    that owner after the decision has been consumed.
    """
    with session_factory() as owned_db:
        run = owned_db.get(ProcessingRun, run_id)
        job = owned_db.scalar(
            select(PipelineJob).where(PipelineJob.processing_run_id == run_id)
        )
        if run is None or job is None or job.status not in {"paused", "running"}:
            return
        job.status = "completed" if run.status in {"completed", "rejected"} else "paused"
        if job.status == "completed":
            job.completed_at = datetime.now(UTC)
            job.next_attempt_at = None
        job.lease_token = None
        job.lease_expires_at = None
        job.worker_id = None
        job.heartbeat_at = None
        owned_db.commit()


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
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    interrupt_value = _interrupt_value(result)
    with session_factory() as db:
        run = db.scalar(
            select(ProcessingRun)
            .where(ProcessingRun.id == request.workflow_run_id)
            .with_for_update()
        )
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
        if execution_guard is not None:
            execution_guard.assert_owned(db)
        db.commit()


def _mark_failed(
    session_factory: SessionFactory,
    request: ItemProcessingRequest,
    exc: Exception,
    execution_guard: PipelineExecutionGuard | None = None,
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
        try:
            if execution_guard is not None:
                execution_guard.assert_owned(db)
            db.commit()
        except Exception:
            db.rollback()


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
    method_config: MethodAssemblyConfig | None = None,
) -> dict[str, Any]:
    async def invoke(active_checkpointer: BaseCheckpointSaver[Any]) -> dict[str, Any]:
        runtime = LeagueNewsWorkflowRuntime(
            session_factory,
            llm_factory=llm_factory,
            checkpointer=active_checkpointer,
            execution_guard=execution_guard,
            method_config=method_config,
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
        _sync_graph_result(session_factory, request, result, execution_guard)
        return result
    except Exception as exc:
        _mark_failed(session_factory, request, exc, execution_guard)
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
    defer_execution: bool = False,
    execution_guard: PipelineExecutionGuard | None = None,
    session_factory: SessionFactory = SessionLocal,
    llm_factory: LLMFactory = LLMClient,
    checkpointer: BaseCheckpointSaver[Any] | None = None,
    method_config: MethodAssemblyConfig | None = None,
) -> ProcessingRun:
    # Serialize manual/automatic starts before creating or invoking a graph.
    # This row lock is released before any model or checkpoint network call.
    db.scalar(select(RawItem.id).where(RawItem.id == raw_item.id).with_for_update())
    active = db.scalar(select(ProcessingRun).where(
        ProcessingRun.raw_item_id == raw_item.id,
        ProcessingRun.workflow_type == "item",
        ProcessingRun.graph_name == ITEM_PROCESSING_GRAPH,
        ProcessingRun.status.in_(["running", "awaiting_review"]),
    ).order_by(ProcessingRun.id.desc()).limit(1))
    if active is not None:
        db.commit()
        return active
    stage = ProcessingStage(restart_from_stage)
    if stage != ProcessingStage.EVIDENCE and replay_from_run_id is None:
        stage = ProcessingStage.EVIDENCE
    resolved_method_config = method_config or MethodAssembly().config
    request = create_item_processing_run(
        db,
        raw_item_id=raw_item.id,
        review_mode=ReviewMode(execution_mode),
        supersedes_run_id=supersedes_run_id,
        correction_id=correction_id,
        restart_from_stage=stage,
        replay_from_run_id=replay_from_run_id,
        allow_existing_projection=allow_existing_projection,
        method_config=resolved_method_config,
        execution_guard=execution_guard,
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
        method_config=method_config,
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
    method_config: MethodAssemblyConfig | None = None,
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
        method_config=method_config or method_config_for_run(run),
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
    replacement: dict[str, Any] | None = None,
    decision_source: str = "manual",
    policy_version: str | None = None,
) -> ProcessingRun:
    review_id = review.id
    with session_factory() as owned_db:
        locked_review = owned_db.scalar(
            select(ReviewTask)
            .where(ReviewTask.id == review_id)
            .with_for_update()
        )
        if locked_review is None:
            raise ValueError("review task not found")
        now = datetime.now(UTC)
        if locked_review.status == "pending":
            if replacement is not None:
                locked_review.proposal = dict(replacement)
            locked_review.status = "approved"
            locked_review.feedback = {"note": note} if note else {}
            locked_review.resolved_at = now
            locked_review.delivery_status = "recorded"
            locked_review.decision_source = decision_source
            locked_review.policy_version = policy_version
        elif locked_review.status != "approved":
            raise ValueError(f"review task cannot be resolved from status={locked_review.status}")
        if locked_review.delivery_status == "consumed":
            existing_run = db.get(ProcessingRun, locked_review.processing_run_id)
            if existing_run is None:
                raise ValueError("processing run not found")
            return existing_run
        owned_db.commit()
    delivery_token = claim_review_delivery(session_factory, review_id)
    if delivery_token is None:
        existing_run = db.get(ProcessingRun, review.processing_run_id)
        if existing_run is None:
            raise ValueError("processing run not found")
        return existing_run
    # The decision was committed by the short delivery transaction above.
    # Refresh the caller's identity map before building the LangGraph command;
    # this also makes correct-and-approve use the committed replacement rather
    # than a stale pending ReviewTask object.
    db.expire_all()
    review = db.get(ReviewTask, review_id)
    run = db.get(ProcessingRun, review.processing_run_id) if review is not None else None
    if review is None or run is None:
        raise ValueError("review task or processing run not found")
    request = request_for_processing_run(db, run)
    run.status = "running"
    run.error_message = None
    run.completed_at = None
    assert_execution_owned(db, execution_guard)
    db.commit()
    try:
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
            method_config=method_config_for_run(run),
        )
    except Exception:
        with session_factory() as owned_db:
            failed_delivery = owned_db.scalar(
                select(ReviewTask)
                .where(
                    ReviewTask.id == review_id,
                    ReviewTask.delivery_claim_token == delivery_token,
                )
                .with_for_update()
            )
            if failed_delivery is not None:
                failed_delivery.delivery_claim_token = None
                failed_delivery.delivery_claimed_at = None
                failed_delivery.delivery_claim_expires_at = None
                owned_db.commit()
        raise
    with session_factory() as owned_db:
        delivered = owned_db.scalar(
            select(ReviewTask)
            .where(
                ReviewTask.id == review_id,
                ReviewTask.delivery_claim_token == delivery_token,
            )
            .with_for_update()
        )
        if delivered is not None:
            delivered.delivery_status = "consumed"
            delivered.consumed_at = datetime.now(UTC)
            delivered.delivery_claim_token = None
            delivered.delivery_claimed_at = None
            delivered.delivery_claim_expires_at = None
            owned_db.commit()
    _sync_pipeline_job_after_review(session_factory, run.id)
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
    review_id = review.id
    delivery_token = claim_review_delivery(session_factory, review_id)
    db.expire_all()
    review = db.get(ReviewTask, review_id)
    if review is None:
        raise ValueError("review task not found")
    run = db.get(ProcessingRun, review.processing_run_id)
    if run is None:
        raise ValueError("processing run not found")
    if delivery_token is None:
        return run
    request = request_for_processing_run(db, run)
    note = str(review.feedback.get("reason") or "review rejected")
    try:
        await invoke_item_processing(
            request,
            command=Command(
                resume=ReviewDecision(action="reject", note=note).model_dump(mode="json")
            ),
            session_factory=session_factory,
            llm_factory=llm_factory,
            checkpointer=checkpointer,
            method_config=method_config_for_run(run),
        )
    except Exception:
        with session_factory() as owned_db:
            failed_delivery = owned_db.scalar(
                select(ReviewTask)
                .where(
                    ReviewTask.id == review_id,
                    ReviewTask.delivery_claim_token == delivery_token,
                )
                .with_for_update()
            )
            if failed_delivery is not None:
                failed_delivery.delivery_claim_token = None
                failed_delivery.delivery_claimed_at = None
                failed_delivery.delivery_claim_expires_at = None
                owned_db.commit()
        raise
    with session_factory() as owned_db:
        delivered = owned_db.scalar(
            select(ReviewTask)
            .where(
                ReviewTask.id == review_id,
                ReviewTask.delivery_claim_token == delivery_token,
            )
            .with_for_update()
        )
        if delivered is not None:
            delivered.delivery_status = "consumed"
            delivered.consumed_at = datetime.now(UTC)
            delivered.delivery_claim_token = None
            delivered.delivery_claimed_at = None
            delivered.delivery_claim_expires_at = None
            owned_db.commit()
    _sync_pipeline_job_after_review(session_factory, run.id)
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
    raw_item = db.get(RawItem, run.raw_item_id)
    if raw_item is None:
        raise ValueError("processing run raw item no longer exists")
    pending_job = db.scalar(select(PipelineJob).where(
        PipelineJob.workflow_name == ITEM_PROCESSING_GRAPH,
        PipelineJob.target_entity_type == "raw_item",
        PipelineJob.target_entity_id == run.raw_item_id,
        PipelineJob.target_revision == raw_item.revision,
        (PipelineJob.status.in_(["queued", "running", "paused"])) |
        ((PipelineJob.status == "failed") & PipelineJob.next_attempt_at.is_not(None)),
    ))
    if pending_job is not None and execution_guard is None:
        raise ValueError("active pipeline job owns the pending retry")
    if run.status != "failed":
        raise ValueError(f"processing run cannot retry from status={run.status}")
    if run.graph_name != ITEM_PROCESSING_GRAPH:
        raise ValueError("only V3 item processing runs can be retried")
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
        method_config=method_config_for_run(run),
    )
    db.expire_all()
    return db.get(ProcessingRun, run.id)  # type: ignore[return-value]
