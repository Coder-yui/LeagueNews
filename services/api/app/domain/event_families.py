import re
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
        "product",
    }
)

_MYTHIC_SHOP_VALUES = frozenset({"mythic_shop", "mythicshop", "神话商店"})
_MYTHIC_SHOP_KEYS = frozenset({"shop", "store", "system", "service", "rotation_type"})
_MARKET_KEYS = frozenset({"market", "region", "region_scope", "server"})
_PERIOD_KEYS = frozenset(
    {"rotation", "rotation_period", "rotation_window", "period", "window"}
)


def _anchor_strings(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [str(item).strip() for item in values if str(item).strip()]


def _first_anchor_value(anchors: dict[str, Any], keys: frozenset[str]) -> str | None:
    for key, value in anchors.items():
        if key.casefold() in keys:
            values = _anchor_strings(value)
            if values:
                return values[0]
    return None


def _normalize_market(value: str) -> str:
    lowered = value.casefold().strip()
    if lowered in {"中国", "中国大陆", "国服", "cn server", "china", "cn"}:
        return "cn"
    if lowered in {"海外", "外服", "国际服", "海外服", "overseas", "international"}:
        return "overseas"
    return re.sub(r"\s+", "_", lowered)


def _normalize_period(value: str) -> str:
    lowered = re.sub(r"\s+", " ", value.casefold().strip())
    weekly = re.fullmatch(
        r"(\d{4})\s*(?:(?:年|year)\s*)?(?:(?:第)?\s*(\d{1,2})\s*周|week\s*(\d{1,2}))",
        lowered,
    )
    if weekly:
        week = weekly.group(2) or weekly.group(3)
        return f"{weekly.group(1)}-w{int(week):02d}"
    return lowered.replace(" ", "-").replace("~", "/")


def is_mythic_shop_event(event_family: str, anchors: dict[str, Any]) -> bool:
    if event_family != "commercial_offer":
        return False
    return any(
        str(value).casefold().replace(" ", "_") in _MYTHIC_SHOP_VALUES
        for key, raw_value in anchors.items()
        if key.casefold() in _MYTHIC_SHOP_KEYS
        for value in _anchor_strings(raw_value)
    )


def canonicalize_event_anchors(event_family: str, anchors: dict[str, Any]) -> dict[str, Any]:
    """Keep recurring mythic-shop identity independent from the listed products."""
    copied = dict(anchors)
    if not is_mythic_shop_event(event_family, copied):
        return copied
    market = _first_anchor_value(copied, _MARKET_KEYS)
    period = _first_anchor_value(copied, _PERIOD_KEYS)
    normalized = {"shop": "mythic_shop"}
    if market:
        normalized["market"] = _normalize_market(market)
    if period:
        normalized["rotation_period"] = _normalize_period(period)
    return normalized


def has_complete_mythic_shop_identity(event_family: str, anchors: dict[str, Any]) -> bool:
    normalized = canonicalize_event_anchors(event_family, anchors)
    return is_mythic_shop_event(event_family, normalized) and all(
        isinstance(normalized.get(key), str) and bool(normalized[key])
        for key in ("shop", "market", "rotation_period")
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
