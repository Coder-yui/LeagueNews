from typing import Any, Final

from app.domain.event_types import IMPORTANCE_POLICY_VERSION


FAMILY_BASE: Final = {
    "gameplay_balance": 18,
    "gameplay_release": 25,
    "cosmetic_release": 10,
    "player_activity": 10,
    "commercial_offer": 8,
    "service_incident": 20,
    "security_enforcement": 22,
    "esports_match": 8,
    "esports_schedule": 10,
    "roster_change": 15,
    "esports_rules": 20,
    "universe_release": 15,
    "media_release": 15,
    "corporate_change": 25,
    "platform_service": 22,
    "other_named_development": 10,
}
SCOPE_POINTS: Final = {
    "individual": 0,
    "group": 8,
    "product_segment": 14,
    "product_wide": 22,
    "ecosystem": 28,
}
MAGNITUDE_POINTS: Final = {"minor": 0, "moderate": 10, "major": 20, "transformative": 30}
DURATION_POINTS: Final = {
    "transient": 0,
    "short_term": 4,
    "cycle_or_season": 10,
    "long_term": 16,
}
URGENCY_POINTS: Final = {"none": 0, "timely": 4, "immediate": 8}


def importance_level(score: float) -> str:
    if score < 0.30:
        return "low"
    if score < 0.55:
        return "medium"
    if score < 0.80:
        return "high"
    return "critical"


def calculate_event_importance(
    *, event_family: str, impact: dict[str, Any]
) -> tuple[float, dict[str, Any]]:
    try:
        components = {
            "event_family_base": FAMILY_BASE[event_family],
            "scope": SCOPE_POINTS[str(impact["scope"])],
            "magnitude": MAGNITUDE_POINTS[str(impact["magnitude"])],
            "duration": DURATION_POINTS[str(impact["duration"])],
            "urgency": URGENCY_POINTS[str(impact["urgency"])],
        }
    except KeyError as exc:
        raise ValueError(f"unsupported event importance input: {exc.args[0]}") from exc
    raw_points = sum(components.values())
    points = min(100, max(0, raw_points))
    score = round(points / 100, 6)
    return score, {
        "policy_version": IMPORTANCE_POLICY_VERSION,
        "impact": dict(impact),
        "components": components,
        "raw_points": raw_points,
        "capped_points": points,
        "level": importance_level(score),
    }
