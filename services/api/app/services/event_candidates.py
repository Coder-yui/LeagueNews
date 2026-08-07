import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.content_blocks import text_from_content_blocks
from app.domain.event_clusters import is_marquee_match, route_event_clusters
from app.domain.ontology import EventRoute
from app.models.event import Event, EventMessage
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.services.raw_item_versions import superseded_normalized_item_ids

_WORD_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_HOTFIX_CONTINUITY_TYPES = {
    "champion",
    "game_mode",
    "item",
    "system",
}
_IDENTITY_TEXT_PATTERN = re.compile(r"[^a-z0-9\u4e00-\u9fff]+", re.IGNORECASE)
_GENERIC_ENTITY_NAMES = {
    "英雄联盟",
    "leagueoflegends",
    "lol",
    "云顶之弈",
    "teamfighttactics",
    "tft",
    "拳头游戏",
    "riotgames",
}
_ROUTE_SUBJECT_NOISE = {
    "gameplay_release": (
        "英雄联盟",
        "leagueoflegends",
        "lol",
        "云顶之弈",
        "teamfighttactics",
        "tft",
        "游戏模式",
        "gamemode",
        "模式",
        "mode",
    ),
    "cosmetic_release": ("皮肤", "skin"),
    "player_activity": ("活动", "event"),
    "community_activity": ("活动", "event"),
}


def _importance_dimension_value(item: NormalizedItem, key: str) -> str:
    dimension = item.importance_dimensions.get(key)
    if not isinstance(dimension, dict):
        return ""
    return str(dimension.get("value") or "")


@dataclass(frozen=True)
class EventCandidate:
    event_id: int
    aggregation_key: str | None
    title: str
    summary: str
    core_entities: tuple[str, ...]
    event_kind: str
    aggregation_strategy: str
    product_scope: str
    lifecycle_status: str
    credibility_status: str
    timeline_nodes: tuple[str, ...]
    member_count: int
    match_level: str
    deterministic_route_key: str | None
    score: float
    reasons: tuple[str, ...]


def aggregation_routes(item: NormalizedItem) -> list[EventRoute]:
    return route_event_clusters(
        topic=item.primary_topic,
        subtopic=item.subtopic,
        source_kind=item.source_kind,
        information_stage=item.information_stage,
        content_form=item.content_form,
        product_scope=item.product_scope,
        title=item.translated_title or item.normalized_title,
        summary=item.summary,
        entities=list(item.entities),
        facets=dict(item.facets),
        published_at=item.raw_item.published_at,
        fact_claims=[
            {
                "subject": dict(claim.subject),
                "predicate": claim.predicate,
                "object": dict(claim.object_value),
                "temporal_role": claim.temporal_role,
            }
            for claim in item.claims
            if claim.status == "active"
        ],
        source_connector_type=item.raw_item.source.connector_type,
        source_name=item.raw_item.source.name,
        editorial_prominence=_importance_dimension_value(item, "prominence") or "normal",
    )


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for part in _WORD_PATTERN.findall(text.casefold()):
        if part.isascii() or len(part) == 1:
            tokens.add(part)
        else:
            tokens.update(part[index : index + 2] for index in range(len(part) - 1))
    return tokens


def _similarity(left: str, right: str) -> float:
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _entity_names(item: NormalizedItem) -> set[str]:
    names: set[str] = set()
    for entity in item.entities:
        if str(entity.get("role") or "").casefold() == "context":
            continue
        name = str(
            entity.get("canonical_name") or entity.get("name") or ""
        ).strip().casefold()
        if not name or _identity_text(name) in _GENERIC_ENTITY_NAMES:
            continue
        names.add(name)
    return names


def _event_entity_names(event: Event) -> set[str]:
    return {
        name
        for message in event.messages
        if message.membership_status == "active"
        and message.normalized_item.publication_status == "published"
        for name in _entity_names(message.normalized_item)
    }


def _hotfix_subjects(item: NormalizedItem) -> set[str]:
    return {
        str(entity.get("canonical_name") or entity.get("name") or "")
        .strip()
        .casefold()
        for entity in item.entities
        if str(entity.get("type") or "").casefold() in _HOTFIX_CONTINUITY_TYPES
        and str(entity.get("role") or "core").casefold() in {"core", "affected"}
        and (entity.get("canonical_name") or entity.get("name"))
    }


def _event_hotfix_subjects(event: Event) -> set[str]:
    return {
        subject
        for message in event.messages
        if message.membership_status == "active"
        and message.normalized_item.publication_status == "published"
        for subject in _hotfix_subjects(message.normalized_item)
    }


def _named_activity_subjects(item: NormalizedItem) -> set[str]:
    return {
        str(entity.get("canonical_name") or entity.get("name") or "").strip()
        for entity in item.entities
        if str(entity.get("type") or "").casefold() == "activity"
        and str(entity.get("role") or "core").casefold() in {"core", "affected"}
        and (entity.get("canonical_name") or entity.get("name"))
    }


def _event_named_activity_subjects(event: Event) -> set[str]:
    return {
        subject
        for message in event.messages
        if message.membership_status == "active"
        and message.normalized_item.publication_status == "published"
        for subject in _named_activity_subjects(message.normalized_item)
    }


def _team_subjects(item: NormalizedItem) -> set[str]:
    return {
        str(entity.get("canonical_name") or entity.get("name") or "")
        .strip()
        .casefold()
        for entity in item.entities
        if str(entity.get("type") or "").casefold() == "team"
        and str(entity.get("role") or "core").casefold() in {"core", "affected"}
        and (entity.get("canonical_name") or entity.get("name"))
    }


def _event_team_subjects(event: Event) -> set[str]:
    return {
        subject
        for message in event.messages
        if message.membership_status == "active"
        and message.normalized_item.publication_status == "published"
        for subject in _team_subjects(message.normalized_item)
    }


def _identity_text(value: str) -> str:
    return _IDENTITY_TEXT_PATTERN.sub("", value.casefold())


def _route_subject(key: str, event_kind: str) -> str:
    subject = _identity_text(key.rsplit(":", 1)[-1])
    for noise in _ROUTE_SUBJECT_NOISE.get(event_kind, ()):
        subject = subject.replace(_identity_text(noise), "")
    return subject


def _same_stable_subject(
    left_key: str,
    right_key: str,
    *,
    event_kind: str,
) -> bool:
    left = _route_subject(left_key, event_kind)
    right = _route_subject(right_key, event_kind)
    if not left or not right:
        return False
    if left == right:
        return len(left) >= 2
    return min(len(left), len(right)) >= 4 and (
        left in right or right in left
    )


def _stable_subject_alias_key(
    event: Event,
    routes: list[EventRoute],
) -> tuple[str | None, set[str]]:
    if not event.aggregation_key or event.event_kind not in _ROUTE_SUBJECT_NOISE:
        return None, set()
    matches = [
        route
        for route in routes
        if route.aggregation_key
        and route.aggregation_key != event.aggregation_key
        and route.event_kind == event.event_kind
        and route.aggregation_strategy == event.aggregation_strategy
        and route.product_scope == event.product_scope
        and _same_stable_subject(
            route.aggregation_key,
            event.aggregation_key,
            event_kind=route.event_kind,
        )
    ]
    if len(matches) != 1:
        return None, set()
    route = matches[0]
    return route.aggregation_key, {
        f"{route.aggregation_key} ↔ {event.aggregation_key}"
    }


def _single_activity_reminder_route(
    event: Event,
    routes: list[EventRoute],
) -> EventRoute | None:
    matches = [
        route
        for route in routes
        if route.event_kind == "player_activity"
        and route.aggregation_strategy == "timeline"
        and route.creation_policy == "existing_only"
        and route.product_scope == event.product_scope
        and route.event_kind == event.event_kind
        and route.aggregation_strategy == event.aggregation_strategy
    ]
    return matches[0] if len(matches) == 1 else None


def _named_activity_aliases(
    item: NormalizedItem,
    event: Event,
    routes: list[EventRoute],
) -> tuple[str | None, set[str]]:
    route = _single_activity_reminder_route(event, routes)
    if route is None:
        return None, set()
    aliases: set[str] = set()
    for current in _named_activity_subjects(item):
        current_identity = _identity_text(current)
        if len(current_identity) < 4:
            continue
        for existing in _event_named_activity_subjects(event):
            existing_identity = _identity_text(existing)
            if (
                current_identity != existing_identity
                and (
                    current_identity in existing_identity
                    or existing_identity in current_identity
                )
            ):
                aliases.add(f"{current} ↔ {existing}")
    if not aliases:
        return None, set()
    return route.aggregation_key, aliases


def _item_context(item: NormalizedItem) -> str:
    return " ".join(
        (
            item.translated_title or item.normalized_title,
            item.summary,
            " ".join(sorted(_entity_names(item))),
            " ".join(
                f"{claim.subject} {claim.predicate} {claim.object_value}"
                for claim in item.claims
                if claim.status == "active"
            ),
        )
    )


def _event_context(event: Event) -> str:
    return " ".join(
        (
            event.title,
            event.summary,
            " ".join(sorted(_event_entity_names(event))),
            " ".join(
                f"{claim.subject} {claim.predicate} {claim.object_value}"
                for message in event.messages
                if message.membership_status == "active"
                for claim in message.normalized_item.claims
                if claim.status == "active"
            ),
        )
    )


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _within_window(item: NormalizedItem, event: Event) -> tuple[bool, int | None]:
    published_at = item.raw_item.published_at
    event_at = event.last_published_at
    if published_at is None or event_at is None:
        return False, None
    distance = int(
        abs((_naive_utc(published_at) - _naive_utc(event_at)).total_seconds())
        // 86_400
    )
    window_days = {
        "patch_cycle": 45,
        "timeline": 180,
        "calendar_day": 2,
        "recurring_window": 8,
        "release": 90,
        "singleton": 14,
    }.get(event.aggregation_strategy, 14)
    return distance <= window_days, distance


def _hotfix_continuation_key(
    item: NormalizedItem,
    event: Event,
    routes: list[EventRoute],
) -> tuple[str | None, set[str]]:
    if item.raw_item.published_at is None or event.last_published_at is None:
        return None, set()
    gap = abs(
        _naive_utc(item.raw_item.published_at) - _naive_utc(event.last_published_at)
    )
    if gap > timedelta(days=2):
        return None, set()
    overlap = _hotfix_subjects(item) & _event_hotfix_subjects(event)
    if not overlap:
        return None, set()
    for route in routes:
        key = route.aggregation_key
        prefix = f"hotfix:{route.product_scope}:"
        if (
            key
            and key.startswith(prefix)
            and event.aggregation_key
            and event.aggregation_key.startswith(prefix)
            and event.aggregation_key != key
            and event.event_kind == route.event_kind
            and event.aggregation_strategy == route.aggregation_strategy
            and event.product_scope == route.product_scope
        ):
            if _explicit_hotfix_date(item, key) and _event_has_explicit_hotfix_date(
                event,
                event.aggregation_key,
            ):
                continue
            return key, overlap
    return None, set()


def _source_text(item: NormalizedItem) -> str:
    return "\n".join(
        (
            item.raw_item.native_title or "",
            text_from_content_blocks(item.raw_item.content_blocks),
        )
    )


def _explicit_hotfix_date(item: NormalizedItem, key: str) -> bool:
    try:
        date_value = datetime.strptime(key.rsplit(":", 1)[-1], "%Y-%m-%d")
    except ValueError:
        return False
    year = date_value.year
    month = date_value.month
    day = date_value.day
    pattern = re.compile(
        rf"(?:{year}[年\-/]\s*0?{month}[月\-/]\s*0?{day}日?)"
        rf"|(?:0?{month}月\s*0?{day}日)"
        rf"|(?:0?{month}[\-/]\s*0?{day})(?!\d)"
    )
    return pattern.search(_source_text(item)) is not None


def _event_has_explicit_hotfix_date(event: Event, key: str) -> bool:
    return any(
        message.membership_status == "active"
        and message.normalized_item.publication_status == "published"
        and _explicit_hotfix_date(message.normalized_item, key)
        for message in event.messages
    )


def _matchday_context_key(
    item: NormalizedItem,
    event: Event,
    routes: list[EventRoute],
) -> tuple[str | None, set[str]]:
    text = f"{item.translated_title or item.normalized_title} {item.summary}"
    item_teams = _team_subjects(item)
    if len(item_teams) != 2 or not item_teams.issubset(_event_team_subjects(event)):
        return None, set()
    for route in routes:
        key = route.aggregation_key
        if not key or not key.startswith("match:"):
            continue
        parts = key.split(":", 2)
        if len(parts) != 3:
            continue
        date_key = parts[1]
        event_key_parts = (event.aggregation_key or "").split(":", 2)
        competition = event_key_parts[1] if len(event_key_parts) == 3 else None
        if is_marquee_match(
            text,
            competition,
            _importance_dimension_value(item, "prominence") or "normal",
        ):
            continue
        if (
            event.aggregation_key
            and event.aggregation_key.startswith("matchday:")
            and event.aggregation_key.endswith(f":{date_key}")
            and event.event_kind == "esports_match"
            and event.aggregation_strategy == "calendar_day"
            and event.product_scope == route.product_scope
        ):
            return key, item_teams
    return None, set()


def resolve_aggregation_routes(
    routes: list[EventRoute],
    candidates: Iterable[EventCandidate | dict[str, object]],
) -> list[EventRoute]:
    """Replace a provisional route when exactly one deterministic parent exists."""
    matches_by_route: dict[str, list[EventCandidate | dict[str, object]]] = {}
    for candidate in candidates:
        route_key = (
            candidate.deterministic_route_key
            if isinstance(candidate, EventCandidate)
            else candidate.get("deterministic_route_key")
        )
        if route_key:
            matches_by_route.setdefault(str(route_key), []).append(candidate)
    resolved: list[EventRoute] = []
    for route in routes:
        matches = matches_by_route.get(str(route.aggregation_key), [])
        if len(matches) != 1:
            resolved.append(route)
            continue
        candidate = matches[0]

        def value(name: str) -> object:
            return (
                getattr(candidate, name)
                if isinstance(candidate, EventCandidate)
                else candidate.get(name)
            )

        aggregation_key = value("aggregation_key")
        if not aggregation_key:
            resolved.append(route)
            continue
        resolved.append(
            replace(
                route,
                event_kind=str(value("event_kind")),
                aggregation_strategy=str(value("aggregation_strategy")),
                product_scope=str(value("product_scope")),
                aggregation_key=str(aggregation_key),
            )
        )
    return resolved


def find_event_candidates(
    db: Session,
    *,
    normalized_item_id: int,
    limit: int = 8,
    include_event_ids: set[int] | None = None,
) -> list[EventCandidate]:
    if limit < 1 or limit > 8:
        raise ValueError("candidate limit must be between 1 and 8")
    item = db.scalar(
        select(NormalizedItem)
        .where(NormalizedItem.id == normalized_item_id)
        .options(
            selectinload(NormalizedItem.raw_item).selectinload(RawItem.source),
            selectinload(NormalizedItem.claims),
        )
    )
    if item is None:
        raise ValueError(f"normalized item {normalized_item_id} not found")
    routes = aggregation_routes(item)
    keys = {route.aggregation_key for route in routes if route.aggregation_key}
    kinds = {route.event_kind for route in routes}
    strategies = {route.aggregation_strategy for route in routes}
    superseded_ids = set(superseded_normalized_item_ids(db, item))
    included_ids = include_event_ids or set()
    revision_event_ids = (
        set(
            db.scalars(
                select(EventMessage.event_id).where(
                    EventMessage.normalized_item_id.in_(superseded_ids)
                )
            )
        )
        if superseded_ids
        else set()
    )
    recalled_ids = included_ids | revision_event_ids
    reference = item.raw_item.published_at or datetime.now(UTC)
    if reference.tzinfo is not None:
        reference = reference.astimezone(UTC).replace(tzinfo=None)
    recall_filters = [Event.id.in_(recalled_ids)]
    if keys:
        recall_filters.append(Event.aggregation_key.in_(keys))
    if kinds:
        recall_filters.append(
            (Event.event_kind.in_(kinds))
            & (Event.product_scope == item.product_scope)
            & (Event.last_published_at >= reference - timedelta(days=180))
        )
    statement = (
        select(Event)
        .options(
            selectinload(Event.messages)
            .selectinload(EventMessage.normalized_item)
            .selectinload(NormalizedItem.claims)
        )
        .where(
            or_(*recall_filters),
            or_(Event.status == "active", Event.id.in_(recalled_ids)),
        )
        .order_by(Event.last_published_at.desc().nullslast(), Event.id.desc())
        .limit(200)
    )
    item_entities = _entity_names(item)
    candidates: list[EventCandidate] = []
    for event in db.scalars(statement):
        reasons: list[str] = []
        score = 0.0
        forced = event.id in included_ids
        revision_member = event.id in revision_event_ids
        exact_key = event.aggregation_key is not None and event.aggregation_key in keys
        hotfix_route_key, continuation_subjects = _hotfix_continuation_key(
            item,
            event,
            routes,
        )
        alias_route_key, activity_aliases = _named_activity_aliases(
            item,
            event,
            routes,
        )
        matchday_route_key, matchday_teams = _matchday_context_key(
            item,
            event,
            routes,
        )
        subject_alias_key, subject_aliases = _stable_subject_alias_key(
            event,
            routes,
        )
        deterministic_route_key = (
            hotfix_route_key
            or alias_route_key
            or matchday_route_key
            or subject_alias_key
        )
        if forced:
            score += 500
            reasons.append("该事件是撤回前的原事件")
        if revision_member:
            score += 200
            reasons.append("当前消息是该事件成员的新修订")
        if exact_key:
            score += 300
            reasons.append(f"稳定聚合键匹配：{event.aggregation_key}")
        if hotfix_route_key:
            score += 250
            reasons.append(
                "短窗口热更新连续：核心对象重叠："
                + "、".join(sorted(continuation_subjects))
            )
        if alias_route_key:
            score += 250
            reasons.append("命名主体包含：" + "、".join(sorted(activity_aliases)))
        if matchday_route_key:
            score += 300
            reasons.append(
                "比赛日上下文：同日双方队伍已归入比赛日："
                + "、".join(sorted(matchday_teams))
            )
        if subject_alias_key:
            score += 250
            reasons.append("稳定主体同义：" + "、".join(sorted(subject_aliases)))
        within_window, distance = _within_window(item, event)
        if within_window:
            score += 5
            reasons.append(f"发布时间相距 {distance} 天")
        if event.event_kind in kinds:
            score += 20
            reasons.append(f"事件事实类型一致：{event.event_kind}")
        if event.aggregation_strategy in strategies:
            score += 10
            reasons.append(f"聚合策略一致：{event.aggregation_strategy}")
        overlapping = sorted(item_entities & _event_entity_names(event))
        if overlapping:
            score += min(200, 100 * len(overlapping))
            reasons.append("实体重叠：" + "、".join(overlapping))
        title_similarity = _similarity(
            item.translated_title or item.normalized_title, event.title
        )
        semantic_similarity = _similarity(_item_context(item), _event_context(event))
        if title_similarity >= 0.15:
            score += title_similarity * 40
            reasons.append(f"标题相似度 {title_similarity:.2f}")
        if semantic_similarity >= 0.12:
            score += semantic_similarity * 100
            reasons.append(f"事实相似度 {semantic_similarity:.2f}")
        strong_identity = bool(
            revision_member
            or exact_key
            or deterministic_route_key
            or overlapping
            or semantic_similarity >= 0.25
            or title_similarity >= 0.35
        )
        has_identity = bool(
            strong_identity
            or semantic_similarity >= 0.12
            or title_similarity >= 0.15
        )
        if not forced and not has_identity and not within_window:
            continue
        candidates.append(
            EventCandidate(
                event_id=event.id,
                aggregation_key=event.aggregation_key,
                title=event.title,
                summary=event.summary[:800],
                core_entities=tuple(sorted(_event_entity_names(event))),
                event_kind=event.event_kind,
                aggregation_strategy=event.aggregation_strategy,
                product_scope=event.product_scope,
                lifecycle_status=event.lifecycle_status,
                credibility_status=event.credibility_status,
                timeline_nodes=tuple(
                    message.normalized_item.summary[:200]
                    for message in sorted(
                        (
                            value
                            for value in event.messages
                            if value.membership_status == "active"
                        ),
                        key=lambda value: value.source_published_at or value.added_at,
                    )[-5:]
                ),
                member_count=sum(
                    message.membership_status == "active"
                    for message in event.messages
                ),
                match_level="strong" if strong_identity else "broad",
                deterministic_route_key=deterministic_route_key,
                score=round(score, 4),
                reasons=tuple(reasons),
            )
        )
    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.match_level != "strong",
            -candidate.score,
            candidate.event_id,
        ),
    )[:limit]
