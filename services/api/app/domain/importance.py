from typing import Final

IMPORTANCE_POLICY_VERSION: Final = "importance-v1-five-dimensions"
DIMENSIONS: Final = (
    "impact_scope",
    "magnitude",
    "duration",
    "actionability",
    "novelty",
)
WEIGHTS: Final = {
    "impact_scope": 0.25,
    "magnitude": 0.25,
    "duration": 0.15,
    "actionability": 0.2,
    "novelty": 0.15,
}
TOPIC_RANGES: Final = {
    "patch": (0.60, 1.0),
    "champion": (0.60, 1.0),
    "game_mode": (0.45, 0.98),
    "esports": (0.20, 0.98),
    "roster": (0.20, 0.80),
    "skin": (0.20, 0.80),
    "activity": (0.15, 0.92),
    "community": (0.05, 0.50),
    "business": (0.10, 0.55),
    "service": (0.20, 1.0),
    "other": (0.05, 0.75),
}


def calculate_importance(
    dimensions: dict[str, dict[str, object]],
    *,
    primary_topic: str,
) -> tuple[float, dict[str, object]]:
    scores = {
        name: max(0, min(4, int(dimensions.get(name, {}).get("score", 0))))
        for name in DIMENSIONS
    }
    raw = sum((scores[name] / 4) * WEIGHTS[name] for name in DIMENSIONS)
    floor, cap = TOPIC_RANGES.get(primary_topic, TOPIC_RANGES["other"])
    final = round(max(floor, min(cap, raw)), 4)
    return final, {
        "policy_version": IMPORTANCE_POLICY_VERSION,
        "weights": WEIGHTS,
        "scores": scores,
        "raw_score": round(raw, 4),
        "topic_floor": floor,
        "topic_cap": cap,
        "final_score": final,
    }
