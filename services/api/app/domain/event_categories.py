from typing import Final, Literal


EventCategory = Literal["esports", "lol_pc", "tft", "other_products", "ecosystem"]

EVENT_CATEGORIES: Final[tuple[EventCategory, ...]] = (
    "esports",
    "lol_pc",
    "tft",
    "other_products",
    "ecosystem",
)

_ESPORTS_FAMILIES = frozenset(
    {"esports_match", "esports_schedule", "roster_change", "esports_rules"}
)
_ECOSYSTEM_FAMILIES = frozenset(
    {"corporate_change", "platform_service", "universe_release", "media_release"}
)


def event_category(*, event_family: str, products: list[str]) -> EventCategory:
    """Map the existing message taxonomy to the small public event filter set."""
    if event_family in _ESPORTS_FAMILIES or "lol_esports" in products:
        return "esports"
    if event_family in _ECOSYSTEM_FAMILIES or "riot_ecosystem" in products or "lol_universe" in products:
        return "ecosystem"
    if "lol_pc" in products:
        return "lol_pc"
    if "tft" in products:
        return "tft"
    if "other_lol_product" in products:
        return "other_products"
    return "other_products"
