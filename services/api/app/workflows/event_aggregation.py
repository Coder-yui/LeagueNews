import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.domain.event_admission import AdmissionDecision, decide_event_admission
from app.domain.event_families import (
    canonicalize_event_anchors,
    determine_mythic_shop_market,
    is_mythic_shop_event,
    mythic_shop_rotation_period_from_date,
)
from app.domain.event_identity import (
    event_identity_key,
    event_identity_matches,
    identity_is_supported_by_message,
    identity_anchors_with_hints,
    project_event_identity,
    resolve_esports_match_anchors,
    select_identity_evidence,
)
from app.domain.event_granularity import editorial_granularity_guidance
from app.domain.event_types import AGGREGATION_POLICY_VERSION
from app.domain.importance import IMPORTANCE_POLICY_VERSION, score_importance_profile
from app.core.config import settings
from app.models.event import Event, EventAggregationRun
from app.models.normalized_item import NormalizedItem
from app.schemas.event_aggregation import EventMentionDecision
from app.services.event_candidates import recall_event_candidates
from app.services.event_metrics import refresh_event_metrics
from app.services.events import add_event_mention, create_event
from app.services.llm import (
    LLMAnalysisError,
    LLMClient,
    LLMConfigurationError,
    execution_metadata,
)
from app.services.raw_item_versions import is_latest_raw_item


def _run_key(item: NormalizedItem) -> str:
    return f"{item.id}:{item.current_revision}:{AGGREGATION_POLICY_VERSION}"


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _aggregation_key(
    *, event_family: str, products: list[str], canonical_anchors: dict[str, Any]
) -> str:
    identity_products = [] if is_mythic_shop_event(event_family, canonical_anchors) else sorted(products)
    identity_key = event_identity_key(event_family, canonical_anchors)
    if identity_key is None:
        raise ValueError(f"{event_family} lacks a deterministic identity")
    identity = {
        "event_family": event_family,
        "products": identity_products,
        "identity_key": identity_key,
    }
    return f"event-v1:{event_family}:{_fingerprint(identity)[:32]}"


def _normalize_upstream_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return None
    return urlunsplit(("https", parsed.hostname.casefold(), parsed.path.rstrip("/"), "", ""))


def _upstream_url(item: NormalizedItem) -> str | None:
    classification_source = item.facets.get("classification_source")
    if isinstance(classification_source, dict):
        value = classification_source.get("upstream_source_url")
        if isinstance(value, str) and (normalized := _normalize_upstream_url(value)):
            return normalized
    for block in item.raw_item.content_blocks:
        if block.get("type") == "embed" and block.get("embed_kind") == "quoted_post":
            value = block.get("source_url")
            if isinstance(value, str) and (normalized := _normalize_upstream_url(value)):
                return normalized
    return None


def _independence_group(item: NormalizedItem) -> str | None:
    if item.content_form == "repost":
        upstream = _upstream_url(item)
        return f"upstream:{upstream}" if upstream else None
    return f"source:{item.raw_item.source_id}"


def _verified_source_role(item: NormalizedItem, proposed_role: str) -> str:
    if proposed_role != "responsible_official":
        return proposed_role
    if item.content_form == "repost":
        return "republisher"
    if not item.raw_item.source.is_official:
        return "unknown"
    return proposed_role


def _domain_importance_snapshot(
    decision: EventMentionDecision,
) -> dict[str, Any] | None:
    semantics = decision.importance
    if semantics is None:
        return None
    features = semantics.model_dump(mode="json", exclude={"profile"})
    result = score_importance_profile(
        semantics.profile,
        features,
        content=decision.evidence_excerpt,
    )
    return {
        "policy_version": IMPORTANCE_POLICY_VERSION,
        "profile": result.profile,
        "score": result.score,
        "features": dict(result.features),
        "modifiers": list(result.modifiers),
    }


def _source_payload(item: NormalizedItem) -> dict[str, object]:
    return {
        "source_id": item.raw_item.source_id,
        "source_name": item.raw_item.source.name,
        "connector_type": item.raw_item.source.connector_type,
        "is_official": item.raw_item.source.is_official,
        "reliability_score": item.raw_item.source.reliability_score,
        "content_form": item.content_form,
        "classification_source": item.facets.get("classification_source", {}),
        "published_at": (
            item.raw_item.published_at.isoformat() if item.raw_item.published_at else None
        ),
        "ingested_at": (
            item.raw_item.ingested_at.isoformat() if item.raw_item.ingested_at else None
        ),
    }


def _block_text(block: dict[str, Any]) -> str:
    if isinstance(block.get("text"), str):
        return str(block["text"])
    if isinstance(block.get("items"), list):
        return "\n".join(str(value) for value in block["items"] if str(value).strip())
    return ""


def _select_content(item: NormalizedItem, *, limit: int = 24_000) -> tuple[str, dict[str, object]]:
    content = item.translated_text or item.normalized_text
    if len(content) <= limit:
        return content, {
            "content_truncated": False,
            "original_characters": len(content),
            "selected_characters": len(content),
            "strategy": "full_existing_message_projection",
        }
    terms = {
        str(value).strip().casefold()
        for entity in item.entities
        for value in (
            entity.get("name"),
            entity.get("display_name"),
            entity.get("canonical_name"),
        )
        if isinstance(value, str) and len(value.strip()) >= 2
    }
    blocks = item.translated_content_blocks
    scored: list[tuple[int, int, str]] = []
    for index, block in enumerate(blocks):
        text_value = _block_text(block).strip()
        if not text_value:
            continue
        lowered = text_value.casefold()
        score = sum(1 for term in terms if term in lowered)
        scored.append((score, index, text_value))
    chosen: list[tuple[int, str]] = []
    used = 0
    for _score, index, text_value in sorted(scored, key=lambda row: (-row[0], row[1])):
        if used >= limit:
            break
        remaining = limit - used
        selected = text_value[:remaining]
        chosen.append((index, selected))
        used += len(selected) + 1
    if not chosen:
        selected_content = content[:limit]
        strategy = "bounded_existing_message_projection"
    else:
        selected_content = "\n".join(value for _, value in sorted(chosen))[:limit]
        strategy = "entity_relevant_translated_blocks"
    return selected_content, {
        "content_truncated": True,
        "original_characters": len(content),
        "selected_characters": len(selected_content),
        "selected_block_indexes": [index for index, _ in sorted(chosen)],
        "strategy": strategy,
    }


def _message_payload(item: NormalizedItem) -> tuple[dict[str, object], dict[str, object]]:
    selected_content, truncation = _select_content(item)
    media_data = [
        {
            "extraction_id": link.media_extraction_id,
            "translated_data": link.translated_structured_data,
        }
        for link in item.media_links[:12]
    ]
    payload: dict[str, object] = {
        "normalized_item_id": item.id,
        "normalized_item_revision": item.current_revision,
        "title": item.translated_title or item.normalized_title,
        "summary": item.summary,
        "content": selected_content,
        "products": item.products,
        "content_form": item.content_form,
        "message_type": item.message_type,
        "topics": item.topics,
        "entities": item.entities,
        "message_importance": {
            "score": item.importance_score,
            "dimensions": item.importance_dimensions,
        },
        "structured_media": media_data,
        "source": _source_payload(item),
        "editorial_granularity_guidance": editorial_granularity_guidance(item),
    }
    return payload, truncation


def _mythic_shop_anchors(
    item: NormalizedItem,
    *,
    event_family: str,
    anchors: dict[str, Any],
) -> dict[str, Any]:
    if not is_mythic_shop_event(event_family, anchors):
        return canonicalize_event_anchors(event_family, anchors)
    text = "\n".join(
        value
        for value in (
            item.raw_item.native_title,
            item.translated_title,
            item.normalized_title,
            item.normalized_text,
            item.translated_text,
        )
        if isinstance(value, str) and value.strip()
    )
    structured_data: list[Any] = [anchors, item.facets]
    structured_data.extend(
        value
        for link in item.media_links
        for value in (
            link.media_extraction.structured_data,
            link.translated_structured_data,
        )
    )
    market = determine_mythic_shop_market(
        text=text,
        structured_data=structured_data,
        source_connector_type=item.raw_item.source.connector_type,
    )
    observed_at = item.raw_item.published_at or item.raw_item.ingested_at
    rotation_period = (
        mythic_shop_rotation_period_from_date(observed_at.date())
        if observed_at is not None
        else None
    )
    return canonicalize_event_anchors(
        event_family,
        {**anchors, "market": market, "rotation_period": rotation_period},
    )


def _record_for_item(db: Session, item: NormalizedItem) -> EventAggregationRun | None:
    return db.scalar(
        select(EventAggregationRun).where(EventAggregationRun.idempotency_key == _run_key(item))
    )


def _complete_without_call(
    db: Session,
    run: EventAggregationRun,
    *,
    outcome: str,
    admission: AdmissionDecision,
) -> EventAggregationRun:
    run.admission_decision = admission.decision
    run.candidate_snapshot = []
    run.decision_draft = {"mentions": [], "admission_reasons": list(admission.reasons)}
    run.model_call_count = 0
    run.status = "completed"
    run.outcome = outcome
    run.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(run)
    return run


def _fact_identity(value: dict[str, Any]) -> str:
    for key in ("id", "key", "name", "fact"):
        if value.get(key):
            return f"{key}:{value[key]}"
    return _fingerprint(value)


def _apply_fact_changes(
    current: list[dict[str, Any]], changes: dict[str, Any]
) -> list[dict[str, Any]]:
    values = {_fact_identity(value): dict(value) for value in current}
    for identity in changes.get("remove", []):
        values.pop(str(identity), None)
        for key in [key for key in values if key.endswith(f":{identity}")]:
            values.pop(key, None)
    for value in changes.get("replace", []):
        values[_fact_identity(value)] = dict(value)
    for value in changes.get("add", []):
        values.setdefault(_fact_identity(value), dict(value))
    return list(values.values())


async def aggregate_normalized_item(
    db: Session,
    item: NormalizedItem,
    *,
    llm_client: LLMClient | None = None,
) -> EventAggregationRun:
    if item.publication_status != "published":
        raise ValueError("event aggregation requires a published NormalizedItem")
    if not is_latest_raw_item(db, item.raw_item):
        raise ValueError("event aggregation requires the latest RawItem revision")

    run = _record_for_item(db, item)
    if run is not None and run.status == "completed":
        return run
    previous_model_call_count = run.model_call_count if run is not None else 0
    if run is None:
        run = EventAggregationRun(
            normalized_item_id=item.id,
            normalized_item_revision=item.current_revision,
            status="running",
            current_stage="admission",
            aggregation_policy_version=AGGREGATION_POLICY_VERSION,
            idempotency_key=_run_key(item),
        )
        db.add(run)
    else:
        run.status = "running"
        run.outcome = None
        run.error_message = None
        run.completed_at = None
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = _record_for_item(db, item)
        if concurrent is not None and concurrent.status == "completed":
            return concurrent
        raise RuntimeError("event aggregation is already running for this item") from None
    db.refresh(run)

    admission = decide_event_admission(item)
    run.admission_decision = admission.decision
    if admission.decision == "skip":
        return _complete_without_call(
            db, run, outcome="skipped_by_admission", admission=admission
        )

    candidates = recall_event_candidates(
        db,
        item=item,
        family_hints=list(admission.family_hints),
        anchors=admission.strong_anchors,
    )
    run.candidate_snapshot = candidates
    if admission.decision == "update_existing_only" and not candidates:
        return _complete_without_call(
            db, run, outcome="no_existing_candidate", admission=admission
        )

    message, truncation = _message_payload(item)
    run.current_stage = "model_decision"
    run.input_fingerprint = _fingerprint(
        {"message": message, "candidates": candidates, "admission": admission.decision}
    )
    db.commit()

    client = llm_client or LLMClient()
    try:
        result = await client.aggregate_events(
            message=message,
            admission_decision=admission.decision,
            family_hints=list(admission.family_hints),
            candidates=candidates,
        )
        metadata = execution_metadata(result)
        run.model_call_count = (
            previous_model_call_count + int(metadata.get("retry_count") or 0) + 1
        )
        run.decision_draft = {
            **result.model_dump(mode="json"),
            "input_truncation": truncation,
            "execution_metadata": metadata,
        }
        run.current_stage = "apply"
        db.commit()
    except (LLMConfigurationError, LLMAnalysisError) as exc:
        db.rollback()
        failed = db.get(EventAggregationRun, run.id)
        if failed is None:
            raise
        failed.status = "failed"
        failed.outcome = "model_error"
        failed.model_call_count = previous_model_call_count + (
            0 if isinstance(exc, LLMConfigurationError) else settings.llm_max_retries + 1
        )
        failed.error_message = str(exc)
        failed.completed_at = datetime.now(UTC)
        db.commit()
        raise

    try:
        applied_count = 0
        affected_event_ids: set[int] = set()
        daily_match_roundup = "daily_esports_match_roundup" in (
            message.get("editorial_granularity_guidance") or []
        )
        admission_anchors = admission.strong_anchors
        for decision in result.mentions:
            if decision.action == "ignore":
                continue
            if daily_match_roundup and decision.event_family != "esports_match":
                raise ValueError(
                    "daily match reminders/results may only create or update concrete esports_match events"
                )
            fact_changes = decision.key_fact_changes.model_dump(mode="json")
            claim_fingerprint = _fingerprint(
                {
                    "family": decision.event_family,
                    "anchors": decision.canonical_anchors,
                    "excerpt": decision.evidence_excerpt,
                }
            )
            independence_group = _independence_group(item)
            source_role = _verified_source_role(item, decision.source_role)
            domain_importance_snapshot = _domain_importance_snapshot(decision)
            canonical_anchors = _mythic_shop_anchors(
                item,
                event_family=decision.event_family,
                anchors=decision.canonical_anchors,
            )
            canonical_anchors = identity_anchors_with_hints(
                decision.event_family, canonical_anchors, admission_anchors
            )
            observed_at = item.raw_item.published_at or item.raw_item.ingested_at
            identity_evidence = select_identity_evidence(
                decision.event_family,
                message_text="\n".join(
                    value
                    for value in (
                        str(message.get("title") or ""),
                        str(message.get("summary") or ""),
                        str(message.get("content") or ""),
                    )
                    if value
                ),
                mention_excerpt=decision.evidence_excerpt,
            )
            incoming_anchors = canonical_anchors
            message_identity_text = "\n".join(
                str(message.get(key) or "") for key in ("title", "summary", "content")
            )
            if decision.event_family == "esports_match":
                incoming_anchors = resolve_esports_match_anchors(
                    incoming_anchors,
                    message_text=message_identity_text,
                    mention_excerpt=decision.evidence_excerpt,
                    observed_on=observed_at,
                )
            if not identity_is_supported_by_message(
                decision.event_family,
                incoming_anchors,
                message_text=message_identity_text,
                mention_excerpt=decision.evidence_excerpt,
                message_anchors=admission_anchors,
                observed_on=observed_at,
            ):
                raise ValueError(
                    f"mention[{decision.mention_index}] identity is not supported by message evidence"
                )
            projected_identity = project_event_identity(
                decision.event_family,
                incoming_anchors,
                evidence_text=identity_evidence,
                observed_on=observed_at,
            )
            if not projected_identity:
                raise ValueError(
                    f"{decision.event_family} mention lacks a deterministic identity"
                )
            if decision.action == "create":
                canonical_anchors = projected_identity
                aggregation_key = _aggregation_key(
                    event_family=decision.event_family,
                    products=item.products,
                    canonical_anchors=canonical_anchors,
                )
                existing_event = db.scalar(
                    select(Event).where(Event.aggregation_key == aggregation_key)
                )
                if existing_event is None:
                    event, _created = create_event(
                        db,
                        normalized_item_id=item.id,
                        mention_index=decision.mention_index,
                        event_family=decision.event_family,
                        products=item.products,
                        canonical_anchors=canonical_anchors,
                        aggregation_key=aggregation_key,
                        title=decision.event_title or "",
                        current_summary=decision.proposed_summary or "",
                        relation=decision.relation,
                        source_role=source_role,
                        materiality=decision.materiality,
                        independence_group=independence_group,
                        evidence_excerpt=decision.evidence_excerpt,
                        structured_fact_changes=fact_changes,
                        domain_importance_snapshot=domain_importance_snapshot,
                        content_fingerprint=claim_fingerprint,
                        latest_development=decision.latest_development or "",
                        key_facts=list(decision.key_fact_changes.add),
                        commit=False,
                        use_savepoint=False,
                    )
                else:
                    event, _created = add_event_mention(
                        db,
                        event_id=existing_event.id,
                        normalized_item_id=item.id,
                        mention_index=decision.mention_index,
                        relation=decision.relation,
                        source_role=source_role,
                        materiality=decision.materiality,
                        independence_group=independence_group,
                        evidence_excerpt=decision.evidence_excerpt,
                        structured_fact_changes=fact_changes,
                        domain_importance_snapshot=domain_importance_snapshot,
                        content_fingerprint=claim_fingerprint,
                        title=decision.event_title,
                        current_summary=decision.proposed_summary,
                        latest_development=decision.latest_development,
                        canonical_anchors=existing_event.canonical_anchors,
                        key_facts=_apply_fact_changes(
                            existing_event.key_facts, fact_changes
                        ),
                        commit=False,
                        use_savepoint=False,
                    )
            else:
                event = db.get(Event, int(decision.candidate_event_id or 0))
                if event is None:
                    raise ValueError(f"candidate event {decision.candidate_event_id} disappeared")
                if event.event_family != decision.event_family:
                    raise ValueError("candidate event family does not match")
                if not event_identity_matches(
                    decision.event_family,
                    event.canonical_anchors,
                    incoming_anchors,
                    evidence_text=identity_evidence,
                    observed_on=observed_at,
                ):
                    raise ValueError("event update candidate identity does not match")
                add_event_mention(
                    db,
                    event_id=event.id,
                    normalized_item_id=item.id,
                    mention_index=decision.mention_index,
                    relation=decision.relation,
                    source_role=source_role,
                    materiality=decision.materiality,
                    independence_group=independence_group,
                    evidence_excerpt=decision.evidence_excerpt,
                    structured_fact_changes=fact_changes,
                    domain_importance_snapshot=domain_importance_snapshot,
                    content_fingerprint=claim_fingerprint,
                    title=decision.event_title,
                    current_summary=decision.proposed_summary,
                    latest_development=decision.latest_development,
                    canonical_anchors=event.canonical_anchors,
                    key_facts=_apply_fact_changes(event.key_facts, fact_changes),
                    commit=False,
                    use_savepoint=False,
                )
            affected_event_ids.add(event.id)
            applied_count += 1
        refresh_event_metrics(db, affected_event_ids)
        run.status = "completed"
        run.outcome = "applied" if applied_count else "ignored"
        run.applied_at = datetime.now(UTC)
        run.completed_at = run.applied_at
        db.commit()
        db.refresh(run)
        return run
    except Exception as exc:
        db.rollback()
        failed = db.get(EventAggregationRun, run.id)
        if failed is not None:
            failed.status = "failed"
            failed.outcome = "apply_error"
            failed.error_message = str(exc)
            failed.completed_at = datetime.now(UTC)
            db.commit()
        raise
