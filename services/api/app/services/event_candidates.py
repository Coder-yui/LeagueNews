import re
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.event_families import STRONG_ANCHOR_KEYS
from app.models.event import Event
from app.models.normalized_item import NormalizedItem


FAMILY_WINDOWS_DAYS: Final[dict[str, int]] = {
    "gameplay_balance": 45,
    "gameplay_release": 120,
    "cosmetic_release": 120,
    "player_activity": 90,
    "commercial_offer": 35,
    "service_incident": 14,
    "security_enforcement": 90,
    "esports_match": 14,
    "esports_schedule": 120,
    "roster_change": 180,
    "esports_rules": 180,
    "universe_release": 365,
    "media_release": 365,
    "corporate_change": 365,
    "platform_service": 180,
    "other_named_development": 90,
}

_WORD_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]", re.IGNORECASE)


def _tokens(value: str) -> set[str]:
    return set(_WORD_PATTERN.findall(value.casefold()))


def _values(value: Any) -> set[str]:
    values = value if isinstance(value, list) else [value]
    return {str(item).strip().casefold() for item in values if str(item).strip()}


def _anchor_score(
    message_anchors: dict[str, Any], event_anchors: dict[str, Any]
) -> tuple[int, list[str], bool]:
    score = 0
    reasons: list[str] = []
    conflict = False
    for key, message_value in message_anchors.items():
        if key not in event_anchors:
            continue
        message_values = _values(message_value)
        event_values = _values(event_anchors[key])
        if message_values & event_values:
            points = 60 if key in STRONG_ANCHOR_KEYS else 12
            score += points
            reasons.append(f"anchor:{key}")
        elif key in STRONG_ANCHOR_KEYS and message_values and event_values:
            conflict = True
            reasons.append(f"anchor_conflict:{key}")
    return score, reasons, conflict


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
    family_hints: list[str] | tuple[str, ...],
    anchors: dict[str, Any],
    per_family_limit: int = 5,
    total_limit: int = 12,
) -> list[dict[str, Any]]:
    if per_family_limit < 1 or total_limit < 1:
        raise ValueError("candidate limits must be positive")
    observed_at = _observed_at(item)
    oldest = observed_at - timedelta(days=max(FAMILY_WINDOWS_DAYS.values()))
    events = list(
        db.scalars(
            select(Event)
            .where(
                (Event.last_seen_at.is_(None) | (Event.last_seen_at >= oldest)),
            )
            .order_by(Event.last_seen_at.desc(), Event.id.desc())
            .limit(500)
        )
    )
    message_tokens = _tokens(f"{item.normalized_title} {item.summary}")
    product_set = set(item.products)
    ranked_by_family: dict[str, list[tuple[int, Event, list[str]]]] = {
        family: [] for family in family_hints
    }
    for event in events:
        if event.event_family not in ranked_by_family:
            continue
        anchor_points, reasons, conflict = _anchor_score(anchors, event.canonical_anchors)
        if conflict:
            continue
        score = 20 + anchor_points
        reasons.insert(0, "family")
        if product_set & set(event.products):
            score += 20
            reasons.append("product")
        event_tokens = _tokens(f"{event.title} {event.current_summary}")
        if message_tokens and event_tokens:
            similarity = len(message_tokens & event_tokens) / len(message_tokens | event_tokens)
            text_points = round(similarity * 20)
            score += text_points
            if text_points:
                reasons.append(f"text:{text_points}")
        window_days = FAMILY_WINDOWS_DAYS.get(event.event_family, 90)
        if abs((observed_at - _event_time(event)).total_seconds()) <= window_days * 86_400:
            score += 8
            reasons.append("time_window")
        if event.aggregation_key and any(
            str(value).casefold() in event.aggregation_key.casefold()
            for value in anchors.values()
            if isinstance(value, str) and value
        ):
            score += 40
            reasons.append("aggregation_key")
        ranked_by_family[event.event_family].append((score, event, reasons))

    selected: list[tuple[int, Event, list[str]]] = []
    for family in family_hints:
        ranked = sorted(ranked_by_family[family], key=lambda row: (-row[0], row[1].id))
        selected.extend(ranked[:per_family_limit])
    selected = sorted(selected, key=lambda row: (-row[0], row[1].id))[:total_limit]
    return [
        {
            "event_id": event.id,
            "event_family": event.event_family,
            "products": event.products,
            "canonical_anchors": event.canonical_anchors,
            "title": event.title,
            "current_summary": event.current_summary,
            "latest_development": event.latest_development,
            "key_facts": event.key_facts,
            "unresolved_points": event.unresolved_points,
            "lifecycle_status": event.lifecycle_status,
            "match_score": score,
            "match_reasons": reasons,
        }
        for score, event, reasons in selected
    ]
