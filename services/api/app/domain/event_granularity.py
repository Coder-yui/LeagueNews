import re

from app.models.normalized_item import NormalizedItem


_ROUNDUP_MARKERS = re.compile(
    r"(?:"
    r"(?:今日|今天|今晚|明日|明天|本日)[^。\n]{0,20}(?:赛程|赛果|比赛(?:安排|结果|汇总)|赛事汇总|[一二三四五六七八九十\d]+\s*场比赛|多场比赛)"
    r"|(?:赛程|赛果|比赛|赛事)\s*(?:汇总|合集|一览|速报)"
    r"|(?:daily|today(?:'s)?|matchday)\s+(?:matches?|results?|schedule|fixtures?)"
    r"|(?:matches?|results?)\s+(?:summary|roundup)"
    r"|schedule\s+roundup"
    r")",
    re.IGNORECASE,
)
_SCHEDULE_CHANGE_MARKERS = re.compile(
    r"(?:延期|推迟|提前|改期|重赛|场地(?:变更|更换)|对阵(?:变更|调整)|赛制(?:变更|调整)|"
    r"reschedul(?:e|ed)|postpon(?:e|ed)|moved|rematch|venue change|format change|bo[345])",
    re.IGNORECASE,
)
_MATCH_PAIR_PATTERN = re.compile(
    r"(?P<left>[A-Za-z0-9][A-Za-z0-9 ._'’-]{0,35}|[\u4e00-\u9fff]{1,12})"
    r"(?:\s+vs\.?\s+|\s+v\.?\s+|\s*对阵\s*|\s*对战\s*|\s*迎战\s*|\s*交手\s*|\s*对决\s*)"
    r"(?P<right>[A-Za-z0-9][A-Za-z0-9 ._'’-]{0,35}|[\u4e00-\u9fff]{1,12})",
    re.IGNORECASE,
)
_MATCH_SCORE_PATTERN = re.compile(
    r"(?P<left>[A-Za-z][A-Za-z0-9 ._'’-]{0,35})\s*"
    r"(?P<score>[0-3]\s*[-:：]\s*[0-3])\s*"
    r"(?P<right>[A-Za-z][A-Za-z0-9 ._'’-]{0,35})",
    re.IGNORECASE,
)


def _item_text(item: NormalizedItem) -> str:
    """Use one canonical text representation so translated copies do not double-count signals."""
    primary = [item.normalized_title, item.normalized_text]
    if not any(isinstance(value, str) and value.strip() for value in primary):
        primary = [
            item.summary,
            item.translated_title,
            item.translated_text,
        ]
    parts: list[str] = []
    seen: set[str] = set()
    for value in primary:
        if not isinstance(value, str):
            continue
        normalized = value.strip()
        if normalized and normalized not in seen:
            parts.append(normalized)
            seen.add(normalized)
    return "\n".join(parts)


def _match_key(left: str, right: str) -> tuple[str, str]:
    first = re.sub(r"\s+", " ", left).strip().casefold()
    second = re.sub(r"\s+", " ", right).strip().casefold()
    return (first, second) if first <= second else (second, first)


def explicit_match_count(item: NormalizedItem) -> int:
    text = _item_text(item)
    structures = {
        _match_key(match.group("left"), match.group("right"))
        for match in _MATCH_PAIR_PATTERN.finditer(text)
    }
    structures.update(
        _match_key(match.group("left"), match.group("right"))
        for match in _MATCH_SCORE_PATTERN.finditer(text)
    )
    return len(structures)


def is_daily_match_roundup(item: NormalizedItem) -> bool:
    """Recognize daily reminders/results without classifying formal schedule changes."""
    if not (
        "lol_esports" in item.products
        or any(topic.startswith("esports_") for topic in item.topics)
    ):
        return False
    text = _item_text(item)
    return bool(
        _ROUNDUP_MARKERS.search(text)
        and explicit_match_count(item) >= 2
        and not _SCHEDULE_CHANGE_MARKERS.search(text)
    )


def editorial_granularity_guidance(item: NormalizedItem) -> list[str]:
    guidance: list[str] = []
    if is_daily_match_roundup(item):
        guidance.extend(
            [
                "daily_esports_match_roundup",
                f"explicit_match_count={explicit_match_count(item)}",
                "emit_one_esports_match_per_explicit_match; do_not_create_daily_preview_or_summary_event",
            ]
        )
    if "activities_rewards" in item.topics:
        guidance.append(
            "activity_rewards_are_components_by_default; create_a_separate_cosmetic_only_for_an_independent_lifecycle"
        )
    return guidance
