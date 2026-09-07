"""Pure candidate ranking over a frozen event pool."""

import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from app.domain.event_families import product_supports_family

_WORD_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.IGNORECASE)


def _tokens(value: str) -> set[str]:
    return set(_WORD_PATTERN.findall(value.casefold()))


def _text_values(value: Any) -> set[str]:
    if isinstance(value, dict):
        return {text for nested in value.values() for text in _text_values(nested)}
    if isinstance(value, list):
        return {text for nested in value for text in _text_values(nested)}
    text = str(value or "").strip().casefold()
    return {text} if text else set()


def _event_time(event: Any) -> datetime:
    value = event.last_seen_at or event.updated_at or event.created_at or datetime.now(UTC)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def rank_event_candidates(
    *, message, candidates, possible_families=(), entity_hints=None, total_limit=12, window_days=60
):
    if not 1 <= total_limit <= 24 or window_days <= 0:
        raise ValueError("invalid recall limits")
    observed_at = datetime.fromisoformat(message["published_at"])
    observed_at = observed_at.replace(tzinfo=UTC) if observed_at.tzinfo is None else observed_at
    events = []
    for row in candidates:
        timestamp = (
            datetime.fromisoformat(row["last_seen_at"]) if row.get("last_seen_at") else observed_at
        )
        timestamp = timestamp.replace(tzinfo=UTC) if timestamp.tzinfo is None else timestamp
        if abs((observed_at - timestamp).total_seconds()) > window_days * 86400:
            continue
        events.append(
            SimpleNamespace(
                id=int(row["event_id"]),
                event_family=row["event_family"],
                products=row.get("products", []),
                canonical_anchors=row.get("canonical_anchors", {}),
                title=row.get("title", ""),
                current_summary=row.get("current_summary", row.get("summary", "")),
                latest_development=row.get("latest_development", ""),
                key_facts=row.get("key_facts", []),
                lifecycle_status=row.get("lifecycle_status", "developing"),
                last_seen_at=timestamp,
            )
        )
    semantic_title, semantic_text = str(message.get("title", "")), str(message.get("content", ""))
    message_tokens = _tokens(
        " ".join(
            value
            for value in (semantic_title, str(message.get("summary", "")), semantic_text[:4000])
            if value
        )
    )
    message_entities = _text_values(entity_hints or {})
    hinted_families = set(possible_families)
    message_products = {str(product) for product in message.get("products", [])}
    ranked: list[tuple[float, Any, list[str]]] = []

    for event in events:
        event_products = {str(product) for product in event.products}
        concrete_message_products = message_products - {"unknown"}
        if concrete_message_products:
            # Event membership is product-isolated. A cross-product message
            # may recall single-product events in either domain, but legacy or
            # manually-created multi-product Events are not attach candidates.
            if len(event_products) != 1 or not event_products.issubset(concrete_message_products):
                continue
        if hinted_families and event.event_family not in hinted_families:
            continue
        if len(event_products) == 1:
            event_product = next(iter(event_products))
            if event_product != "unknown" and not product_supports_family(
                event_product,
                event.event_family,  # type: ignore[arg-type]
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
        score += max(0.0, 20 * (1 - age_days / window_days))
        reasons.append("recent_activity")
        ranked.append((score, event, reasons))

    selected = sorted(ranked, key=lambda row: (-row[0], -row[1].id))[:total_limit]
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
