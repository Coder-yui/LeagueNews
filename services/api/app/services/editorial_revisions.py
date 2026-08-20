from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.event import Event, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem, NormalizedItemRevision
from app.schemas.editorial import (
    EditorialRevisionResult,
    ManualEventRevisionCommand,
    ManualMessageRevisionCommand,
)


class EditorialTargetNotFoundError(ValueError):
    pass


class EditorialRevisionConflictError(ValueError):
    pass


def _message_snapshot(item: NormalizedItem) -> dict[str, Any]:
    return {
        "normalized_title": item.normalized_title,
        "normalized_text": item.normalized_text,
        "summary": item.summary,
        "entities": list(item.entities or []),
        "products": list(item.products or []),
        "message_type": item.message_type,
        "topics": list(item.topics or []),
        "classification_version": item.classification_version,
        "content_form": item.content_form,
        "facets": dict(item.facets or {}),
        "importance_score": item.importance_score,
        "importance_dimensions": dict(item.importance_dimensions or {}),
        "importance_policy_version": item.importance_policy_version,
        "importance_calculation": dict(item.importance_calculation or {}),
        "priority_score": item.priority_score,
        "priority_calculation": dict(item.priority_calculation or {}),
        "language": item.language,
        "source_language": item.source_language,
        "target_language": item.target_language,
        "translated_title": item.translated_title,
        "translated_text": item.translated_text,
        "translated_content_blocks": list(item.translated_content_blocks or []),
        "translation_status": item.translation_status,
        "translation_model": item.translation_model,
        "analysis_model": item.analysis_model,
        "analysis_version": item.analysis_version,
        "approved_media_extraction_ids": item.approved_media_extraction_ids,
        "translated_media_extractions": item.translated_media_extractions,
        "manual_override_fields": list(item.manual_override_fields or []),
    }


def _event_snapshot(event: Event) -> dict[str, Any]:
    return {
        "title": event.title,
        "current_summary": event.current_summary,
        "event_family": event.event_family,
        "products": list(event.products or []),
        "canonical_anchors": dict(event.canonical_anchors or {}),
        "latest_development": event.latest_development,
        "key_facts": list(event.key_facts or []),
        "lifecycle_status": event.lifecycle_status,
        "manual_override_fields": list(event.manual_override_fields or []),
    }


def _clone_current_event_mentions(
    db: Session,
    *,
    item: NormalizedItem,
    previous_revision: int,
) -> None:
    mentions = db.scalars(
        select(EventMention).where(
            EventMention.normalized_item_id == item.id,
            EventMention.normalized_item_revision == previous_revision,
        )
    ).all()
    for mention in mentions:
        db.add(
            EventMention(
                event_id=mention.event_id,
                normalized_item_id=item.id,
                normalized_item_revision=item.current_revision,
                mention_index=mention.mention_index,
                aggregation_policy_version=mention.aggregation_policy_version,
                relation=mention.relation,
                source_role=mention.source_role,
                independence_group=mention.independence_group,
                materiality=mention.materiality,
                evidence_excerpt=mention.evidence_excerpt,
                structured_fact_changes=dict(mention.structured_fact_changes or {}),
                content_fingerprint=mention.content_fingerprint,
                source_reliability_snapshot=mention.source_reliability_snapshot,
                source_published_at=mention.source_published_at,
            )
        )


def revise_published_message(
    db: Session,
    command: ManualMessageRevisionCommand,
    *,
    commit: bool = True,
) -> EditorialRevisionResult:
    existing = db.scalar(
        select(NormalizedItemRevision).where(
            NormalizedItemRevision.idempotency_key == command.metadata.idempotency_key
        )
    )
    if existing is not None:
        if existing.normalized_item_id != command.normalized_item_id:
            raise EditorialRevisionConflictError("idempotency key belongs to another message")
        return EditorialRevisionResult(
            target_id=existing.normalized_item_id,
            revision=existing.revision,
            changed_fields=sorted(
                existing.snapshot.get("editorial_metadata", {}).get(
                    "changed_fields", []
                )
            ),
            idempotent_replay=True,
        )

    item = db.scalar(
        select(NormalizedItem)
        .where(NormalizedItem.id == command.normalized_item_id)
        .with_for_update()
    )
    if item is None:
        raise EditorialTargetNotFoundError(
            f"normalized item {command.normalized_item_id} not found"
        )
    if item.publication_status != "published":
        raise EditorialRevisionConflictError("only a published message can be revised")
    if item.current_revision != command.expected_revision:
        raise EditorialRevisionConflictError(
            f"message revision changed from {command.expected_revision} "
            f"to {item.current_revision}"
        )

    changes = command.patch.model_dump(exclude_unset=True)
    if "importance_score" in changes:
        calculation = dict(item.importance_calculation or {})
        calculation.setdefault("computed_score", item.importance_score)
        calculation["final_score"] = changes["importance_score"]
        calculation["manual_override"] = {
            "score": changes["importance_score"],
            "reason": command.metadata.reason,
            "editor_id": command.metadata.editor_id,
        }
        item.importance_calculation = calculation
    if "priority_score" in changes:
        calculation = dict(item.priority_calculation or {})
        calculation.setdefault("computed_score", item.priority_score)
        calculation["final_score"] = changes["priority_score"]
        calculation["manual_override"] = {
            "score": changes["priority_score"],
            "reason": command.metadata.reason,
            "editor_id": command.metadata.editor_id,
        }
        item.priority_calculation = calculation
    for field, value in changes.items():
        setattr(item, field, value)
    previous_revision = item.current_revision
    item.current_revision += 1
    if command.metadata.lock_edited_fields:
        item.manual_override_fields = sorted(
            set(item.manual_override_fields or []).union(changes)
        )
    _clone_current_event_mentions(db, item=item, previous_revision=previous_revision)
    snapshot = _message_snapshot(item)
    snapshot["editorial_metadata"] = {"changed_fields": sorted(changes)}
    revision = NormalizedItemRevision(
        normalized_item_id=item.id,
        revision=item.current_revision,
        snapshot=snapshot,
        processing_run_id=None,
        change_note=command.metadata.reason,
        revision_source="manual",
        editor_id=command.metadata.editor_id,
        idempotency_key=command.metadata.idempotency_key,
    )
    db.add(revision)
    if commit:
        db.commit()
        db.refresh(item)
    else:
        db.flush()
    return EditorialRevisionResult(
        target_id=item.id,
        revision=item.current_revision,
        changed_fields=sorted(changes),
    )


def revise_published_event(
    db: Session,
    command: ManualEventRevisionCommand,
    *,
    commit: bool = True,
) -> EditorialRevisionResult:
    existing = db.scalar(
        select(EventRevision).where(
            EventRevision.idempotency_key == command.metadata.idempotency_key
        )
    )
    if existing is not None:
        if existing.event_id != command.event_id:
            raise EditorialRevisionConflictError("idempotency key belongs to another event")
        return EditorialRevisionResult(
            target_id=existing.event_id,
            revision=existing.revision,
            changed_fields=sorted(
                existing.evidence_snapshot.get("changed_fields", [])
            ),
            idempotent_replay=True,
        )

    event = db.scalar(
        select(Event).where(Event.id == command.event_id).with_for_update()
    )
    if event is None:
        raise EditorialTargetNotFoundError(f"event {command.event_id} not found")
    if event.current_revision != command.expected_revision:
        raise EditorialRevisionConflictError(
            f"event revision changed from {command.expected_revision} "
            f"to {event.current_revision}"
        )

    changes = command.patch.model_dump(exclude_unset=True)
    for field, value in changes.items():
        setattr(event, field, value)
    event.current_revision += 1
    if command.metadata.lock_edited_fields:
        event.manual_override_fields = sorted(
            set(event.manual_override_fields or []).union(changes)
        )
    revision = EventRevision(
        event_id=event.id,
        revision=event.current_revision,
        title=event.title,
        summary=event.current_summary,
        change_note=command.metadata.reason,
        evidence_snapshot={
            "kind": "manual_editorial_revision",
            "projection_snapshot": _event_snapshot(event),
            "manual_override_fields": list(event.manual_override_fields or []),
            "changed_fields": sorted(changes),
        },
        revision_source="manual",
        editor_id=command.metadata.editor_id,
        idempotency_key=command.metadata.idempotency_key,
    )
    db.add(revision)
    if commit:
        db.commit()
        db.refresh(event)
    else:
        db.flush()
    return EditorialRevisionResult(
        target_id=event.id,
        revision=event.current_revision,
        changed_fields=sorted(changes),
    )
