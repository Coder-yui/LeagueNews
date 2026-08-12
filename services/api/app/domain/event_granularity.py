import re

from app.models.normalized_item import NormalizedItem


_ROUNDUP_MARKERS = re.compile(
    r"(?:今日|今天|今晚|明日|明天|本日|今日赛程|今日赛果|比赛预告|比赛结果|赛前|赛后|"
    r"today(?:'s)?|tonight|daily|matchday|results? summary|daily summary)",
    re.IGNORECASE,
)
_SCHEDULE_CHANGE_MARKERS = re.compile(
    r"(?:延期|推迟|提前|改期|重赛|场地(?:变更|更换)|对阵(?:变更|调整)|赛制(?:变更|调整)|"
    r"reschedul(?:e|ed)|postpon(?:e|ed)|moved|rematch|venue change|format change|bo[345])",
    re.IGNORECASE,
)
_MATCH_PAIR_MARKERS = re.compile(
    r"(?:\bvs\.?\b|\bv\.?\b|对阵|对战|迎战|交手|对决)", re.IGNORECASE
)
_SCORE_MARKER = re.compile(r"\b\d+\s*[-:：]\s*\d+\b")


def _item_text(item: NormalizedItem) -> str:
    return "\n".join(
        value
        for value in (
            item.normalized_title,
            item.normalized_text,
            item.summary,
            item.translated_title,
            item.translated_text,
        )
        if isinstance(value, str) and value.strip()
    )


def explicit_match_count(item: NormalizedItem) -> int:
    text = _item_text(item)
    pair_count = len(_MATCH_PAIR_MARKERS.findall(text))
    score_count = len(_SCORE_MARKER.findall(text))
    team_count = sum(1 for entity in item.entities if entity.get("type") == "team")
    return max(pair_count, score_count, team_count // 2)


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
