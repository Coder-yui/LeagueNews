import re
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from app.models.event import Event, EventMessage
from app.models.normalized_item import NormalizedItem
from app.services.raw_item_versions import superseded_normalized_item_ids

_PATCH_PATTERN = re.compile(r"(?<!\d)(\d{1,2}\.\d{1,2})(?!\d)")
_EVENT_DATE_PATTERN = re.compile(
    r"(?:(?P<year>20\d{2})年)?(?P<month>\d{1,2})月(?P<day>\d{1,2})日"
)
_WORD_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)
_KEY_TOKEN_PATTERN = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_MATCH_WORDS = ("击败", "战胜", "对阵", "vs", " vs ", "比分", "赛果")
_ESPORTS_CATEGORY_WORDS = ("赛事", "赛果", "赛程", "比赛", "集锦")
_LATE_PLAYOFF_WORDS = (
    "半决赛",
    "胜者组决赛",
    "败者组决赛",
    "季军赛",
    "总决赛",
    "决赛",
)
_TRANSFER_WORDS = ("转会", "加盟", "离队", "续约", "试训", "接触")
_CN_CONNECTORS = {"baidu_tieba", "weibo", "tencent_lol"}
_MYTHIC_SHOP_TERMS = ("神话商城", "mythic shop")
_ROTATION_TERMS = ("轮换", "每日更新", "每周更新", "daily rotation", "weekly rotation")


@dataclass(frozen=True)
class EventCandidate:
    event_id: int
    event_key: str | None
    title: str
    summary: str
    category: str
    core_entities: tuple[str, ...]
    event_type: str
    lifecycle_status: str
    credibility_status: str
    match_level: str
    score: float
    reasons: tuple[str, ...]


def _event_date_key(item: NormalizedItem, text: str) -> str | None:
    published_at = item.raw_item.published_at
    local_published_at = (
        published_at.astimezone(ZoneInfo("Asia/Shanghai"))
        if published_at is not None and published_at.tzinfo is not None
        else published_at
    )
    match = _EVENT_DATE_PATTERN.search(text)
    if match is not None:
        year = int(
            match.group("year")
            or (
                local_published_at.year
                if local_published_at is not None
                else datetime.now(ZoneInfo("Asia/Shanghai")).year
            )
        )
        try:
            return datetime(
                year,
                int(match.group("month")),
                int(match.group("day")),
            ).date().isoformat()
        except ValueError:
            pass
    return (
        local_published_at.date().isoformat()
        if local_published_at is not None
        else None
    )


def stable_event_key(item: NormalizedItem) -> str | None:
    entity_text = " ".join(
        str(entity.get("canonical_name") or entity.get("name") or "")
        for entity in item.entities
    )
    text = f"{item.normalized_title} {item.translated_title or ''} {entity_text}"
    match = _PATCH_PATTERN.search(text)
    lowered = text.casefold()
    if match is not None:
        has_patch_context = (
            "patch" in lowered
            or "preview" in lowered
            or "版本" in text
            or any(
                str(entity.get("type", "")).casefold() in {"patch", "version"}
                for entity in item.entities
            )
        )
        if has_patch_context:
            return f"patch:{match.group(1)}"

    policy = event_aggregation_policy(item)
    if policy and policy["event_eligible"]:
        return str(policy["stable_event_key"])

    game_modes = _entity_values(item, {"game_mode", "mode"})
    if game_modes and (
        _category_family(item.category) == "game_mode"
        or "模式" in text
        or "mode" in lowered
    ):
        return f"mode:{game_modes[0]}"

    published_at = item.raw_item.published_at
    date_key = _event_date_key(item, f"{text} {item.summary}")
    teams = _entity_values(item, {"team", "esports_team"})
    is_lpl = "lpl" in lowered or "英雄联盟职业联赛" in text
    is_esports = any(word in item.category for word in _ESPORTS_CATEGORY_WORDS)
    if is_lpl and is_esports and date_key:
        is_late_playoff = any(word in text for word in _LATE_PLAYOFF_WORDS)
        if is_late_playoff:
            if len(teams) >= 2:
                return (
                    f"match:lpl:{date_key}:"
                    f"{'-vs-'.join(sorted(teams)[:2])}"
                )
            return None
        return f"matchday:lpl:{date_key}"

    is_match = (
        is_esports
        and len(teams) >= 2
        and any(word in lowered for word in _MATCH_WORDS)
    )
    if is_match and date_key:
        league = "lpl" if "lpl" in lowered or "英雄联盟职业联赛" in text else "lol"
        return f"match:{league}:{date_key}:{'-vs-'.join(sorted(teams)[:2])}"

    players = _entity_values(item, {"player", "pro_player", "coach"})
    is_transfer = "转会" in item.category or any(
        word in lowered for word in _TRANSFER_WORDS
    )
    if is_transfer and players and teams:
        year = published_at.year if published_at else datetime.now(UTC).year
        return f"transfer:{year}:{players[0]}:{teams[0]}"
    return None


def event_aggregation_policy(item: NormalizedItem) -> dict[str, object] | None:
    text = " ".join(
        (
            item.normalized_title,
            item.translated_title or "",
            item.summary,
            item.normalized_text,
        )
    )
    lowered = text.casefold()
    if not (
        any(term in lowered for term in _MYTHIC_SHOP_TERMS)
        and any(term in lowered for term in _ROTATION_TERMS)
    ):
        return None

    connector_type = item.raw_item.source.connector_type
    explicit_cn = "国服" in text or "中国服务器" in text
    is_cn = connector_type != "x_twitter" and (
        explicit_cn or connector_type in _CN_CONNECTORS
    )
    headline = f"{item.normalized_title} {item.translated_title or ''}"
    headline_lowered = headline.casefold()
    if "每周" in headline or "weekly rotation" in headline_lowered:
        cadence = "weekly"
    elif "每日" in headline or "daily rotation" in headline_lowered:
        cadence = "daily"
    else:
        cadence = (
            "daily"
            if "每日" in text or "daily rotation" in lowered
            else "weekly"
        )
    published_at = item.raw_item.published_at or item.raw_item.ingested_at
    localized = (
        published_at.replace(tzinfo=UTC)
        if published_at.tzinfo is None
        else published_at
    ).astimezone(ZoneInfo("Asia/Shanghai"))
    iso_year, iso_week, _ = localized.isocalendar()
    return {
        "policy_type": "mythic_shop_rotation",
        "region": "cn" if is_cn else "international",
        "event_eligible": is_cn,
        "cadence": cadence,
        "aggregation_period": f"{iso_year}-W{iso_week:02d}",
        "stable_event_key": (
            f"mythic-shop:cn:{iso_year}-w{iso_week:02d}"
            if is_cn
            else None
        ),
        "importance_range": [0.3, 0.45],
        "required_event_type": "activity",
        "daily_update_kind": "context",
    }


def _key_token(value: str) -> str:
    return _KEY_TOKEN_PATTERN.sub("-", value.strip().casefold()).strip("-")[:60]


def _entity_values(item: NormalizedItem, accepted_types: set[str]) -> list[str]:
    return sorted(
        {
            token
            for entity in item.entities
            if str(entity.get("type", "")).casefold() in accepted_types
            if (
                token := _key_token(
                    str(entity.get("canonical_name") or entity.get("name") or "")
                )
            )
        }
    )


def _tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for part in _WORD_PATTERN.findall(text.casefold()):
        if part.isascii():
            tokens.add(part)
        elif len(part) == 1:
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
    return {
        str(entity.get("canonical_name") or entity.get("name") or "").strip().casefold()
        for entity in item.entities
        if entity.get("canonical_name") or entity.get("name")
    }


def _event_entity_names(event: Event) -> set[str]:
    return {
        name
        for message in event.messages
        if message.membership_status == "active"
        and message.normalized_item.publication_status == "published"
        for name in _entity_names(message.normalized_item)
    }


def _item_context(item: NormalizedItem) -> str:
    entities = " ".join(
        str(entity.get("canonical_name") or entity.get("name") or "")
        for entity in item.entities
    )
    return " ".join(
        (
            item.translated_title or item.normalized_title,
            item.summary,
            entities,
        )
    )


def _event_context(event: Event) -> str:
    return " ".join(
        (
            event.title,
            event.summary,
            " ".join(sorted(_event_entity_names(event))),
        )
    )


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _category_family(category: str) -> str:
    value = category.strip().casefold()
    if any(token in value for token in ("新模式", "游戏模式", "玩法模式", "game mode")):
        return "game_mode"
    if "版本" in value or "patch" in value:
        return "patch"
    if "转会" in value or "阵容" in value:
        return "roster"
    if "赛事" in value or "赛果" in value or "比赛" in value:
        return "esports"
    if "活动" in value:
        return "activity"
    return value


def _within_window(item: NormalizedItem, event: Event) -> tuple[bool, int | None]:
    published_at = item.raw_item.published_at
    event_at = event.last_published_at
    if published_at is None or event_at is None:
        return False, None
    distance = int(
        abs(
            (
                _naive_utc(published_at) - _naive_utc(event_at)
            ).total_seconds()
        )
        // 86_400
    )
    category_family = _category_family(item.category)
    if category_family == "patch" or event.event_type == "patch":
        window_days = 45
    elif (
        category_family == "game_mode"
        or event.event_type == "major_gameplay_change"
    ):
        window_days = 180
    elif category_family == "roster" or event.event_type in {"transfer", "roster"}:
        window_days = 180
    elif category_family == "esports" or event.event_type in {"match", "tournament"}:
        window_days = 14
    elif category_family == "activity" or event.event_type == "activity":
        window_days = 90
    else:
        window_days = 7
    return distance <= window_days, distance


def find_event_candidates(
    db: Session,
    *,
    normalized_item_id: int,
    limit: int = 5,
    include_event_ids: set[int] | None = None,
) -> list[EventCandidate]:
    if limit < 1 or limit > 5:
        raise ValueError("candidate limit must be between 1 and 5")

    item = db.scalar(
        select(NormalizedItem)
        .where(NormalizedItem.id == normalized_item_id)
        .options(selectinload(NormalizedItem.raw_item))
    )
    if item is None:
        raise ValueError(f"normalized item {normalized_item_id} not found")

    item_key = stable_event_key(item)
    item_entities = _entity_names(item)
    superseded_item_ids = set(superseded_normalized_item_ids(db, item))
    included_ids = include_event_ids or set()
    statement = (
        select(Event)
        .options(
            selectinload(Event.messages).selectinload(
                EventMessage.normalized_item
            )
        )
        .where(
            or_(
                Event.status == "active",
                Event.id.in_(included_ids),
            )
        )
        .order_by(Event.id)
    )

    candidates: list[EventCandidate] = []
    for event in db.scalars(statement):
        reasons: list[str] = []
        score = 0.0
        forced_candidate = event.id in included_ids
        if forced_candidate:
            score += 500
            reasons.append("该事件是本次撤回前的原事件，保留为纠正候选")
        supersedes_member = bool(
            superseded_item_ids
            & {
                message.normalized_item_id
                for message in event.messages
                if message.membership_status == "active"
            }
        )
        if supersedes_member:
            score += 200
            reasons.append("当前消息是该事件成员的更新版本")
        exact_key = item_key is not None and event.event_key == item_key
        if exact_key:
            score += 100
            reasons.append(f"稳定事件键精确匹配：{item_key}")

        within_window, distance = _within_window(item, event)
        if within_window:
            score += 5
            reasons.append(f"发布时间相距 {distance} 天")

        if event.category == item.category:
            score += 10
            reasons.append(f"分类一致：{item.category}")
        elif _category_family(event.category) == _category_family(item.category):
            score += 10
            reasons.append(
                f"分类归一一致：{event.category} / {item.category}"
            )

        overlapping_entities = sorted(item_entities & _event_entity_names(event))
        if overlapping_entities:
            score += min(30, 15 * len(overlapping_entities))
            reasons.append("核心实体重叠：" + "、".join(overlapping_entities))

        title_similarity = _similarity(
            item.translated_title or item.normalized_title,
            event.title,
        )
        if title_similarity >= 0.15:
            score += round(title_similarity * 20, 4)
            reasons.append(f"标题相似度 {title_similarity:.2f}")

        has_identity_signal = (
            supersedes_member
            or exact_key
            or bool(overlapping_entities)
            or title_similarity >= 0.15
        )
        if (
            not forced_candidate
            and not supersedes_member
            and not exact_key
            and not within_window
        ):
            continue
        match_level = "strong" if has_identity_signal else "broad"
        if not has_identity_signal:
            context_similarity = _similarity(
                _item_context(item),
                _event_context(event),
            )
            score += round(context_similarity * 20, 4)
            reasons.append(
                "宽召回候选：时间范围相符，交由事件编辑判断别名、父级对象或附属内容关系"
            )
        candidates.append(
            EventCandidate(
                event_id=event.id,
                event_key=event.event_key,
                title=event.title,
                summary=event.summary[:800],
                category=event.category,
                core_entities=tuple(sorted(_event_entity_names(event))),
                event_type=event.event_type,
                lifecycle_status=event.lifecycle_status,
                credibility_status=event.credibility_status,
                match_level=match_level,
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
