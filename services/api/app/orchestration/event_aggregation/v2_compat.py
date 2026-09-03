import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.event_admission import minimal_event_filter
from app.domain.event_types import AGGREGATION_POLICY_VERSION
from app.models.event import EventAggregationRun
from app.models.normalized_item import NormalizedItem
from app.orchestration.contracts import ReviewMode, RunMode
from app.orchestration.event_aggregation.graph import (
    EVENT_AGGREGATION_GRAPH_VERSION,
    EventAdmissionProposal,
    EventAggregationRequest,
    EventAggregationStage,
    EventCandidateProposal,
    EventDecisionProposal,
    EventMembershipResult,
    EventMessageSnapshot,
    EventProjectionResult,
)
from app.repositories.events import event_ids_for_normalized_item
from app.services.event_candidates import recall_event_candidates
from app.services.event_metrics import refresh_event_metrics
from app.services.llm import LLMClient, execution_metadata
from app.services.pipeline_execution import (
    PipelineExecutionGuard,
    assert_execution_owned,
)
from app.services.raw_item_versions import is_latest_raw_item
from app.workflows.event_aggregation import (
    _message_payload,
    _run_key,
    _suppress_out_of_space_mentions,
    apply_membership_transaction,
)


SessionFactory = Callable[[], Session]
LLMFactory = Callable[[], LLMClient]


def _load_item(
    db: Session, normalized_item_id: int, normalized_item_revision: int
) -> NormalizedItem:
    item = db.get(NormalizedItem, normalized_item_id)
    if item is None:
        raise ValueError(f"normalized item {normalized_item_id} not found")
    if item.current_revision != normalized_item_revision:
        raise ValueError("normalized item revision does not match graph request")
    if not is_latest_raw_item(db, item.raw_item):
        raise ValueError("event aggregation requires the latest RawItem revision")
    return item


class V2CompatibilityEventBackend:
    """Expose the proven V2 event algorithms through small V3 graph ports."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        llm_factory: LLMFactory = LLMClient,
        execution_guard: PipelineExecutionGuard | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._llm_factory = llm_factory
        self._execution_guard = execution_guard

    def _assert_execution_owned(self, db: Session) -> None:
        assert_execution_owned(db, self._execution_guard)

    async def load_message(
        self, request: EventAggregationRequest
    ) -> EventMessageSnapshot:
        with self._session_factory() as db:
            item = _load_item(
                db, request.normalized_item_id, request.normalized_item_revision
            )
            message, truncation = _message_payload(item)
            fingerprint = hashlib.sha256(
                json.dumps(
                    message,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            return EventMessageSnapshot(
                normalized_item_id=item.id,
                normalized_item_revision=item.current_revision,
                message=message,
                input_truncation=truncation,
                evidence_fingerprint=fingerprint,
            )

    async def minimal_filter(
        self, request: EventAggregationRequest
    ) -> EventAdmissionProposal:
        with self._session_factory() as db:
            item = _load_item(
                db, request.normalized_item_id, request.normalized_item_revision
            )
            admission = minimal_event_filter(item)
            return EventAdmissionProposal(
                decision=admission.decision,
                reasons=list(admission.reasons),
                products=list(admission.event_space.products),
                possible_event_families=list(admission.event_space.possible_families),
                entity_hints=admission.entity_hints,
            )

    async def retrieve_candidates(
        self,
        request: EventAggregationRequest,
        admission: EventAdmissionProposal,
    ) -> EventCandidateProposal:
        with self._session_factory() as db:
            item = _load_item(
                db, request.normalized_item_id, request.normalized_item_revision
            )
            candidates = recall_event_candidates(
                db,
                item=item,
                possible_families=admission.possible_event_families,
                entity_hints=admission.entity_hints,
            )
            return EventCandidateProposal(candidates=candidates)

    async def decide_semantics(
        self,
        request: EventAggregationRequest,
        snapshot: EventMessageSnapshot,
        admission: EventAdmissionProposal,
        candidates: EventCandidateProposal,
    ) -> EventDecisionProposal:
        result = await self._llm_factory().aggregate_events(
            message=snapshot.message,
            possible_event_families=admission.possible_event_families,
            candidates=candidates.candidates,
        )
        from app.domain.event_families import EventSpace

        result, suppressed = _suppress_out_of_space_mentions(
            result,
            EventSpace(
                products=tuple(admission.products),
                possible_families=tuple(admission.possible_event_families),
            ),
        )
        return EventDecisionProposal(
            result=result,
            suppressed_mentions=suppressed,
            execution_metadata=execution_metadata(result),
        )

    async def save_stage(
        self,
        request: EventAggregationRequest,
        stage: EventAggregationStage,
        output: dict[str, object],
    ) -> None:
        if request.run_mode != RunMode.PRODUCTION:
            return
        with self._session_factory() as db:
            run = db.get(EventAggregationRun, request.workflow_run_id)
            if run is None:
                raise ValueError("event aggregation run not found")
            draft = dict(run.decision_draft or {})
            checkpoints = dict(draft.get("stage_checkpoints") or {})
            checkpoints[stage.value] = output
            draft["stage_checkpoints"] = checkpoints
            if stage == EventAggregationStage.SEMANTIC_DECISION:
                result = output.get("result")
                if isinstance(result, dict):
                    draft.update(result)
                draft["suppressed_mentions"] = output.get("suppressed_mentions", [])
                draft["execution_metadata"] = output.get("execution_metadata", {})
                metadata = output.get("execution_metadata")
                if isinstance(metadata, dict):
                    run.model_call_count = int(metadata.get("retry_count") or 0) + 1
            run.decision_draft = draft
            run.current_stage = _legacy_stage(stage)
            if stage == EventAggregationStage.MINIMAL_FILTER:
                run.admission_decision = str(output.get("decision") or "process")
            elif stage == EventAggregationStage.CANDIDATE_RETRIEVAL:
                candidates = output.get("candidates")
                run.candidate_snapshot = candidates if isinstance(candidates, list) else []
            elif stage == EventAggregationStage.LOAD_MESSAGE:
                fingerprint = output.get("evidence_fingerprint")
                run.input_fingerprint = str(fingerprint) if fingerprint else None
            self._assert_execution_owned(db)
            db.commit()

    async def apply_membership(
        self,
        request: EventAggregationRequest,
        candidates: EventCandidateProposal,
        decision: EventDecisionProposal,
    ) -> EventMembershipResult:
        if request.run_mode != RunMode.PRODUCTION:
            raise RuntimeError("non-production event graph attempted membership writes")
        with self._session_factory() as db:
            item = _load_item(
                db, request.normalized_item_id, request.normalized_item_revision
            )
            historical = event_ids_for_normalized_item(db, item.id)
            count, affected = apply_membership_transaction(
                db,
                item=item,
                result=decision.result,
                candidates=candidates.candidates,
                refresh_metrics=False,
            )
            self._assert_execution_owned(db)
            db.commit()
            return EventMembershipResult(
                applied_count=count,
                affected_event_ids=sorted(affected),
                historical_event_ids=sorted(historical),
            )

    async def refresh_projection(
        self,
        request: EventAggregationRequest,
        membership: EventMembershipResult,
    ) -> EventProjectionResult:
        if request.run_mode != RunMode.PRODUCTION:
            raise RuntimeError("non-production event graph attempted projection writes")
        event_ids = set(membership.affected_event_ids) | set(
            membership.historical_event_ids
        )
        with self._session_factory() as db:
            refresh_event_metrics(db, event_ids)
            self._assert_execution_owned(db)
            db.commit()
        return EventProjectionResult(refreshed_event_ids=sorted(event_ids))

    async def complete(
        self, request: EventAggregationRequest, outcome: str
    ) -> None:
        if request.run_mode != RunMode.PRODUCTION:
            return
        with self._session_factory() as db:
            run = db.get(EventAggregationRun, request.workflow_run_id)
            if run is None:
                raise ValueError("event aggregation run not found")
            run.status = "completed"
            run.outcome = outcome
            run.completed_at = datetime.now(UTC)
            if outcome in {"applied", "ignored"}:
                run.applied_at = run.completed_at
            self._assert_execution_owned(db)
            db.commit()


def _legacy_stage(stage: EventAggregationStage) -> str:
    if stage in {
        EventAggregationStage.LOAD_MESSAGE,
        EventAggregationStage.MINIMAL_FILTER,
    }:
        return "minimal_filter"
    if stage in {
        EventAggregationStage.CANDIDATE_RETRIEVAL,
        EventAggregationStage.SEMANTIC_DECISION,
    }:
        return "model_decision"
    return "apply_membership"


def create_event_aggregation_run(
    session_factory: SessionFactory,
    *,
    normalized_item_id: int,
    normalized_item_revision: int,
    review_mode: ReviewMode = ReviewMode.AUTOMATIC,
) -> EventAggregationRequest:
    with session_factory() as db:
        item = _load_item(db, normalized_item_id, normalized_item_revision)
        existing = db.scalar(
            select(EventAggregationRun).where(
                EventAggregationRun.idempotency_key == _run_key(item)
            )
        )
        if existing is None:
            existing = EventAggregationRun(
                normalized_item_id=item.id,
                normalized_item_revision=item.current_revision,
                status="running",
                current_stage="minimal_filter",
                aggregation_policy_version=AGGREGATION_POLICY_VERSION,
                idempotency_key=_run_key(item),
                decision_draft={
                    "graph_version": EVENT_AGGREGATION_GRAPH_VERSION,
                    "review_mode": review_mode.value,
                    "stage_checkpoints": {},
                },
            )
            db.add(existing)
            db.commit()
            db.refresh(existing)
        return EventAggregationRequest(
            workflow_run_id=existing.id,
            normalized_item_id=item.id,
            normalized_item_revision=item.current_revision,
            run_mode=RunMode.PRODUCTION,
            review_mode=review_mode,
        )
