import re
from dataclasses import dataclass
from typing import Literal

from app.domain.event_families import anchors_from_entities, family_hints, has_strong_anchor
from app.domain.event_types import EventFamily
from app.models.normalized_item import NormalizedItem


AdmissionKind = Literal["skip", "update_existing_only", "create_or_update"]

_CREATE_MESSAGE_TYPES = frozenset(
    {
        "game_patch_notes",
        "game_official_preview",
        "game_announcement",
        "game_notice",
        "game_community_notice",
        "game_leak",
        "esports_announcement",
        "esports_rumor_speculation",
        "lol_universe_announcement",
        "lol_universe_leak",
        "other_lol_product_announcement",
        "other_lol_product_leak",
        "riot_ecosystem_announcement",
        "riot_ecosystem_leak",
    }
)
_DISCUSSION_OR_PROMOTION_TYPES = frozenset(
    value
    for value in (
        "game_promotion_interaction",
        "game_community_promotion_interaction",
        "game_community_discussion",
        "esports_promotion_interaction",
        "esports_community_discussion",
        "lol_universe_promotion_interaction",
        "lol_universe_community_discussion",
        "other_lol_product_promotion_interaction",
        "other_lol_product_community_discussion",
        "riot_ecosystem_promotion_interaction",
        "riot_ecosystem_community_discussion",
    )
)
_FREE_CHAMPION_ROTATION_PATTERNS = (
    re.compile(r"周免英雄|每周免费英雄|免费英雄(?:轮换|名单|阵容)|本周免费英雄"),
    re.compile(
        r"\b(?:weekly\s+)?free(?:-to-play\s+)?champions?\s+(?:rotation|lineup|pool)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bfree\s+(?:champion\s+)?rotation\b", re.IGNORECASE),
    re.compile(r"\brotation\s+of\s+free\s+champions?\b", re.IGNORECASE),
)
_FREE_ROTATION_FALSE_POSITIVES = (
    "英雄平衡",
    "英雄调整",
    "新英雄",
    "英雄发布",
    "champion balance",
    "champion update",
    "new champion",
    "champion release",
    "champion launch",
)


@dataclass(frozen=True, slots=True)
class AdmissionDecision:
    decision: AdmissionKind
    family_hints: tuple[EventFamily, ...]
    reasons: tuple[str, ...]
    strong_anchors: dict[str, object]


def _is_free_champion_rotation(item: NormalizedItem) -> bool:
    text = "\n".join(
        value
        for value in (
            item.normalized_title,
            item.normalized_text,
            item.summary,
            item.translated_title,
            item.translated_text,
        )
        if isinstance(value, str) and value.strip()
    ).casefold()
    if any(marker.casefold() in text for marker in _FREE_ROTATION_FALSE_POSITIVES):
        return False
    return any(pattern.search(text) for pattern in _FREE_CHAMPION_ROTATION_PATTERNS)


def decide_event_admission(item: NormalizedItem) -> AdmissionDecision:
    anchors = anchors_from_entities(item.entities)
    families = tuple(family_hints(item.topics))
    if item.publication_status != "published":
        return AdmissionDecision("skip", families, ("normalized item is not published",), anchors)
    if item.content_form in {"media_only", "link_only"}:
        return AdmissionDecision("skip", families, (f"content_form={item.content_form}",), anchors)
    if _is_free_champion_rotation(item):
        return AdmissionDecision(
            "skip",
            families,
            ("editorial exclusion: free champion rotation is outside the event layer",),
            anchors,
        )
    if (
        item.products == ["unknown"]
        and item.message_type == "unknown"
        and item.topics == ["unknown"]
        and not anchors
    ):
        return AdmissionDecision("skip", families, ("all semantic axes are unknown",), anchors)
    if item.content_form == "repost":
        return AdmissionDecision(
            "update_existing_only",
            families,
            ("pure repost cannot create an event",),
            anchors,
        )
    if item.message_type in _CREATE_MESSAGE_TYPES:
        if item.message_type.endswith(("leak", "speculation")) and not has_strong_anchor(anchors):
            return AdmissionDecision(
                "update_existing_only",
                families,
                ("unconfirmed report lacks a stable identity anchor",),
                anchors,
            )
        return AdmissionDecision(
            "create_or_update", families, (f"message_type={item.message_type}",), anchors
        )
    if item.message_type in _DISCUSSION_OR_PROMOTION_TYPES:
        official_promotion_with_anchor = (
            "promotion_interaction" in item.message_type
            and has_strong_anchor(anchors)
            and bool(item.raw_item.source.is_official)
        )
        return AdmissionDecision(
            "create_or_update" if official_promotion_with_anchor else "update_existing_only",
            families,
            (
                "official promotion contains a stable release anchor"
                if official_promotion_with_anchor
                else "discussion or promotion may only update an identified event"
            ,),
            anchors,
        )
    if has_strong_anchor(anchors):
        return AdmissionDecision(
            "create_or_update", families, ("message contains a stable event anchor",), anchors
        )
    return AdmissionDecision(
        "update_existing_only",
        families,
        ("no deterministic creation signal",),
        anchors,
    )
