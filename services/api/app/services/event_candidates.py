import re
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.domain.event_families import product_supports_family
from app.domain.esports_match_identity import (
    esports_match_has_subject,
    esports_match_identity_conflict,
    match_identity_from_anchors,
    match_identity_from_message_entities,
)
from app.models.event import Event
from app.models.normalized_item import NormalizedItem
from app.services.event_semantics import semantic_projection


RECALL_WINDOW_DAYS: Final = 365
ESPORTS_MATCH_RECALL_WINDOW_DAYS: Final = 7
_WORD_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.IGNORECASE)


def _is_recallable_event(event: Event) -> bool:
    """An esports_match needs a valid title and a complete match subject.

    A shell Event (empty title, or without exactly two participants) cannot identify
    what match it is, so it must never enter the candidate pool as an attach target;
    repair/rebuild deletes such invalid Events instead of leaving them behind.
    """
    if event.event_family != "esports_match":
        return True
    if not (event.title or "").strip():
        return False
    return esports_match_has_subject(event.canonical_anchors or {})


def _recall_window_for(family: str) -> int:
    """The candidate search boundary is family aware: 7 days for esports_match.

    This is a recall boundary only, never a match identity rule. Two matches
    inside the window can still be distinct Events when they are clearly
    different occurrences.
    """
    return (
        ESPORTS_MATCH_RECALL_WINDOW_DAYS
        if family == "esports_match"
        else RECALL_WINDOW_DAYS
    )


def _tokens(value: str) -> set[str]:
    return set(_WORD_PATTERN.findall(value.casefold()))


def _text_values(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {
            text
            for nested in value.values()
            for text in _text_values(nested)
        }
    if isinstance(value, list):
        return {text for nested in value for text in _text_values(nested)}
    text = str(value or "").strip().casefold()
    return {text} if text else set()


def _observed_at(item: NormalizedItem) -> datetime:
    value = item.raw_item.published_at or item.raw_item.ingested_at or datetime.now(UTC)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _event_time(event: Event) -> datetime:
    value = event.last_seen_at or event.updated_at or event.created_at or datetime.now(UTC)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def recall_event_candidates(
    db: Session,
    *,
    item: NormalizedItem,
    possible_families: list[str] | tuple[str, ...],
    entity_hints: dict[str, Any] | None = None,
    total_limit: int = 12,
) -> list[dict[str, Any]]:
    """Recall a bounded candidate set from upstream product/topic/entity semantics."""

    if total_limit < 1:
        raise ValueError("candidate limit must be positive")
    observed_at = _observed_at(item)
    families = [str(family) for family in (possible_families or [])]
    if families:
        # Filter by routed family *in SQL*, before the candidate limit, so unrelated
        # families cannot consume the bounded budget and starve the relevant family.
        # Each family keeps its own recall window (7 days for esports_match).
        family_windows = [
            (Event.event_family == family)
            & (
                Event.last_seen_at.is_(None)
                | (
                    Event.last_seen_at
                    >= observed_at - timedelta(days=_recall_window_for(family))
                )
            )
            for family in families
        ]
        where_clauses = or_(*family_windows)
    else:
        oldest_esports = observed_at - timedelta(days=ESPORTS_MATCH_RECALL_WINDOW_DAYS)
        oldest_general = observed_at - timedelta(days=RECALL_WINDOW_DAYS)
        where_clauses = or_(
            (
                (Event.event_family == "esports_match")
                & (
                    Event.last_seen_at.is_(None)
                    | (Event.last_seen_at >= oldest_esports)
                )
            ),
            (
                (Event.event_family != "esports_match")
                & (
                    Event.last_seen_at.is_(None)
                    | (Event.last_seen_at >= oldest_general)
                )
            ),
        )
    events = list(
        db.scalars(
            select(Event)
            .where(where_clauses)
            .order_by(Event.last_seen_at.desc(), Event.id.desc())
            .limit(500)
        )
    )
    semantic_title, semantic_text = semantic_projection(item)
    message_tokens = _tokens(
        " ".join(value for value in (semantic_title, item.summary, semantic_text[:4000]) if value)
    )
    message_entities = _text_values(entity_hints or {})
    hinted_families = set(possible_families)
    message_products = {str(product) for product in item.products}
    ranked: list[tuple[float, Event, list[str]]] = []

    # esports_match identity gate — it MUST run before the final ranking/limit, never
    # after: a structurally conflicting candidate that is dropped only after the top-N
    # truncation has already consumed a slot and silently evicted the true candidate
    # (ranked 13th behind 12 conflicting ones), forcing a false split. The incoming
    # signal is the conservative high-confidence participants extracted from the
    # message's own team entities; other families never pass through this gate.
    incoming_match_identity: dict[str, Any] | None = None

    for event in events:
        if not _is_recallable_event(event):
            continue
        if event.event_family == "esports_match":
            if incoming_match_identity is None:
                incoming_match_identity = match_identity_from_message_entities(
                    item.entities
                )
            if esports_match_identity_conflict(
                match_identity_from_anchors(event.canonical_anchors or {}),
                incoming_match_identity,
            ):
                continue
        event_products = {str(product) for product in event.products}
        concrete_message_products = message_products - {"unknown"}
        if concrete_message_products:
            # Event membership is product-isolated. A cross-product message
            # may recall single-product events in either domain, but legacy or
            # manually-created multi-product Events are not attach candidates.
            if len(event_products) != 1 or not event_products.issubset(
                concrete_message_products
            ):
                continue
        if hinted_families and event.event_family not in hinted_families:
            continue
        if len(event_products) == 1:
            event_product = next(iter(event_products))
            if event_product != "unknown" and not product_supports_family(
                event_product, event.event_family  # type: ignore[arg-type]
            ):
                continue
        score = 0.0
        reasons: list[str] = []
        if event.event_family in hinted_families:
            score += 30
            reasons.append("family_hint")
        product_overlap = message_products.intersection(event_products)
        if product_overlap:
            score += 20
            reasons.append("product_overlap")
        event_entities = _text_values(event.canonical_anchors)
        entity_overlap = message_entities.intersection(event_entities)
        if entity_overlap:
            score += min(30, 10 * len(entity_overlap))
            reasons.append("entity_overlap")
        event_tokens = _tokens(f"{event.title} {event.current_summary}")
        if message_tokens and event_tokens:
            similarity = len(message_tokens & event_tokens) / len(message_tokens | event_tokens)
            if similarity:
                score += similarity * 30
                reasons.append("text_overlap")
        age_days = abs((observed_at - _event_time(event)).total_seconds()) / 86_400
        window = _recall_window_for(str(event.event_family))
        score += max(0.0, 20 * (1 - age_days / window))
        reasons.append("recent_activity")
        ranked.append((score, event, reasons))

    selected = sorted(ranked, key=lambda row: (-row[0], -row[1].id))
    if len(families) > 1 and "esports_match" in families:
        # Family-fair truncation for esports_match routing. A routed sibling family
        # with roster-style anchors (an esports_schedule roundup lists every team in
        # its anchors) outscores concrete match Events on entity overlap and fills
        # the whole budget, starving the esports_match attach targets the LLM needs
        # — the same top-N eviction the pre-gate ordering caused, now across
        # families. esports_match keeps a protected share of the budget; sibling
        # families fill the remaining slots with their original ranking. Pure
        # single-family routing and every non-esports_match routing keep the
        # original global truncation.
        match_rows = [row for row in selected if row[1].event_family == "esports_match"]
        other_rows = [row for row in selected if row[1].event_family != "esports_match"]
        match_quota = max(1, total_limit // 2)
        selected = sorted(
            match_rows[:match_quota]
            + other_rows[: max(0, total_limit - min(len(match_rows), match_quota))],
            key=lambda row: (-row[0], -row[1].id),
        )
    else:
        selected = selected[:total_limit]
    return [
        {
            "event_id": event.id,
            "event_family": event.event_family,
            "products": event.products,
            "canonical_anchors": event.canonical_anchors,
            "title": event.title,
            "current_summary": event.current_summary,
            "latest_development": event.latest_development,
            "key_facts": event.key_facts[:12],
            "lifecycle_status": event.lifecycle_status,
            "last_seen_at": event.last_seen_at.isoformat() if event.last_seen_at else None,
            "recall_score": round(score, 4),
            "recall_reasons": reasons,
        }
        for score, event, reasons in selected
    ]
