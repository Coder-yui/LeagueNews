import re
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

ONTOLOGY_VERSION: Final = "lol-news-v1"
CONTENT_TYPES: Final = {
    "official_fact",
    "official_notice",
    "match_result",
    "insider_rumor",
    "insider_confirmed",
    "data_mine",
    "aggregation",
    "community_noise",
}
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
EVENT_TYPES: Final = {
    "transfer_saga",
    "patch_cycle",
    "release_saga",
    "shop_rotation",
    "daily_matches",
    "tft_patch",
    "sr_patch",
    "major_match",
    "major_gameplay_change",
    "dev_preview",
    "incident",
    "activity",
    "qualification_saga",
    "other",
}
TIMELINE_EVENT_TYPES: Final = {
    "transfer_saga",
    "patch_cycle",
    "release_saga",
    "dev_preview",
    "incident",
    "qualification_saga",
}
RECURRING_EVENT_TYPES: Final = {
    "shop_rotation",
    "daily_matches",
    "tft_patch",
    "sr_patch",
}
SINGLETON_EVENT_TYPES: Final = {
    "major_match",
    "major_gameplay_change",
    "activity",
}
_PATCH_PATTERN = re.compile(r"(?<!\d)(\d{1,2}\.\d{1,2})(?!\d)")
_MAJOR_MATCH_TERMS = (
    "总决赛",
    "世界赛",
    "全球总决赛",
    "worlds",
    "决赛",
)


@dataclass(frozen=True, slots=True)
class EventRoute:
    event_type: str
    aggregation_key: str
    membership_role: str = "primary"


def _entity_names(
    entities: list[dict[str, object]],
    *,
    types: set[str] | None = None,
    roles: set[str] | None = None,
) -> list[str]:
    values = []
    for entity in entities:
        entity_type = str(entity.get("type") or "").casefold()
        role = str(entity.get("role") or "context").casefold()
        if types is not None and entity_type not in types:
            continue
        if roles is not None and role not in roles:
            continue
        value = str(
            entity.get("canonical_name")
            or entity.get("canonical_id")
            or entity.get("name")
            or ""
        ).strip()
        if value:
            values.append(value)
    return values


def route_event_types(
    *,
    content_type: str | None,
    topic: str,
    title: str,
    summary: str,
    entities: list[dict[str, object]],
    facets: dict[str, object],
    published_at: datetime | None,
) -> list[EventRoute]:
    if content_type == "community_noise":
        return []
    text = f"{title} {summary}"
    lowered = text.casefold()
    local_date = (
        published_at.astimezone(ZoneInfo("Asia/Shanghai"))
        if published_at is not None and published_at.tzinfo is not None
        else published_at
    )
    year = local_date.year if local_date is not None else datetime.now().year

    if content_type == "data_mine":
        products = _entity_names(
            entities,
            roles={"core", "affected"},
        )
        product = products[0] if products else title
        return [EventRoute("dev_preview", f"dev_preview:{product.casefold()}")]

    if topic == "roster":
        teams = _entity_names(entities, types={"team", "organization"})
        team = teams[0] if teams else "unknown-team"
        position = next(
            (
                value
                for token, value in (
                    ("打野", "jungle"),
                    ("上单", "top"),
                    ("中单", "mid"),
                    ("下路", "bot"),
                    ("adc", "bot"),
                    ("辅助", "support"),
                    ("教练", "coach"),
                )
                if token in lowered
            ),
            "unknown",
        )
        return [
            EventRoute(
                "transfer_saga",
                f"{team}:{position}:{year}off",
            )
        ]

    patch_match = _PATCH_PATTERN.search(text)
    version = patch_match.group(1) if patch_match is not None else "unknown"
    if topic == "patch":
        routes = [EventRoute("patch_cycle", f"patch:{version}")]
        if (
            content_type in {"official_fact", "official_notice"}
            and any(
                term in text
                for term in ("经典模式", "重大玩法", "系统重做", "全新模式")
            )
        ):
            feature = next(
                (
                    term
                    for term in ("经典模式", "重大玩法", "系统重做", "全新模式")
                    if term in text
                ),
                "重大玩法更新",
            )
            routes.append(
                EventRoute(
                    "major_gameplay_change",
                    f"gameplay:{feature}",
                    "component",
                )
            )
        return routes

    if topic == "game_mode" and (
        "云顶" in text or "tft" in lowered or "teamfight tactics" in lowered
    ):
        return [EventRoute("tft_patch", f"tft:{version}")]

    if topic in {"skin", "champion", "game_mode"}:
        products = _entity_names(entities, roles={"core", "affected"})
        product = products[0] if products else title
        return [
            EventRoute(
                "release_saga",
                f"release:{product.casefold()}",
            )
        ]

    if topic == "esports":
        leagues = _entity_names(
            entities,
            types={"league", "tournament"},
        )
        league = leagues[0].casefold() if leagues else "unknown-league"
        if any(term in lowered for term in _MAJOR_MATCH_TERMS):
            stage = next(
                (term for term in _MAJOR_MATCH_TERMS if term in lowered),
                "key-stage",
            )
            return [EventRoute("major_match", f"{league}:{stage}")]
        date_key = (
            local_date.date().isoformat()
            if local_date is not None
            else "unknown-date"
        )
        return [EventRoute("daily_matches", f"{league}:{date_key}")]

    if topic == "activity" and (
        "神话商城" in text or "mythic shop" in lowered
    ):
        week = local_date.isocalendar().week if local_date is not None else 0
        return [EventRoute("shop_rotation", f"mythic_shop:week:{week}")]

    if topic == "activity":
        activities = _entity_names(entities, types={"activity"})
        activity = activities[0] if activities else title
        return [EventRoute("activity", f"activity:{activity.casefold()}")]

    if topic == "service":
        return [EventRoute("incident", f"incident:{title.casefold()}")]

    temporal = facets.get("temporal")
    if isinstance(temporal, dict) and temporal.get("is_recurring"):
        return [EventRoute("other", f"recurring:{topic}:{year}")]
    return []


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
