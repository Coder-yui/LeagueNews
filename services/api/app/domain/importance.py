from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal, TypedDict, cast

from app.domain.content_semantics import OFFICIAL_ONLY_UPDATE_SUBTOPICS

IMPORTANCE_POLICY_VERSION: Final = "importance-v8-official-updates"

ImportanceSubtype = Literal[
    "shop_daily_standard",
    "shop_cosmetic_rotation",
    "shop_rare_cosmetic",
    "shop_bulk_refresh",
    "shop_standard_offer",
    "free_champion_rotation",
    "free_reward",
    "patch_preview",
    "patch_full_preview",
    "patch_official_notes",
    "patch_hotfix",
    "pbe_change",
    "new_champion",
    "new_game_mode",
    "major_gameplay_change",
    "tft_set_update",
    "tft_cosmetic_release",
    "activity_paid",
    "activity_standard",
    "activity_free_skin",
    "esports_schedule",
    "esports_ticketing",
    "esports_regular",
    "esports_playoffs",
    "esports_final",
    "worlds_regular",
    "worlds_key",
    "roster_transfer",
    "skin_release",
    "service_incident",
    "disciplinary_action",
    "security_notice",
    "merch_release",
    "partnership",
    "media_release",
    "riot_corporate",
    "lol_universe",
    "community_event",
    "gameplay_guide",
    "community",
    "other",
]
ImportanceScale = Literal["minor", "standard", "major"]
CompetitionRegion = Literal["none", "lpl", "lck", "international", "other"]
Prominence = Literal["normal", "notable", "star"]
AudienceRegion = Literal["cn", "global", "international_only", "unknown"]
SkinTier = Literal["none", "standard", "legendary", "prestige_or_mythic", "ultimate"]


@dataclass(frozen=True, slots=True)
class ScoreBand:
    base: float
    floor: float
    cap: float


SCORE_BANDS: Final[dict[ImportanceSubtype, ScoreBand]] = {
    "shop_daily_standard": ScoreBand(0.48, 0.42, 0.54),
    "shop_cosmetic_rotation": ScoreBand(0.58, 0.52, 0.64),
    "shop_rare_cosmetic": ScoreBand(0.66, 0.60, 0.72),
    "shop_bulk_refresh": ScoreBand(0.66, 0.60, 0.72),
    "shop_standard_offer": ScoreBand(0.52, 0.42, 0.62),
    "free_champion_rotation": ScoreBand(0.42, 0.38, 0.48),
    "free_reward": ScoreBand(0.62, 0.52, 0.75),
    "patch_preview": ScoreBand(0.86, 0.82, 0.90),
    "patch_full_preview": ScoreBand(0.90, 0.87, 0.93),
    "patch_official_notes": ScoreBand(0.92, 0.88, 0.95),
    "patch_hotfix": ScoreBand(0.72, 0.62, 0.84),
    "pbe_change": ScoreBand(0.62, 0.52, 0.72),
    "new_champion": ScoreBand(0.93, 0.88, 0.96),
    "new_game_mode": ScoreBand(0.91, 0.86, 0.95),
    "major_gameplay_change": ScoreBand(0.92, 0.87, 0.95),
    "tft_set_update": ScoreBand(0.84, 0.78, 0.90),
    "tft_cosmetic_release": ScoreBand(0.58, 0.50, 0.68),
    "activity_paid": ScoreBand(0.68, 0.60, 0.76),
    "activity_standard": ScoreBand(0.72, 0.62, 0.82),
    "activity_free_skin": ScoreBand(0.88, 0.83, 0.93),
    "esports_schedule": ScoreBand(0.52, 0.46, 0.62),
    "esports_ticketing": ScoreBand(0.58, 0.50, 0.68),
    "esports_regular": ScoreBand(0.60, 0.54, 0.68),
    "esports_playoffs": ScoreBand(0.70, 0.66, 0.76),
    "esports_final": ScoreBand(0.76, 0.71, 0.82),
    "worlds_regular": ScoreBand(0.68, 0.62, 0.74),
    "worlds_key": ScoreBand(0.80, 0.74, 0.86),
    "roster_transfer": ScoreBand(0.62, 0.54, 0.74),
    "skin_release": ScoreBand(0.68, 0.60, 0.80),
    "service_incident": ScoreBand(0.68, 0.54, 0.86),
    "disciplinary_action": ScoreBand(0.76, 0.66, 0.88),
    "security_notice": ScoreBand(0.82, 0.72, 0.92),
    "merch_release": ScoreBand(0.52, 0.44, 0.64),
    "partnership": ScoreBand(0.58, 0.50, 0.68),
    "media_release": ScoreBand(0.58, 0.48, 0.70),
    "riot_corporate": ScoreBand(0.66, 0.58, 0.76),
    "lol_universe": ScoreBand(0.66, 0.58, 0.78),
    "community_event": ScoreBand(0.52, 0.42, 0.64),
    "gameplay_guide": ScoreBand(0.42, 0.32, 0.52),
    "community": ScoreBand(0.34, 0.16, 0.48),
    "other": ScoreBand(0.50, 0.30, 0.66),
}

_FULL_PREVIEW = re.compile(r"full\s+preview|完整预览", re.IGNORECASE)
_RARE_COSMETIC = re.compile(r"稀有|限定|绝版|rare|limited|神话炫彩", re.IGNORECASE)
_COSMETIC = re.compile(r"炫彩|皮肤|chroma|skin", re.IGNORECASE)
_BULK_REFRESH = re.compile(
    r"大量|批量|上新|版本更新|全新内容|多款|multiple|refresh",
    re.IGNORECASE,
)
_CN_PAID_CHROMA = re.compile(r"臻彩", re.IGNORECASE)
_EXPLICIT_PRESTIGE_OR_MYTHIC = re.compile(
    r"至臻|神话(?:级)?(?:皮肤|炫彩)|prestige\s+(?:skin|chroma)"
    r"|mythic\s+(?:skin|chroma)",
    re.IGNORECASE,
)


class EditorialImportanceAnalysis(TypedDict):
    scale: ImportanceScale
    audience_region: AudienceRegion
    competition_region: CompetitionRegion
    prominence: Prominence
    skin_tier: SkinTier
    is_bulk_update: bool
    evidence: list[str]


_SUBTOPIC_SUBTYPES: Final[dict[str, ImportanceSubtype]] = {
    "patch_notes": "patch_official_notes",
    "hotfix": "patch_hotfix",
    "pbe_change": "pbe_change",
    "champion_release": "new_champion",
    "champion_update": "major_gameplay_change",
    "item_rune_system": "major_gameplay_change",
    "game_mode_release": "new_game_mode",
    "game_mode_update": "major_gameplay_change",
    "tft_set": "tft_set_update",
    "tft_patch": "tft_set_update",
    "tft_cosmetic": "tft_cosmetic_release",
    "skin_release": "skin_release",
    "shop_offer": "shop_standard_offer",
    "free_rotation": "free_champion_rotation",
    "free_reward": "free_reward",
    "event_pass": "activity_paid",
    "in_game_activity": "activity_standard",
    "ticketing": "esports_ticketing",
    "match_schedule": "esports_schedule",
    "roster_move": "roster_transfer",
    "maintenance": "service_incident",
    "outage": "service_incident",
    "disciplinary": "disciplinary_action",
    "security": "security_notice",
    "merch": "merch_release",
    "partnership": "partnership",
    "corporate": "riot_corporate",
    "lore": "lol_universe",
    "music_video": "media_release",
    "community_event": "community_event",
    "gameplay_guide": "gameplay_guide",
    "community_post": "community",
}
_WORLD_EVENT = re.compile(r"worlds|全球总决赛|世界赛", re.IGNORECASE)
_FINAL = re.compile(r"总决赛|决赛|\bfinals?\b", re.IGNORECASE)
_PLAYOFF = re.compile(r"季后赛|淘汰赛|半决赛|playoffs?|semifinals?", re.IGNORECASE)
_FREE_SKIN = re.compile(
    r"(?:免费|无偿|必得|可领|领取|领到|获得|兑换|解锁|开箱)"
    r".{0,16}(?:随机|限定|至臻|神话)?皮肤"
    r"|皮肤.{0,16}(?:免费|无偿|必得|可领|可以领取|开放领取|领取|领到|获得|兑换|开箱)"
    r"|free.{0,16}skin|skin.{0,16}(?:claim|reward|unbox)",
    re.IGNORECASE,
)
_LOTTERY = re.compile(r"抽奖|概率|有机会|召唤", re.IGNORECASE)


def derive_importance_subtype(
    *,
    primary_topic: str,
    subtopic: str,
    content: str,
    source_kind: str = "first_party",
) -> ImportanceSubtype:
    """Map controlled classification to an editorial policy band."""
    if subtopic in OFFICIAL_ONLY_UPDATE_SUBTOPICS and source_kind != "first_party":
        return "pbe_change"
    if subtopic == "shop_rotation":
        if _RARE_COSMETIC.search(content):
            return "shop_rare_cosmetic"
        if _BULK_REFRESH.search(content):
            return "shop_bulk_refresh"
        if _COSMETIC.search(content):
            return "shop_cosmetic_rotation"
        return "shop_daily_standard"
    if subtopic == "patch_preview":
        return "patch_full_preview" if _FULL_PREVIEW.search(content) else "patch_preview"
    if subtopic == "match_result":
        if _WORLD_EVENT.search(content):
            return (
                "worlds_key"
                if _FINAL.search(content) or _PLAYOFF.search(content)
                else "worlds_regular"
            )
        if _FINAL.search(content):
            return "esports_final"
        if _PLAYOFF.search(content):
            return "esports_playoffs"
        return "esports_regular"
    if (
        subtopic in {"in_game_activity", "free_reward"}
        and _FREE_SKIN.search(content)
        and not _LOTTERY.search(content)
    ):
        return "activity_free_skin"
    if subtype := _SUBTOPIC_SUBTYPES.get(subtopic):
        return subtype
    return {
        "community": "community",
        "universe": "lol_universe",
        "business": "riot_corporate",
        "media": "media_release",
        "activity": "activity_standard",
        "service": "service_incident",
        "roster": "roster_transfer",
        "skin": "skin_release",
    }.get(primary_topic, "other")


def _modifier(key: str, value: float, evidence: str) -> dict[str, object]:
    return {"key": key, "value": round(value, 4), "evidence": evidence}


def normalize_importance_analysis(
    analysis: EditorialImportanceAnalysis,
    *,
    primary_topic: str,
    subtopic: str,
    content: str,
    source_kind: str = "first_party",
) -> EditorialImportanceAnalysis:
    normalized = dict(analysis)
    subtype = derive_importance_subtype(
        primary_topic=primary_topic,
        subtopic=subtopic,
        content=content,
        source_kind=source_kind,
    )
    if subtype not in {"skin_release", "tft_cosmetic_release"}:
        normalized["skin_tier"] = "none"
    elif (
        normalized["skin_tier"] == "prestige_or_mythic"
        and _CN_PAID_CHROMA.search(content)
        and not _EXPLICIT_PRESTIGE_OR_MYTHIC.search(content)
    ):
        normalized["skin_tier"] = "standard"
    return cast(EditorialImportanceAnalysis, normalized)


def calculate_importance(
    analysis: EditorialImportanceAnalysis,
    *,
    primary_topic: str,
    subtopic: str,
    content: str = "",
    source_kind: str = "first_party",
) -> tuple[float, dict[str, object]]:
    """Calculate stable intrinsic importance from content impact only."""
    analysis = normalize_importance_analysis(
        analysis,
        primary_topic=primary_topic,
        subtopic=subtopic,
        content=content,
        source_kind=source_kind,
    )
    subtype = derive_importance_subtype(
        primary_topic=primary_topic,
        subtopic=subtopic,
        content=content,
        source_kind=source_kind,
    )
    band = SCORE_BANDS[subtype]
    modifiers: list[dict[str, object]] = []
    scale = analysis["scale"]
    if subtype in {"patch_hotfix", "service_incident", "security_notice"}:
        scale_delta = {"minor": -0.07, "standard": 0.0, "major": 0.09}[scale]
    else:
        scale_delta = {"minor": -0.03, "standard": 0.0, "major": 0.03}[scale]
    if scale_delta:
        modifiers.append(_modifier("scale", scale_delta, f"内容规模识别为 {scale}"))
    if analysis["is_bulk_update"] and subtype not in {
        "shop_bulk_refresh",
        "patch_full_preview",
        "patch_official_notes",
    }:
        modifiers.append(_modifier("bulk_update", 0.03, "包含批量新增或大量改动"))
    if subtype.startswith("esports_") or subtype.startswith("worlds_"):
        region_delta = {
            "lpl": 0.03,
            "lck": 0.0,
            "international": 0.01,
            "other": -0.03,
            "none": -0.03,
        }[analysis["competition_region"]]
        if region_delta:
            modifiers.append(
                _modifier(
                    "competition_region",
                    region_delta,
                    f"赛事赛区为 {analysis['competition_region']}",
                )
            )
    if subtype in {
        "esports_regular",
        "esports_playoffs",
        "esports_final",
        "worlds_regular",
        "worlds_key",
        "roster_transfer",
    }:
        prominence_delta = {"normal": 0.0, "notable": 0.03, "star": 0.07}[analysis["prominence"]]
        if prominence_delta:
            modifiers.append(
                _modifier(
                    "prominence",
                    prominence_delta,
                    f"涉及对象知名度为 {analysis['prominence']}",
                )
            )
    if subtype in {"skin_release", "tft_cosmetic_release"}:
        skin_tier_delta = {
            "none": 0.0,
            "standard": 0.0,
            "legendary": 0.04,
            "prestige_or_mythic": 0.06,
            "ultimate": 0.10,
        }[analysis["skin_tier"]]
        if skin_tier_delta:
            modifiers.append(
                _modifier(
                    "skin_tier",
                    skin_tier_delta,
                    f"外观档次为 {analysis['skin_tier']}",
                )
            )
    modifier_total = sum(float(item["value"]) for item in modifiers)
    pre_clamp = band.base + modifier_total
    final = round(max(band.floor, min(band.cap, pre_clamp)), 4)
    return final, {
        "policy_version": IMPORTANCE_POLICY_VERSION,
        "score_kind": "intrinsic_importance",
        "editorial_subtype": subtype,
        "base_score": band.base,
        "score_band": {"floor": band.floor, "cap": band.cap},
        "modifiers": modifiers,
        "modifier_total": round(modifier_total, 4),
        "pre_clamp_score": round(pre_clamp, 4),
        "final_score": final,
    }


def calculate_message_priority(
    intrinsic_score: float,
    *,
    information_stage: str,
    content_form: str,
    audience_region: str,
) -> tuple[float, dict[str, object]]:
    """Project intrinsic value into a feed priority without claiming history knowledge."""
    modifiers: list[dict[str, object]] = []
    stage_delta = {
        "correction": 0.04,
        "result": 0.02,
        "active": 0.01,
        "announcement": 0.0,
        "preview": 0.0,
        "update": 0.0,
        "rumor": -0.02,
        "speculation": -0.06,
        "reminder": -0.12,
        "commentary": -0.16,
    }.get(information_stage, 0.0)
    if stage_delta:
        modifiers.append(
            _modifier(
                "information_stage",
                stage_delta,
                f"信息阶段为 {information_stage}",
            )
        )
    form_delta = {"repost": -0.08, "roundup": -0.04}.get(content_form, 0.0)
    if form_delta:
        modifiers.append(_modifier("content_form", form_delta, f"内容形式为 {content_form}"))
    if audience_region == "international_only":
        modifiers.append(_modifier("audience_region", -0.12, "明确仅影响非国服受众"))
    total = sum(float(item["value"]) for item in modifiers)
    final = round(max(0.05, min(1.0, intrinsic_score + total)), 4)
    return final, {
        "policy_version": IMPORTANCE_POLICY_VERSION,
        "score_kind": "message_priority",
        "intrinsic_score": intrinsic_score,
        "modifiers": modifiers,
        "modifier_total": round(total, 4),
        "final_score": final,
        "novelty_basis": "current-message-signals-only",
    }
