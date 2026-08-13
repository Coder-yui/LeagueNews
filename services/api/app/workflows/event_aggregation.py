import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.domain.event_admission import AdmissionDecision, minimal_event_filter
from app.domain.event_families import EventSpace, product_supports_family
from app.domain.event_types import AGGREGATION_POLICY_VERSION
from app.models.event import Event, EventAggregationRun
from app.models.normalized_item import NormalizedItem
from app.repositories.events import event_ids_for_normalized_item
from app.schemas.event_aggregation import EventAggregationResult, EventMentionDecision
from app.services.event_candidates import recall_event_candidates
from app.services.event_semantics import semantic_projection
from app.services.event_metrics import refresh_event_metrics
from app.services.events import add_event_mention, create_event
from app.services.llm import (
    LLMAnalysisError,
    LLMClient,
    LLMConfigurationError,
    execution_metadata,
)
from app.services.raw_item_versions import is_latest_raw_item


STALE_RUNNING_RUN_AFTER = timedelta(seconds=settings.pipeline_worker_lease_seconds)


class SupersededEventAggregationError(RuntimeError):
    """The requested NormalizedItem revision is no longer the current projection."""


def _run_key(item: NormalizedItem) -> str:
    return f"{item.id}:{item.current_revision}:{AGGREGATION_POLICY_VERSION}"


def _fingerprint(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


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


def _source_payload(item: NormalizedItem) -> dict[str, object]:
    return {
        "source_id": item.raw_item.source_id,
        "source_name": item.raw_item.source.name,
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


def _select_content(
    item: NormalizedItem, *, limit: int = 24_000
) -> tuple[str, dict[str, object]]:
    _, content = semantic_projection(item)
    if len(content) <= limit:
        return content, {
            "content_truncated": False,
            "original_characters": len(content),
            "selected_characters": len(content),
            "strategy": "full_message_projection",
        }
    entity_terms = {
        str(value).strip().casefold()
        for entity in item.entities
        for value in (
            entity.get("name"),
            entity.get("display_name"),
            entity.get("canonical_name"),
        )
        if isinstance(value, str) and len(value.strip()) >= 2
    }
    scored: list[tuple[int, int, str]] = []
    for index, block in enumerate(item.translated_content_blocks):
        text_value = _block_text(block).strip()
        if not text_value:
            continue
        lowered = text_value.casefold()
        scored.append((sum(term in lowered for term in entity_terms), index, text_value))
    selected: list[tuple[int, str]] = []
    used = 0
    for _score, index, text_value in sorted(scored, key=lambda row: (-row[0], row[1])):
        if used >= limit:
            break
        value = text_value[: limit - used]
        selected.append((index, value))
        used += len(value) + 1
    bounded = (
        "\n".join(value for _, value in sorted(selected))[:limit]
        if selected
        else content[:limit]
    )
    return bounded, {
        "content_truncated": True,
        "original_characters": len(content),
        "selected_characters": len(bounded),
        "selected_block_indexes": [index for index, _ in sorted(selected)],
        "strategy": "entity_relevant_blocks" if selected else "bounded_message_projection",
    }


def _message_payload(item: NormalizedItem) -> tuple[dict[str, object], dict[str, object]]:
    title, _ = semantic_projection(item)
    content, truncation = _select_content(item)
    return {
        "normalized_item_id": item.id,
        "normalized_item_revision": item.current_revision,
        "title": title,
        "summary": item.summary,
        "content": content,
        "products": item.products,
        "content_form": item.content_form,
        "message_type": item.message_type,
        "topics": item.topics,
        "entities": item.entities,
        "structured_media": [
            {
                "extraction_id": link.media_extraction_id,
                "translated_data": link.translated_structured_data,
            }
            for link in item.media_links[:12]
        ],
        "source": _source_payload(item),
    }, truncation


def _record_for_item(db: Session, item: NormalizedItem) -> EventAggregationRun | None:
    return db.scalar(
        select(EventAggregationRun)
        .where(EventAggregationRun.idempotency_key == _run_key(item))
        .with_for_update()
    )


def _is_stale_running_run(
    run: EventAggregationRun, *, as_of: datetime | None = None
) -> bool:
    last_activity = run.updated_at or run.created_at
    if last_activity is None:
        return False
    normalized = (
        last_activity.replace(tzinfo=UTC)
        if last_activity.tzinfo is None
        else last_activity.astimezone(UTC)
    )
    reference = as_of or datetime.now(UTC)
    return reference - normalized >= STALE_RUNNING_RUN_AFTER


def _reclaim_stale_run(run: EventAggregationRun) -> None:
    recovered_at = datetime.now(UTC)
    draft = dict(run.decision_draft or {})
    draft["recovery"] = {
        "type": "stale_running_run_reclaimed",
        "recovered_at": recovered_at.isoformat(),
        "previous_stage": run.current_stage,
        "previous_updated_at": (
            run.updated_at.isoformat() if run.updated_at is not None else None
        ),
    }
    run.decision_draft = draft
    run.status = "running"
    run.outcome = None
    run.error_message = None
    run.completed_at = None
    run.applied_at = None


def _decision_from_draft(run: EventAggregationRun) -> EventAggregationResult | None:
    if run.current_stage != "apply_membership":
        return None
    draft = run.decision_draft or {}
    if "mentions" not in draft:
        return None
    try:
        return EventAggregationResult.model_validate({"mentions": draft["mentions"]})
    except ValueError:
        return None


def _lock_current_item_for_membership(
    db: Session, item: NormalizedItem
) -> NormalizedItem:
    """Fence membership writes to the revision that is still publicly current."""

    current = db.scalar(
        select(NormalizedItem)
        .where(NormalizedItem.id == item.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if current is None:
        raise SupersededEventAggregationError("normalized item no longer exists")
    if current.current_revision != item.current_revision:
        raise SupersededEventAggregationError(
            "normalized item revision was superseded before membership apply"
        )
    if current.publication_status != "published":
        raise SupersededEventAggregationError(
            "normalized item is no longer a published projection"
        )
    if not is_latest_raw_item(db, current.raw_item):
        raise SupersededEventAggregationError(
            "normalized item belongs to a superseded RawItem revision"
        )
    return current


def _complete_superseded_run(
    db: Session, run: EventAggregationRun
) -> EventAggregationRun:
    if run.status != "completed":
        run.status = "completed"
        run.outcome = "ignored"
        run.error_message = "normalized item revision was superseded before membership apply"
        run.completed_at = datetime.now(UTC)
        db.commit()
        db.refresh(run)
    return run


def _complete_without_call(
    db: Session,
    run: EventAggregationRun,
    *,
    outcome: str,
    admission: AdmissionDecision,
) -> EventAggregationRun:
    run.admission_decision = admission.decision
    run.candidate_snapshot = []
    run.decision_draft = {"mentions": [], "filter_reasons": list(admission.reasons)}
    run.model_call_count = 0
    run.status = "completed"
    run.outcome = outcome
    run.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(run)
    return run


def _resolve_mention_product(
    mention: EventMentionDecision, *, item_products: list[str]
) -> str:
    products = [str(product) for product in item_products]
    if mention.product is not None:
        product = str(mention.product)
        if product not in products:
            raise ValueError(
                f"mention[{mention.mention_index}] product {product} is not in message products"
            )
        return product
    concrete_products = [product for product in products if product != "unknown"]
    if len(concrete_products) == 1 and len(set(products)) == 1:
        return concrete_products[0]
    raise ValueError(
        f"mention[{mention.mention_index}] must specify product for a cross-product message"
    )


def _validate_candidate_references(
    result: EventAggregationResult,
    candidates: list[dict[str, Any]],
    *,
    item_products: list[str],
) -> dict[int, str]:
    candidate_by_id = {int(candidate["event_id"]): candidate for candidate in candidates}
    resolved_products: dict[int, str] = {}
    for mention in result.mentions:
        if mention.action == "ignore":
            continue
        product = _resolve_mention_product(mention, item_products=item_products)
        resolved_products[mention.mention_index] = product
        if mention.action != "attach":
            continue
        candidate = candidate_by_id.get(int(mention.event_id or 0))
        if candidate is None:
            raise ValueError(f"mention[{mention.mention_index}] references a non-candidate event")
        if candidate.get("event_family") != mention.event_family:
            raise ValueError(f"mention[{mention.mention_index}] candidate family does not match")
        candidate_products = {str(value) for value in candidate.get("products") or []}
        if candidate_products != {product}:
            raise ValueError(
                f"mention[{mention.mention_index}] candidate must be isolated to product {product}"
            )
    return resolved_products


def _validate_product_family_compatibility(
    result: EventAggregationResult, resolved_products: dict[int, str]
) -> None:
    for mention in result.mentions:
        if mention.action == "ignore":
            continue
        product = resolved_products[mention.mention_index]
        if product != "unknown" and not product_supports_family(
            product, mention.event_family  # type: ignore[arg-type]
        ):
            raise ValueError(
                f"mention[{mention.mention_index}] event_family {mention.event_family} "
                f"is not supported by product {product}"
            )


def _suppress_out_of_space_mentions(
    result: EventAggregationResult, event_space: EventSpace
) -> tuple[EventAggregationResult, list[dict[str, object]]]:
    """Treat model mentions outside upstream routing as explicit ignores.

    Upstream products/topics define the event search space. A model may still
    notice a semantically plausible fragment that upstream did not classify;
    that fragment must not expand the taxonomy or abort valid mentions in the
    same message.
    """

    allowed = set(event_space.possible_families)
    suppressed: list[dict[str, object]] = []
    mentions: list[EventMentionDecision] = []
    for mention in result.mentions:
        if mention.action != "ignore" and mention.event_family not in allowed:
            suppressed.append(
                {
                    "mention_index": mention.mention_index,
                    "event_family": mention.event_family,
                    "product": mention.product,
                    "reason": "outside_upstream_event_space",
                    "evidence_excerpt": mention.evidence_excerpt,
                }
            )
            mentions.append(
                mention.model_copy(
                    update={
                        "action": "ignore",
                        "event_id": None,
                        "new_event": None,
                        "projection": None,
                        "relation": "mentions",
                        "materiality": "context_only",
                    }
                )
            )
        else:
            mentions.append(mention)
    return EventAggregationResult(mentions=mentions), suppressed


def _validate_repost_actions(item: NormalizedItem, result: EventAggregationResult) -> None:
    if item.content_form != "repost":
        return
    if any(mention.action == "create" for mention in result.mentions):
        raise ValueError("repost messages cannot create events")


def apply_membership_transaction(
    db: Session,
    *,
    item: NormalizedItem,
    result: EventAggregationResult,
    candidates: list[dict[str, Any]],
    additional_event_ids: set[int] | None = None,
) -> tuple[int, set[int]]:
    """Apply already-decided membership. The caller owns the surrounding transaction."""

    item = _lock_current_item_for_membership(db, item)
    _validate_repost_actions(item, result)
    resolved_products = _validate_candidate_references(
        result, candidates, item_products=item.products
    )
    _validate_product_family_compatibility(result, resolved_products)
    applied_count = 0
    affected_event_ids: set[int] = set()
    independence_group = _independence_group(item)

    for decision in result.mentions:
        if decision.action == "ignore":
            continue
        source_role = _verified_source_role(item, decision.source_role)
        claim_fingerprint = _fingerprint(
            {
                "family": decision.event_family,
                "product": resolved_products[decision.mention_index],
                "excerpt": decision.evidence_excerpt,
            }
        )
        if decision.action == "create":
            seed = decision.new_event
            if seed is None:  # Pydantic enforces this; keep type narrowing explicit.
                raise ValueError("create mention is missing new_event")
            if decision.event_family is None:
                raise ValueError("create mention is missing event_family")
            event, _created = create_event(
                db,
                normalized_item_id=item.id,
                mention_index=decision.mention_index,
                event_family=decision.event_family,
                products=[resolved_products[decision.mention_index]],
                canonical_anchors=seed.canonical_anchors,
                title=seed.title,
                current_summary=seed.summary,
                relation=decision.relation,
                source_role=source_role,
                materiality=decision.materiality,
                independence_group=independence_group,
                evidence_excerpt=decision.evidence_excerpt,
                content_fingerprint=claim_fingerprint,
                latest_development=seed.latest_development,
                key_facts=seed.key_facts,
                commit=False,
                use_savepoint=False,
            )
        else:
            if decision.event_family is None:
                raise ValueError("attach mention is missing event_family")
            event = db.get(Event, int(decision.event_id or 0))
            if event is None:
                raise ValueError(f"candidate event {decision.event_id} disappeared")
            proposal = decision.projection
            event, _added = add_event_mention(
                db,
                event_id=event.id,
                normalized_item_id=item.id,
                mention_index=decision.mention_index,
                relation=decision.relation,
                source_role=source_role,
                materiality=decision.materiality,
                independence_group=independence_group,
                evidence_excerpt=decision.evidence_excerpt,
                content_fingerprint=claim_fingerprint,
                title=proposal.title if proposal else None,
                current_summary=proposal.summary if proposal else None,
                latest_development=proposal.latest_development if proposal else None,
                key_facts=proposal.key_facts if proposal else None,
                commit=False,
                use_savepoint=False,
            )
        affected_event_ids.add(event.id)
        applied_count += 1

    refresh_event_metrics(db, affected_event_ids | (additional_event_ids or set()))
    return applied_count, affected_event_ids


async def aggregate_normalized_item(
    db: Session,
    item: NormalizedItem,
    *,
    llm_client: LLMClient | None = None,
) -> EventAggregationRun:
    if not is_latest_raw_item(db, item.raw_item):
        raise ValueError("event aggregation requires the latest RawItem revision")

    run = _record_for_item(db, item)
    current_revision = db.scalar(
        select(NormalizedItem.current_revision).where(NormalizedItem.id == item.id)
    )
    if current_revision != item.current_revision:
        if run is None:
            raise SupersededEventAggregationError(
                "event aggregation was requested for a superseded revision"
            )
        return _complete_superseded_run(db, run)
    if run is not None and run.status == "completed":
        return run
    if run is not None and run.status == "running":
        if not _is_stale_running_run(run):
            return run
        _reclaim_stale_run(run)
    reuse_draft = _decision_from_draft(run) if run is not None else None
    previous_model_call_count = run.model_call_count if run is not None else 0
    historical_event_ids = event_ids_for_normalized_item(db, item.id)
    if run is None:
        run = EventAggregationRun(
            normalized_item_id=item.id,
            normalized_item_revision=item.current_revision,
            status="running",
            current_stage="minimal_filter",
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
        refresh_event_metrics(db, historical_event_ids)
        db.commit()
    except IntegrityError:
        db.rollback()
        concurrent = _record_for_item(db, item)
        if concurrent is not None and concurrent.status in {"completed", "running"}:
            return concurrent
        raise RuntimeError("event aggregation is already running for this item") from None
    db.refresh(run)

    admission = minimal_event_filter(item)
    run.admission_decision = admission.decision
    if admission.decision == "skip":
        return _complete_without_call(
            db, run, outcome="skipped_by_minimal_filter", admission=admission
        )

    candidates = recall_event_candidates(
        db,
        item=item,
        possible_families=admission.event_space.possible_families,
        entity_hints=admission.entity_hints,
    ) if reuse_draft is None else list(run.candidate_snapshot or [])
    run.candidate_snapshot = candidates
    message, truncation = _message_payload(item)

    result = reuse_draft
    if result is None:
        run.current_stage = "model_decision"
        run.input_fingerprint = _fingerprint(
            {
                "message": message,
                "products": admission.event_space.products,
                "possible_event_families": admission.event_space.possible_families,
                "candidates": candidates,
            }
        )
        db.commit()

        client = llm_client or LLMClient()
        try:
            result = await client.aggregate_events(
                message=message,
                possible_event_families=list(admission.event_space.possible_families),
                candidates=candidates,
            )
            result, suppressed_mentions = _suppress_out_of_space_mentions(
                result, admission.event_space
            )
            metadata = execution_metadata(result)
            run.model_call_count = previous_model_call_count + int(metadata.get("retry_count") or 0) + 1
            recovery = (run.decision_draft or {}).get("recovery")
            run.decision_draft = {
                **result.model_dump(mode="json"),
                "suppressed_mentions": suppressed_mentions,
                "input_truncation": truncation,
                "execution_metadata": metadata,
                **({"recovery": recovery} if recovery else {}),
            }
            run.current_stage = "apply_membership"
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
    else:
        run.current_stage = "apply_membership"

    try:
        applied_count, _affected_event_ids = apply_membership_transaction(
            db,
            item=item,
            result=result,
            candidates=candidates,
            additional_event_ids=historical_event_ids,
        )
        run.status = "completed"
        run.outcome = "applied" if applied_count else "ignored"
        run.applied_at = datetime.now(UTC)
        run.completed_at = run.applied_at
        db.commit()
        db.refresh(run)
        return run
    except SupersededEventAggregationError:
        db.rollback()
        superseded = db.get(EventAggregationRun, run.id)
        if superseded is None:
            raise
        return _complete_superseded_run(db, superseded)
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


async def publish_normalized_item_downstream(
    db: Session, item: NormalizedItem
) -> EventAggregationRun | None:
    """Run the shared post-publication contract and require a terminal result."""

    if not settings.event_aggregation_enabled:
        return None
    run = await aggregate_normalized_item(db, item)
    if run.status != "completed":
        raise RuntimeError(
            "normalized item publication has no completed event aggregation result"
        )
    return run
