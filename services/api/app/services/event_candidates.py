import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.models.event import Event, EventMessage
from app.models.normalized_item import NormalizedItem

_PATCH_PATTERN = re.compile(r"(?<!\d)(\d{1,2}\.\d{1,2})(?!\d)")
_WORD_PATTERN = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)


@dataclass(frozen=True)
class EventCandidate:
    event_id: int
    event_key: str | None
    title: str
    score: float
    reasons: tuple[str, ...]


def stable_event_key(item: NormalizedItem) -> str | None:
    entity_text = " ".join(
        str(entity.get("canonical_name") or entity.get("name") or "")
        for entity in item.entities
    )
    text = f"{item.normalized_title} {item.translated_title or ''} {entity_text}"
    match = _PATCH_PATTERN.search(text)
    if match is None:
        return None
    lowered = text.casefold()
    has_patch_context = (
        "patch" in lowered
        or "preview" in lowered
        or "版本" in text
        or any(
            str(entity.get("type", "")).casefold() in {"patch", "version"}
            for entity in item.entities
        )
    )
    if not has_patch_context:
        return None
    return f"patch:{match.group(1)}"


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
        for name in _entity_names(message.normalized_item)
    }


def _naive_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def _within_window(item: NormalizedItem, event: Event) -> tuple[bool, int | None]:
    published_at = item.raw_item.published_at
    event_at = event.last_published_at
    if published_at is None or event_at is None:
        return False, None
    distance = abs((_naive_utc(published_at) - _naive_utc(event_at)).days)
    if "版本" in item.category or "patch" in item.category.casefold():
        window_days = 45
    elif "赛事" in item.category:
        window_days = 14
    else:
        window_days = 7
    return distance <= window_days, distance


def find_event_candidates(
    db: Session,
    *,
    normalized_item_id: int,
    limit: int = 5,
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
    statement = (
        select(Event)
        .options(
            selectinload(Event.messages).selectinload(
                EventMessage.normalized_item
            )
        )
        .where(Event.status == "active")
        .order_by(Event.id)
    )

    candidates: list[EventCandidate] = []
    for event in db.scalars(statement):
        reasons: list[str] = []
        score = 0.0
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

        has_identity_signal = exact_key or bool(overlapping_entities) or title_similarity >= 0.15
        if not has_identity_signal:
            continue
        if not exact_key and not within_window:
            continue
        candidates.append(
            EventCandidate(
                event_id=event.id,
                event_key=event.event_key,
                title=event.title,
                score=round(score, 4),
                reasons=tuple(reasons),
            )
        )

    return sorted(
        candidates,
        key=lambda candidate: (-candidate.score, candidate.event_id),
    )[:limit]
