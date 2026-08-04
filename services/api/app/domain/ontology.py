from typing import Final

ONTOLOGY_VERSION: Final = "lol-news-v1"
PRIMARY_TOPICS: Final = {
    "patch",
    "esports",
    "roster",
    "champion",
    "skin",
    "activity",
    "game_mode",
    "service",
    "community",
    "business",
    "other",
}
ENTITY_TYPES: Final = {
    "champion",
    "skin",
    "item",
    "rune",
    "patch",
    "game_mode",
    "team",
    "player",
    "coach",
    "tournament",
    "league",
    "activity",
    "region",
    "organization",
    "product",
    "other",
    "unknown",
}


def topic_from_category(category: str) -> str:
    value = category.casefold()
    mappings = (
        (("版本", "patch", "平衡"), "patch"),
        (("赛事", "赛程", "赛果", "比赛"), "esports"),
        (("转会", "阵容", "退役"), "roster"),
        (("英雄", "champion"), "champion"),
        (("皮肤", "skin"), "skin"),
        (("活动", "商城"), "activity"),
        (("模式", "玩法"), "game_mode"),
        (("故障", "安全", "服务"), "service"),
        (("社区", "互动"), "community"),
        (("商业", "周边", "合作"), "business"),
    )
    return next((topic for words, topic in mappings if any(word in value for word in words)), "other")


def normalize_entities(values: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for value in values:
        entity = dict(value)
        entity_type = str(entity.get("type") or "unknown").casefold()
        entity["type"] = entity_type if entity_type in ENTITY_TYPES else "other"
        entity["display_name"] = str(
            entity.get("display_name") or entity.get("name") or ""
        )
        entity["canonical_name"] = str(
            entity.get("canonical_name") or entity["display_name"]
        )
        entity["canonical_id"] = entity.get("canonical_id")
        normalized.append(entity)
    return normalized
