from dataclasses import dataclass
from typing import Any, Final, Iterable

from app.domain.event_types import IMPORTANCE_POLICY_VERSION


MAX_BREAKDOWN_EVIDENCE: Final = 10


@dataclass(frozen=True, slots=True)
class EventImportanceEvidence:
    normalized_item_id: int
    profile: str | None
    domain_score: object
    materiality: str


def importance_level(score: float) -> str:
    if score < 0.30:
        return "low"
    if score < 0.55:
        return "medium"
    if score < 0.80:
        return "high"
    return "critical"


def calculate_event_importance(
    evidence: Iterable[EventImportanceEvidence],
) -> tuple[float, dict[str, Any]]:
    """Aggregate an event's strongest valid material domain evidence."""
    contributions: list[dict[str, Any]] = []
    ignored_reasons: dict[str, int] = {}
    for item in evidence:
        if item.materiality != "material_update":
            reason = "non_material"
        elif not isinstance(item.domain_score, (int, float)) or isinstance(
            item.domain_score, bool
        ):
            reason = "missing_or_invalid_domain_score"
        elif not 0 <= float(item.domain_score) <= 1:
            reason = "domain_score_out_of_range"
        elif not isinstance(item.profile, str) or not item.profile:
            reason = "missing_or_invalid_profile"
        else:
            contributions.append(
                {
                    "normalized_item_id": item.normalized_item_id,
                    "profile": item.profile,
                    "domain_score": round(float(item.domain_score), 4),
                    "materiality": item.materiality,
                }
            )
            continue
        ignored_reasons[reason] = ignored_reasons.get(reason, 0) + 1

    contributions.sort(
        key=lambda item: (-item["domain_score"], item["normalized_item_id"])
    )
    dominant = contributions[0] if contributions else None
    score = float(dominant["domain_score"]) if dominant else 0.0
    return score, {
        "policy_version": IMPORTANCE_POLICY_VERSION,
        "method": "max_material_domain_score",
        "score": score,
        "level": importance_level(score),
        "dominant_profile": dominant["profile"] if dominant else None,
        "dominant_normalized_item_id": (
            dominant["normalized_item_id"] if dominant else None
        ),
        "contribution_count": len(contributions),
        "contributing_evidence": contributions[:MAX_BREAKDOWN_EVIDENCE],
        "ignored_evidence_count": sum(ignored_reasons.values()),
        "ignored_evidence_reasons": ignored_reasons,
    }
