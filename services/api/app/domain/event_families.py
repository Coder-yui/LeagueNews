from collections.abc import Iterable
from typing import Any, Final

from app.domain.event_types import EventFamily


TOPIC_FAMILY_MAP: Final[dict[str, tuple[EventFamily, ...]]] = {
    "balance_gameplay": ("gameplay_balance",),
    "champions": ("gameplay_release", "gameplay_balance"),
    "items_runes_systems": ("gameplay_balance",),
    "game_modes": ("gameplay_release",),
    "gameplay": ("gameplay_balance", "gameplay_release"),
    "service_technical": ("service_incident", "platform_service"),
    "cosmetics": ("cosmetic_release",),
    "shop_monetization": ("commercial_offer",),
    "activities_rewards": ("player_activity",),
    "security_fair_play": ("security_enforcement",),
    "tft_gameplay": ("gameplay_balance", "gameplay_release"),
    "esports_competition": ("esports_rules", "esports_match"),
    "esports_schedule": ("esports_schedule",),
    "esports_matches": ("esports_match",),
    "esports_rosters": ("roster_change",),
    "lore_universe": ("universe_release",),
    "media_entertainment": ("media_release", "universe_release"),
    "corporate_partnerships": ("corporate_change",),
    "platform_services": ("platform_service",),
}

ANCHOR_KEY_BY_ENTITY_TYPE: Final = {
    "patch": "patch_version",
    "activity": "activity_name",
    "champion": "champion",
    "skin": "skin",
    "game_mode": "game_mode",
    "team": "team",
    "player": "player",
    "coach": "player",
    "tournament": "tournament",
    "league": "league",
    "region": "region",
    "organization": "organization",
    "product": "product",
    "system": "system",
}

STRONG_ANCHOR_KEYS: Final = frozenset(
    {
        "patch_version",
        "activity_name",
        "release_name",
        "skin_series",
        "match",
        "team",
        "player",
        "tournament",
        "league",
        "game_mode",
        "champion",
    }
)


def family_hints(topics: Iterable[str]) -> list[EventFamily]:
    result: list[EventFamily] = []
    for topic in topics:
        for family in TOPIC_FAMILY_MAP.get(topic, ()):
            if family not in result:
                result.append(family)
    return result or ["other_named_development"]


def anchors_from_entities(entities: Iterable[dict[str, Any]]) -> dict[str, Any]:
    collected: dict[str, list[str]] = {}
    for entity in entities:
        key = ANCHOR_KEY_BY_ENTITY_TYPE.get(str(entity.get("type") or ""))
        value = str(
            entity.get("canonical_id")
            or entity.get("canonical_name")
            or entity.get("display_name")
            or entity.get("name")
            or ""
        ).strip()
        if not key or not value:
            continue
        collected.setdefault(key, [])
        if value not in collected[key]:
            collected[key].append(value)
    return {
        key: values[0] if len(values) == 1 else values
        for key, values in collected.items()
    }


def has_strong_anchor(anchors: dict[str, Any]) -> bool:
    return any(key in STRONG_ANCHOR_KEYS and value for key, value in anchors.items())
