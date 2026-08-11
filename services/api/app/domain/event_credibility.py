from dataclasses import dataclass
from typing import Any, Final

from app.domain.event_types import CREDIBILITY_POLICY_VERSION


POSITIVE_RELATIONS: Final = frozenset({"reports", "supports", "confirms", "corrects"})
ROLE_STRENGTH: Final = {
    "responsible_official": lambda _reliability: 100.0,
    "direct_subject": lambda _reliability: 85.0,
    "first_party_participant": lambda _reliability: 80.0,
    "independent_media": lambda reliability: 35.0 + 55.0 * reliability,
    "known_leaker": lambda reliability: 25.0 + 55.0 * reliability,
    "ordinary_account": lambda reliability: 10.0 + 50.0 * reliability,
    "republisher": lambda _reliability: 0.0,
    "unknown": lambda _reliability: 10.0,
}


@dataclass(frozen=True, slots=True)
class CredibilityEvidence:
    relation: str
    source_role: str
    independence_group: str | None
    reliability: float


def _strength(evidence: CredibilityEvidence) -> float:
    calculator = ROLE_STRENGTH.get(evidence.source_role)
    if calculator is None:
        raise ValueError(f"unsupported source role: {evidence.source_role}")
    return min(100.0, max(0.0, calculator(min(1.0, max(0.0, evidence.reliability)))))


def calculate_event_credibility(
    evidence: list[CredibilityEvidence],
) -> tuple[float, str, dict[str, Any]]:
    positive: dict[str, float] = {}
    negative: dict[str, float] = {}
    official_positive: set[str] = set()
    authoritative_denials: set[str] = set()
    for value in evidence:
        if not value.independence_group or value.relation == "mentions":
            continue
        strength = _strength(value)
        if value.relation == "denies":
            negative[value.independence_group] = max(
                negative.get(value.independence_group, 0), strength
            )
            if value.source_role in {"responsible_official", "direct_subject"}:
                authoritative_denials.add(value.independence_group)
        elif value.relation in POSITIVE_RELATIONS and strength > 0:
            positive[value.independence_group] = max(
                positive.get(value.independence_group, 0), strength
            )
            if value.source_role == "responsible_official":
                official_positive.add(value.independence_group)

    strongest_positive = max(positive.values(), default=0.0)
    strongest_negative = max(negative.values(), default=0.0)
    strong_conflict = strongest_positive >= 70 and strongest_negative >= 70
    if (official_positive and authoritative_denials) or strong_conflict:
        score_points = 50.0
        level = "disputed"
    elif authoritative_denials:
        score_points = 0.0
        level = "denied"
    elif official_positive:
        score_points = 100.0
        level = "officially_confirmed"
    else:
        corroboration = 8 * min(max(len(positive) - 1, 0), 3)
        conflict_penalty = 15 * min(len(negative), 2)
        score_points = min(
            95.0,
            max(0.0, strongest_positive + corroboration - conflict_penalty),
        )
        if len(positive) >= 2 and score_points >= 70:
            level = "corroborated"
        elif score_points >= 40:
            level = "plausible"
        else:
            level = "unverified"
    score = round(score_points / 100, 6)
    return score, level, {
        "policy_version": CREDIBILITY_POLICY_VERSION,
        "strongest_positive": round(strongest_positive, 4),
        "strongest_negative": round(strongest_negative, 4),
        "corroboration_groups": len(positive),
        "conflicting_groups": len(negative),
        "official_positive_groups": len(official_positive),
        "authoritative_denial_groups": len(authoritative_denials),
        "official_groups": len(official_positive | authoritative_denials),
        "independent_groups": len(set(positive) | set(negative)),
        "score_points": round(score_points, 4),
        "level": level,
    }
