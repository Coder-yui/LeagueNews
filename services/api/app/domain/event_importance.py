from dataclasses import dataclass
from typing import Any, Final, Iterable

from app.domain.event_types import EventFamily, IMPORTANCE_POLICY_VERSION
from app.domain.importance import SCORE_BANDS


MAX_BREAKDOWN_EVIDENCE: Final = 10


EVENT_FAMILY_IMPORTANCE_PROFILES: Final[dict[EventFamily, frozenset[str]]] = {
    "gameplay_balance": frozenset(
        {
            "patch_official_notes",
            "patch_full_preview",
            "official_gameplay_preview",
            "gameplay_announcement",
            "game_announcement_general",
            "patch_hotfix",
            "promotion_gameplay",
            "leak_gameplay",
            "gameplay_guide",
            "game_discussion",
            "tft_announcement",
        }
    ),
    "gameplay_release": frozenset(
        {
            "patch_official_notes",
            "patch_full_preview",
            "official_gameplay_preview",
            "official_content_preview",
            "gameplay_announcement",
            "tft_announcement",
            "game_announcement_general",
            "promotion_gameplay",
            "leak_gameplay",
            "leak_content",
            "gameplay_guide",
            "game_discussion",
        }
    ),
    "cosmetic_release": frozenset(
        {
            "official_content_preview",
            "cosmetic_announcement",
            "promotion_cosmetic",
            "shop_cosmetic_rotation",
            "shop_rare_cosmetic",
            "activity_free_skin",
            "leak_content",
            "leak_general",
        }
    ),
    "player_activity": frozenset(
        {
            "official_content_preview",
            "activity_announcement",
            "promotion_activity",
            "promotion_cosmetic",
            "free_reward",
            "activity_free_skin",
            "leak_content",
            "leak_general",
        }
    ),
    "commercial_offer": frozenset(
        {
            "commerce_announcement",
            "promotion_general",
            "promotion_cosmetic",
            "shop_daily_standard",
            "shop_cosmetic_rotation",
            "shop_rare_cosmetic",
            "shop_bulk_refresh",
            "free_reward",
        }
    ),
    "service_incident": frozenset(
        {
            "patch_hotfix",
            "service_notice",
            "community_service_notice",
            "game_announcement_general",
        }
    ),
    "security_enforcement": frozenset(
        {"security_notice", "service_notice", "community_service_notice"}
    ),
    "esports_match": frozenset(
        {
            "esports_regular",
            "esports_playoffs",
            "esports_final",
            "worlds_regular",
            "worlds_key",
            "esports_rumor",
        }
    ),
    "esports_schedule": frozenset(
        {
            "esports_schedule",
            "esports_announcement_general",
            "esports_promotion",
            "esports_rumor",
        }
    ),
    "roster_change": frozenset(
        {"roster_announcement", "esports_rumor", "esports_announcement_general", "esports_analysis"}
    ),
    "esports_rules": frozenset(
        {
            "esports_announcement_general",
            "esports_analysis",
            "esports_discussion",
            "esports_promotion",
            "esports_rumor",
        }
    ),
    "universe_release": frozenset(
        {
            "official_content_preview",
            "universe_announcement",
            "universe_promotion",
            "universe_leak",
            "universe_discussion",
            "media_release",
        }
    ),
    "media_release": frozenset(
        {
            "official_content_preview",
            "media_release",
            "promotion_community",
            "promotion_general",
            "leak_content",
            "leak_general",
        }
    ),
    "corporate_change": frozenset(
        {
            "partnership",
            "riot_announcement",
            "riot_promotion",
            "riot_leak",
            "riot_discussion",
            "other_product_announcement",
            "other_product_promotion",
            "other_product_leak",
        }
    ),
    "platform_service": frozenset(
        {
            "service_notice",
            "community_service_notice",
            "patch_hotfix",
            "riot_announcement",
            "riot_discussion",
            "other_product_announcement",
        }
    ),
    "other_named_development": frozenset(SCORE_BANDS),
}


def is_importance_profile_compatible(event_family: str, profile: str) -> bool:
    """Return whether a controlled profile describes the given event family."""
    allowed = EVENT_FAMILY_IMPORTANCE_PROFILES.get(event_family)
    return allowed is not None and profile in allowed


@dataclass(frozen=True, slots=True)
class EventImportanceEvidence:
    normalized_item_id: int
    profile: str | None
    domain_score: object
    materiality: str
    normalized_item_revision: int = 1
    mention_index: int = 0


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
        elif item.profile not in SCORE_BANDS:
            reason = "missing_or_invalid_profile"
        else:
            contributions.append(
                {
                    "normalized_item_id": item.normalized_item_id,
                    "normalized_item_revision": item.normalized_item_revision,
                    "mention_index": item.mention_index,
                    "profile": item.profile,
                    "domain_score": round(float(item.domain_score), 4),
                    "materiality": item.materiality,
                }
            )
            continue
        ignored_reasons[reason] = ignored_reasons.get(reason, 0) + 1

    contributions.sort(
        key=lambda item: (
            -item["domain_score"],
            item["normalized_item_id"],
            item["normalized_item_revision"],
            item["mention_index"],
        )
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
