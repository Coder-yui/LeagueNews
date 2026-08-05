import re
from typing import Final

IMPORTANCE_POLICY_VERSION: Final = "importance-v2-five-dimensions"
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
    "roster": (0.20, 0.60),
    "skin": (0.20, 0.80),
    "activity": (0.15, 0.92),
    "community": (0.05, 0.50),
    "business": (0.10, 0.55),
    "service": (0.20, 1.0),
    "other": (0.05, 0.75),
}
_REDEMPTION_CODE = re.compile(r"\b[A-Z0-9]{2,}(?:-[A-Z0-9]{2,}){2,}\b")


def apply_actionability_signals(
    dimensions: dict[str, dict[str, object]],
    *,
    content: str,
) -> dict[str, dict[str, object]]:
    normalized = {
        name: dict(dimensions.get(name, {}))
        for name in DIMENSIONS
    }
    if "兑换码" in content or _REDEMPTION_CODE.search(content.upper()):
        actionability = normalized["actionability"]
        actionability["score"] = 4
        actionability["evidence"] = (
            f"{actionability.get('evidence', '')} "
            "消息包含可立即使用的兑换码，程序规则将行动紧迫性校准为4。"
        ).strip()
    return normalized


def calculate_importance(
    dimensions: dict[str, dict[str, object]],
    *,
    primary_topic: str,
    content: str = "",
) -> tuple[float, dict[str, object]]:
    dimensions = apply_actionability_signals(dimensions, content=content)
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
