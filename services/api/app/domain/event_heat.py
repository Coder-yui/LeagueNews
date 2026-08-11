import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from app.domain.event_types import HEAT_POLICY_VERSION


BASE_WEIGHTS: Final = {
    "original": 1.0,
    "quote_material": 0.7,
    "quote_context": 0.4,
    "repost": 0.25,
}
REPEAT_FACTORS: Final = (1.0, 0.5, 0.25)


@dataclass(frozen=True, slots=True)
class HeatEvidence:
    normalized_item_id: int
    source_id: int
    published_at: datetime
    content_form: str
    materiality: str
    content_fingerprint: str | None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _base_weight(value: HeatEvidence) -> float:
    if value.content_form == "repost":
        return BASE_WEIGHTS["repost"]
    if value.materiality == "duplicate":
        return 0.0
    if value.content_form == "quote":
        return (
            BASE_WEIGHTS["quote_material"]
            if value.materiality == "material_update"
            else BASE_WEIGHTS["quote_context"]
        )
    return BASE_WEIGHTS["original"]


def heat_level(score: float) -> str:
    if score < 0.15:
        return "cold"
    if score < 0.35:
        return "emerging"
    if score < 0.60:
        return "active"
    if score < 0.80:
        return "hot"
    return "surging"


def calculate_event_heat(
    evidence: list[HeatEvidence], *, as_of: datetime
) -> tuple[float, dict[str, Any]]:
    reference = _utc(as_of)
    newest_by_duplicate_key: dict[tuple[int, str], HeatEvidence] = {}
    unique_without_fingerprint: list[HeatEvidence] = []
    for value in evidence:
        if value.content_fingerprint:
            key = (value.source_id, value.content_fingerprint)
            existing = newest_by_duplicate_key.get(key)
            if existing is None or _utc(value.published_at) > _utc(existing.published_at):
                newest_by_duplicate_key[key] = value
        else:
            unique_without_fingerprint.append(value)
    deduplicated = [*newest_by_duplicate_key.values(), *unique_without_fingerprint]
    by_message = {value.normalized_item_id: value for value in deduplicated}
    deduplicated = list(by_message.values())

    window_start = reference - timedelta(days=7)
    eligible = [
        value for value in deduplicated if _utc(value.published_at) >= window_start
    ]
    source_history: dict[int, list[datetime]] = {}
    heat_raw = 0.0
    contributions: list[dict[str, Any]] = []
    for value in sorted(eligible, key=lambda row: (_utc(row.published_at), row.normalized_item_id)):
        published_at = _utc(value.published_at)
        history = [
            timestamp
            for timestamp in source_history.get(value.source_id, [])
            if published_at - timestamp <= timedelta(hours=6)
        ]
        repeat_index = len(history)
        repeat_factor = (
            REPEAT_FACTORS[repeat_index] if repeat_index < len(REPEAT_FACTORS) else 0.1
        )
        history.append(published_at)
        source_history[value.source_id] = history
        age_hours = max(0.0, (reference - published_at).total_seconds() / 3600)
        decay = 2 ** (-age_hours / 12)
        base = _base_weight(value)
        contribution = base * decay * repeat_factor
        heat_raw += contribution
        contributions.append(
            {
                "normalized_item_id": value.normalized_item_id,
                "base_weight": base,
                "time_decay": round(decay, 6),
                "source_repeat_factor": repeat_factor,
                "contribution": round(contribution, 6),
            }
        )
    score = round(1 - math.exp(-heat_raw / 6), 6)
    recent = [
        value
        for value in deduplicated
        if reference - timedelta(hours=24) <= _utc(value.published_at) <= reference
    ]
    return score, {
        "policy_version": HEAT_POLICY_VERSION,
        "heat_raw": round(heat_raw, 6),
        "level": heat_level(score),
        "message_count_total": len(deduplicated),
        "message_count_24h": len({value.normalized_item_id for value in recent}),
        "unique_sources_24h": len({value.source_id for value in recent}),
        "deduplicated_count": len(evidence) - len(deduplicated),
        "contributions": contributions,
    }
