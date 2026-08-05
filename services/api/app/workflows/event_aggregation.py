from dataclasses import asdict
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.content_blocks import has_quoted_post
from app.models.event import (
    EventAggregationRun,
    EventMessage,
    EventReviewTask,
)
from app.models.normalized_item import NormalizedItem
from app.models.pipeline import PipelineCorrection, ProcessingCheckpoint
from app.models.workflow import KnowledgeRule
from app.schemas.event_workflow import (
    EventDecisionDraft,
    EventMembershipDraft,
    EventReviewRejection,
)
from app.services.event_aggregation import (
    add_message_to_event,
    create_event,
    expire_stale_unconfirmed_events,
)
from app.services.event_candidates import (
    aggregation_routes,
    event_aggregation_policy,
    find_event_candidates,
    stable_event_key,
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
        raise ValueError("normalized item already belongs to an event")
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


async def _generate_review(
    db: Session,
    run: EventAggregationRun,
    *,
    execution_guard: PipelineExecutionGuard | None = None,
) -> None:
    item = run.normalized_item
    expire_stale_unconfirmed_events(db, commit=False)
    correction = (
        db.get(PipelineCorrection, run.correction_id)
        if run.correction_id
        else None
    )
    candidates = find_event_candidates(
        db,
        normalized_item_id=item.id,
        include_event_ids={correction.event_id}
        if correction is not None and correction.event_id is not None
        else None,
    )
    candidate_payloads = [asdict(candidate) for candidate in candidates]
    selected_rules = list(
        db.scalars(
            select(KnowledgeRule)
            .where(
                KnowledgeRule.knowledge_type == "event_aggregation",
                KnowledgeRule.is_active.is_(True),
                KnowledgeRule.scope.in_(["global", item.category]),
            )
            .order_by(KnowledgeRule.updated_at.desc())
        )
    )
    rules = [rule.rule_text for rule in selected_rules]
    item_payload = {
        "normalized_item_id": item.id,
        "title": item.translated_title or item.normalized_title,
        "summary": item.summary,
        "category": item.category,
        "entities": item.entities,
        "content_type": item.content_type,
        "topic": item.primary_topic,
        "entity_roles": item.facets.get("entity_roles", []),
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
        "importance_score": item.importance_score,
        "credibility": item.credibility,
        "credibility_score": item.credibility_score,
        "credibility_evidence": item.credibility_evidence,
        "raw_revision": item.raw_item.revision,
        "supersedes_raw_item_id": item.raw_item.supersedes_raw_item_id,
        "superseded_normalized_item_ids": superseded_normalized_item_ids(
            db, item
        ),
        "event_policy": event_aggregation_policy(item),
        "event_routes": [
            asdict(route) for route in aggregation_routes(item)
        ],
        "source": {
            "source_id": item.raw_item.source_id,
            "source_name": item.raw_item.source.name,
            "connector_type": item.raw_item.source.connector_type,
            "authority": item.raw_item.source.connector_config.get(
                "authority_level"
            ),
            "is_repost": has_quoted_post(item.raw_item.content_blocks),
        },
        "published_at": (
            item.raw_item.published_at.isoformat()
            if item.raw_item.published_at
            else None
        ),
    }
    assert_execution_owned(db, execution_guard)
    db.commit()
    decision = await LLMClient().propose_event(
        item=item_payload,
        candidates=candidate_payloads,
        stable_event_key=stable_event_key(item),
        knowledge_rules=rules,
        route_aggregation_keys=[
            route.aggregation_key for route in aggregation_routes(item)
        ],
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
                    "category": item.category,
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
    decision = EventDecisionDraft.model_validate(run.decision_draft)
    candidate_ids = {
        int(candidate["event_id"]) for candidate in run.candidate_snapshot
    }
    now = datetime.now(UTC)

    item = run.normalized_item
    created_count = 0
    updated_count = 0
    affected_event_ids = []
    for membership in decision.memberships:
        existing_event_id = membership.existing_event_id
        if existing_event_id is None:
            event = create_event(
                db,
                normalized_item_id=run.normalized_item_id,
                aggregation_key=membership.aggregation_key,
                title=membership.timeline_note
                or item.translated_title
                or item.normalized_title,
                summary=item.summary,
                category=item.category,
                event_type=membership.event_type,
                lifecycle_status=membership.lifecycle_status or "developing",
                membership_role=membership.membership_role,
                evidence_stance=membership.evidence_stance,
                independence_key=f"source:{item.raw_item.source_id}",
                is_official_confirmation=_official_confirmation(
                    item, membership
                ),
                importance_score=item.importance_score,
                importance_evidence=[
                    f"由成员消息 {item.id} 的五维重要性初始化"
                ],
                latest_development=membership.timeline_note,
                change_note=f"创建时间线节点：{membership.timeline_note}",
                evidence={
                    "event_run_id": run.id,
                    "timeline_note": membership.timeline_note,
                },
                commit=False,
            )
            affected_event_ids.append(event.id)
            created_count += 1
            continue
        if existing_event_id not in candidate_ids:
            raise ValueError(
                "decision references an event outside the candidate snapshot"
            )
        event, added = add_message_to_event(
            db,
            event_id=existing_event_id,
            normalized_item_id=run.normalized_item_id,
            lifecycle_status=membership.lifecycle_status,
            membership_role=membership.membership_role,
            evidence_stance=membership.evidence_stance,
            independence_key=f"source:{item.raw_item.source_id}",
            is_official_confirmation=_official_confirmation(
                item, membership
            ),
            is_significant_update=membership.update_kind not in {
                "context",
                "duplicate_evidence",
            },
            importance_score=item.importance_score,
            importance_evidence=[
                f"由成员消息 {item.id} 的五维重要性更新"
            ],
            latest_development=membership.timeline_note,
            change_note=f"时间线节点：{membership.timeline_note}",
            evidence={
                "event_run_id": run.id,
                "timeline_note": membership.timeline_note,
            },
            commit=False,
        )
        if not added:
            raise ValueError("normalized item was already attached before approval")
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
                is_active=False,
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


def _official_confirmation(
    item: NormalizedItem,
    membership: EventMembershipDraft,
) -> bool:
    return (
        membership.is_official_confirmation
        and item.credibility == "official"
        and not has_quoted_post(item.raw_item.content_blocks)
    )
