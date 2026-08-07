from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.content_blocks import has_repost_evidence
from app.models.event import (
    Event,
    EventAggregationRun,
    EventMessage,
    EventReviewTask,
)
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, ProcessingCheckpoint
from app.models.workflow import KnowledgeRule
from app.schemas.event_workflow import (
    EventDecisionDraft,
    EventReviewRejection,
)
from app.services.event_aggregation import (
    EventMembershipConflictError,
    add_message_to_event,
    create_event,
)
from app.services.event_candidates import (
    aggregation_routes,
    find_event_candidates,
    resolve_aggregation_routes,
)
from app.services.event_decision import (
    stabilize_event_decision,
    validate_event_decision_business,
)
from app.services.llm import LLMClient, execution_metadata
from app.services.pipeline_execution import (
    PipelineExecutionGuard,
    assert_execution_owned,
)
from app.services.raw_item_versions import (
    is_latest_normalized_item,
    superseded_normalized_item_ids,
)

EVENT_STAGE = "event_decision"


async def start_event_aggregation(
    db: Session,
    item: NormalizedItem,
    *,
    supersedes_run_id: int | None = None,
    execution_mode: str = "manual",
    correction_id: int | None = None,
    execution_guard: PipelineExecutionGuard | None = None,
) -> EventAggregationRun:
    if not is_latest_normalized_item(db, item):
        raise ValueError("normalized item has been superseded by a newer raw revision")
    if item.publication_status != "published":
        raise ValueError("withdrawn normalized item cannot enter event aggregation")
    if db.scalar(
        select(EventMessage).where(
            EventMessage.normalized_item_id == item.id,
            EventMessage.membership_status == "active",
        )
    ):
        raise ValueError("normalized item already has active event memberships")
    active = db.scalar(
        select(EventAggregationRun).where(
            EventAggregationRun.normalized_item_id == item.id,
            EventAggregationRun.status.in_(["running", "awaiting_review"]),
        )
    )
    if active:
        raise ValueError(f"normalized item already has active event run {active.id}")
    run = EventAggregationRun(
        normalized_item_id=item.id,
        supersedes_run_id=supersedes_run_id,
        status="running",
        current_stage=EVENT_STAGE,
        execution_mode=execution_mode,
        correction_id=correction_id,
        restart_from_stage=EVENT_STAGE if correction_id else None,
    )
    db.add(run)
    try:
        assert_execution_owned(db, execution_guard)
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = db.scalar(
            select(EventAggregationRun).where(
                EventAggregationRun.normalized_item_id == item.id,
                EventAggregationRun.status.in_(["running", "awaiting_review"]),
            )
        )
        if concurrent is None:
            raise
        return concurrent
    db.refresh(run)
    try:
        await _generate_review(db, run, execution_guard=execution_guard)
    except Exception as exc:
        db.rollback()
        run = db.get(EventAggregationRun, run.id)
        run.status = "failed"
        run.outcome = "system_error"
        run.error_message = str(exc)
        run.completed_at = datetime.now(UTC)
        assert_execution_owned(db, execution_guard)
        db.commit()
        raise
    return run


async def retry_event_aggregation(
    db: Session,
    run: EventAggregationRun,
) -> EventAggregationRun:
    if run.status not in {"failed", "rejected"}:
        raise ValueError(f"event aggregation run cannot retry from status={run.status}")
    item = db.get(NormalizedItem, run.normalized_item_id)
    if item is None:
        raise ValueError("normalized item no longer exists")
    return await start_event_aggregation(db, item, supersedes_run_id=run.id)


async def resume_event_aggregation(
    db: Session,
    run: EventAggregationRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> EventAggregationRun:
    if run.status != "running":
        return run
    pending = db.scalar(
        select(EventReviewTask).where(
            EventReviewTask.event_aggregation_run_id == run.id,
            EventReviewTask.status == "pending",
        )
    )
    if pending is not None:
        run.status = "awaiting_review"
        assert_execution_owned(db, execution_guard)
        db.commit()
        return run
    try:
        await _generate_review(db, run, execution_guard=execution_guard)
    except IntegrityError:
        db.rollback()
        pending = db.scalar(
            select(EventReviewTask).where(
                EventReviewTask.event_aggregation_run_id == run.id,
                EventReviewTask.status == "pending",
            )
        )
        if pending is None:
            raise
        run = db.get(EventAggregationRun, run.id)
        run.status = "awaiting_review"
        assert_execution_owned(db, execution_guard)
        db.commit()
    return run


def _correction_event_ids(
    correction: PipelineCorrection | None,
) -> set[int] | None:
    if correction is None:
        return None
    return set(correction.original_event_ids) or None


async def _generate_review(
    db: Session,
    run: EventAggregationRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    item = run.normalized_item
    correction = (
        db.get(PipelineCorrection, run.correction_id)
        if run.correction_id
        else None
    )
    candidates = find_event_candidates(
        db,
        normalized_item_id=item.id,
        include_event_ids=_correction_event_ids(correction),
    )
    candidate_payloads = [asdict(candidate) for candidate in candidates]
    routes = resolve_aggregation_routes(
        aggregation_routes(item),
        candidate_payloads,
    )
    selected_rules = list(
        db.scalars(
            select(KnowledgeRule)
            .where(
                KnowledgeRule.knowledge_type == "event_aggregation",
                KnowledgeRule.lifecycle_status == "active",
                KnowledgeRule.scope.in_(
                    [
                        "global",
                        f"topic:{item.primary_topic}",
                        f"subtopic:{item.subtopic}",
                    ]
                ),
            )
            .order_by(KnowledgeRule.updated_at.desc())
        )
    )
    rules = [rule.rule_text for rule in selected_rules]
    item_payload = {
        "normalized_item_id": item.id,
        "title": item.translated_title or item.normalized_title,
        "summary": item.summary,
        "original_title": item.raw_item.display_title,
        "original_content_blocks": item.raw_item.content_blocks,
        "translated_content_blocks": item.translated_content_blocks,
        "entities": item.entities,
        "source_kind": item.source_kind,
        "information_stage": item.information_stage,
        "content_form": item.content_form,
        "topic": item.primary_topic,
        "subtopic": item.subtopic,
        "product_scope": item.product_scope,
        "temporal": item.facets.get("temporal", {}),
        "event_assertion": item.facets.get("event_assertion", "asserted"),
        "event_mentions": item.facets.get("event_mentions", []),
        "fact_claims": [
            {
                "claim_id": claim.id,
                "subject": claim.subject,
                "predicate": claim.predicate,
                "object": claim.object_value,
                "temporal_role": claim.temporal_role,
                "attribution": claim.attribution,
            }
            for claim in item.claims
            if claim.status == "active"
        ],
        "intrinsic_importance_score": item.importance_score,
        "message_priority_score": item.priority_score,
        "processing_metadata": {
            "analysis_model": item.analysis_model,
            "analysis_version": item.analysis_version,
            "ontology_version": item.ontology_version,
        },
        "raw_revision": item.raw_item.revision,
        "supersedes_raw_item_id": item.raw_item.supersedes_raw_item_id,
        "superseded_normalized_item_ids": superseded_normalized_item_ids(
            db, item
        ),
        "event_routes": [asdict(route) for route in routes],
        "source": {
            "source_id": item.raw_item.source_id,
            "source_name": item.raw_item.source.name,
            "connector_type": item.raw_item.source.connector_type,
            "authority": item.raw_item.source.connector_config.get(
                "authority_level"
            ),
            "is_official": item.raw_item.source.is_official,
            "reliability_score": item.raw_item.source.reliability_score,
            "is_repost": has_repost_evidence(item.raw_item.content_blocks),
        },
        "published_at": (
            item.raw_item.published_at.isoformat()
            if item.raw_item.published_at
            else None
        ),
    }
    assert_execution_owned(db, execution_guard)
    db.commit()
    decision = (
        await LLMClient().propose_event(
            item=item_payload,
            candidates=candidate_payloads,
            knowledge_rules=rules,
        )
        if routes
        else EventDecisionDraft()
    )
    run.candidate_snapshot = candidate_payloads
    run.decision_draft = {
        **decision.model_dump(mode="json"),
        "_execution_metadata": execution_metadata(decision),
        "knowledge_rule_ids": [
            {"id": rule.id, "version": rule.version}
            for rule in selected_rules
        ],
    }
    run.status = "awaiting_review"
    db.add(
        EventReviewTask(
            event_aggregation_run_id=run.id,
            proposal={
                "item": {
                    "normalized_item_id": item.id,
                    "title": item.translated_title or item.normalized_title,
                    "summary": item.summary,
                    "topic": item.primary_topic,
                    "subtopic": item.subtopic,
                    "product_scope": item.product_scope,
                },
                "candidates": candidate_payloads,
                "decision": run.decision_draft,
            },
        )
    )
    assert_execution_owned(db, execution_guard)
    db.commit()
    db.refresh(run)


def approve_event_review(
    db: Session,
    review: EventReviewTask,
    *,
    note: str | None,
    execution_guard: PipelineExecutionGuard | None = None,
) -> EventAggregationRun:
    _require_pending(review)
    run = review.run
    if not is_latest_normalized_item(db, run.normalized_item):
        raise ValueError("normalized item was superseded before event review approval")
    if run.normalized_item.publication_status != "published":
        raise ValueError("normalized item was withdrawn before event review approval")
    decision = validate_event_decision(db, run, run.decision_draft)
    candidate_ids = {
        int(candidate["event_id"]) for candidate in run.candidate_snapshot
    }
    now = datetime.now(UTC)

    item = run.normalized_item
    created_count = 0
    updated_count = 0
    affected_event_ids = []
    for membership in decision.memberships:
        created_here = False
        event = db.scalar(
            select(Event)
            .where(Event.aggregation_key == membership.aggregation_key)
            .with_for_update()
        )
        existing_event_id = (
            event.id if event is not None else membership.existing_event_id
        )
        if event is None and existing_event_id is None:
            try:
                with db.begin_nested():
                    event = create_event(
                        db,
                        normalized_item_id=run.normalized_item_id,
                        aggregation_key=membership.aggregation_key,
                        title=membership.timeline_note
                        or item.translated_title
                        or item.normalized_title,
                        summary=item.summary,
                        event_kind=membership.event_kind,
                        aggregation_strategy=membership.aggregation_strategy,
                        product_scope=membership.product_scope,
                        lifecycle_status=membership.lifecycle_status or "developing",
                        membership_role=membership.membership_role,
                        evidence_stance=membership.evidence_stance,
                        independence_key=None,
                        timeline_note=membership.timeline_note,
                        update_kind=membership.update_kind,
                        latest_development=membership.timeline_note,
                        change_note=f"创建时间线节点：{membership.timeline_note}",
                        evidence={
                            "event_run_id": run.id,
                            "timeline_note": membership.timeline_note,
                        },
                        commit=False,
                    )
                    created_here = True
            except EventMembershipConflictError:
                event = db.scalar(
                    select(Event)
                    .where(Event.aggregation_key == membership.aggregation_key)
                    .with_for_update()
                )
                if event is None:
                    raise
            if created_here:
                affected_event_ids.append(event.id)
                created_count += 1
                continue
            existing_event_id = event.id
        if event is None and existing_event_id is not None:
            if existing_event_id not in candidate_ids:
                raise ValueError(
                    "decision references an event outside the candidate snapshot"
                )
            event = db.get(Event, existing_event_id)
        if event is None:
            raise ValueError("target event no longer exists")
        event, added = add_message_to_event(
            db,
            event_id=event.id,
            normalized_item_id=run.normalized_item_id,
            lifecycle_status=membership.lifecycle_status,
            membership_role=membership.membership_role,
            evidence_stance=membership.evidence_stance,
            independence_key=None,
            timeline_note=membership.timeline_note,
            update_kind=membership.update_kind,
            is_significant_update=membership.update_kind not in {
                "context",
                "duplicate_evidence",
            },
            latest_development=membership.timeline_note,
            change_note=f"时间线节点：{membership.timeline_note}",
            evidence={
                "event_run_id": run.id,
                "timeline_note": membership.timeline_note,
            },
            commit=False,
        )
        if not added:
            affected_event_ids.append(event.id)
            continue
        affected_event_ids.append(event.id)
        updated_count += 1
    if not decision.memberships:
        run.outcome = "not_event"
    elif created_count and updated_count:
        run.outcome = "multi_membership"
    elif created_count:
        run.outcome = "created"
    else:
        run.outcome = "updated"

    review.status = "approved"
    db.add(
        ProcessingCheckpoint(
            raw_item_id=run.normalized_item.raw_item_id,
            normalized_item_id=run.normalized_item_id,
            event_aggregation_run_id=run.id,
            correction_id=run.correction_id,
            stage=EVENT_STAGE,
            output_snapshot=dict(run.decision_draft),
            artifact_references={
                "candidate_event_ids": sorted(candidate_ids),
                "affected_event_ids": sorted(affected_event_ids),
                "outcome": run.outcome,
                "workflow_version": "event-aggregation-v2",
                "policy_version": review.policy_version,
                "execution_metadata": run.decision_draft.get(
                    "_execution_metadata", {}
                ),
            },
            knowledge_snapshot={
                "candidate_event_ids": sorted(candidate_ids),
                "knowledge_rule_ids": run.decision_draft.get(
                    "knowledge_rule_ids", []
                ),
            },
            model_name=None,
            decision_source=review.decision_source,
        )
    )
    review.feedback = {"note": note} if note else {}
    review.resolved_at = now
    run.status = "completed"
    run.completed_at = now
    if run.correction_id:
        correction = db.get(PipelineCorrection, run.correction_id)
        if correction is not None:
            correction.status = "completed"
            correction.completed_at = now
    assert_execution_owned(db, execution_guard)
    db.commit()
    db.refresh(run)
    return run


def validate_event_decision(
    db: Session,
    run: EventAggregationRun,
    value: dict[str, object],
) -> EventDecisionDraft:
    """Apply the same deterministic candidate and routing rules to every decision source."""
    decision = EventDecisionDraft.model_validate(value)
    routes = resolve_aggregation_routes(
        aggregation_routes(run.normalized_item),
        run.candidate_snapshot,
    )
    routes_by_key = {
        route.aggregation_key: route
        for route in routes
        if route.aggregation_key is not None
    }
    creatable_keys = {
        key
        for key, route in routes_by_key.items()
        if route.creation_policy != "existing_only"
    }
    item = run.normalized_item
    item_payload = {
        "title": item.translated_title or item.normalized_title,
        "summary": item.summary,
        "information_stage": item.information_stage,
        "content_form": item.content_form,
        "product_scope": item.product_scope,
        "event_assertion": item.facets.get("event_assertion", "asserted"),
        "event_mentions": item.facets.get("event_mentions", []),
        "event_routes": [asdict(route) for route in routes],
    }
    decision = stabilize_event_decision(
        decision,
        item=item_payload,
        candidates=list(run.candidate_snapshot),
    )
    for membership in decision.memberships:
        route = routes_by_key.get(membership.aggregation_key)
        if route is None:
            continue
        membership.event_kind = route.event_kind
        membership.aggregation_strategy = route.aggregation_strategy
        membership.product_scope = route.product_scope
    error = validate_event_decision_business(
        decision,
        item=item_payload,
        candidates=list(run.candidate_snapshot),
        allowed_new_keys=creatable_keys,
    )
    if error:
        raise ValueError(error)
    return decision


def reject_event_review(
    db: Session,
    review: EventReviewTask,
    *,
    payload: EventReviewRejection,
) -> EventAggregationRun:
    _require_pending(review)
    now = datetime.now(UTC)
    review.status = "rejected"
    review.feedback = payload.model_dump(mode="json")
    review.resolved_at = now
    run = review.run
    run.status = "rejected"
    run.outcome = "review_rejected"
    run.completed_at = now
    if payload.knowledge_rule:
        db.add(
            KnowledgeRule(
                knowledge_type="event_aggregation",
                scope=payload.knowledge_scope,
                rule_text=payload.knowledge_rule,
                correction_data={
                    "reason": payload.reason,
                    "decision_draft": run.decision_draft,
                },
                source_event_review_id=review.id,
                lifecycle_status="draft",
            )
        )
    db.commit()
    db.refresh(run)
    return run


def _require_pending(review: EventReviewTask) -> None:
    if review.status != "pending":
        raise ValueError(f"event review is already {review.status}")
    if review.run.status != "awaiting_review":
        raise ValueError(f"event run is not awaiting review: {review.run.status}")
