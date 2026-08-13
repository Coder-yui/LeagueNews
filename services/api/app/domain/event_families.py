from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Final

from app.domain.event_types import EVENT_FAMILY_ORDER, EventFamily


# These mappings are taxonomy routing only. They never identify a concrete Event.
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
    "esports_analysis": ("esports_match", "roster_change", "esports_rules"),
    "esports_broadcast": ("media_release",),
    "esports_fandom_live": ("player_activity",),
    "lore_universe": ("universe_release",),
    "media_entertainment": ("media_release", "universe_release"),
    "merchandise_collectibles": ("commercial_offer", "media_release"),
    "corporate_partnerships": ("corporate_change",),
    "platform_services": ("platform_service",),
}

PRODUCT_FAMILY_MAP: Final[dict[str, frozenset[EventFamily]]] = {
    "lol_pc": frozenset(
        {
            "gameplay_balance",
            "gameplay_release",
            "cosmetic_release",
            "player_activity",
            "commercial_offer",
            "service_incident",
            "security_enforcement",
            "platform_service",
            "other_named_development",
        }
    ),
    "tft": frozenset(
        {
            "gameplay_balance",
            "gameplay_release",
            "cosmetic_release",
            "player_activity",
            "commercial_offer",
            "service_incident",
            "other_named_development",
        }
    ),
    "lol_esports": frozenset(
        {
            "esports_match",
            "esports_schedule",
            "roster_change",
            "esports_rules",
            "player_activity",
            "media_release",
            "other_named_development",
        }
    ),
    "lol_universe": frozenset(
        {"universe_release", "media_release", "player_activity", "other_named_development"}
    ),
    "other_lol_product": frozenset(
        {
            "gameplay_balance",
            "gameplay_release",
            "cosmetic_release",
            "player_activity",
            "commercial_offer",
            "service_incident",
            "universe_release",
            "media_release",
            "other_named_development",
        }
    ),
    "riot_ecosystem": frozenset(
        {
            "commercial_offer",
            "media_release",
            "corporate_change",
            "platform_service",
            "player_activity",
            "other_named_development",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class EventSpace:
    products: tuple[str, ...]
    possible_families: tuple[EventFamily, ...]


def possible_event_families(
    products: Iterable[str], topics: Iterable[str]
) -> tuple[EventFamily, ...]:
    """Derive a small taxonomy search space from upstream message semantics."""

    selected_products = {str(product) for product in products if str(product) != "unknown"}
    allowed = frozenset(
        family
        for product in selected_products
        for family in PRODUCT_FAMILY_MAP.get(product, ())
    )
    topic_families = {
        family for topic in topics for family in TOPIC_FAMILY_MAP.get(str(topic), ())
    }
    if not selected_products:
        return ("other_named_development",)
    routed = topic_families.intersection(allowed)
    if routed:
        return tuple(family for family in EVENT_FAMILY_ORDER if family in routed)
    return tuple(family for family in EVENT_FAMILY_ORDER if family in allowed) or (
        "other_named_development",
    )


def product_supports_family(product: str, family: EventFamily) -> bool:
    """Check the taxonomy compatibility of one event mention's product."""

    return family in PRODUCT_FAMILY_MAP.get(str(product), frozenset())

ANCHOR_KEY_BY_ENTITY_TYPE: Final = {
    "patch": "patch_version",
    "activity": "activity_name",
    "champion": "champion",
    "item": "item",
    "rune": "rune",
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


def anchors_from_entities(entities: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """Build generic descriptive/retrieval hints from already extracted entities."""

    collected: dict[str, list[str]] = {}
    for entity in entities:
        key = ANCHOR_KEY_BY_ENTITY_TYPE.get(str(entity.get("type") or ""))
        value = next(
            (
                str(entity.get(field)).strip()
                for field in ("canonical_id", "canonical_name", "display_name", "name")
                if str(entity.get(field) or "").strip()
            ),
            "",
        )
        if not key or not value:
            continue
        values = collected.setdefault(key, [])
        if value not in values:
            values.append(value)
    return {
        key: values[0] if len(values) == 1 else values
        for key, values in collected.items()
    }
