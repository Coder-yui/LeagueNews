from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal, TypedDict

IMPORTANCE_POLICY_VERSION: Final = "importance-v3-editorial-baselines"

ImportanceSubtype = Literal[
    "shop_daily_standard",
    "shop_cosmetic_rotation",
    "shop_rare_cosmetic",
    "shop_bulk_refresh",
    "patch_preview",
    "patch_full_preview",
    "patch_official_notes",
    "patch_hotfix",
    "new_champion",
    "new_game_mode",
    "major_gameplay_change",
    "activity_paid",
    "activity_standard",
    "activity_free_skin",
    "riot_corporate",
    "lol_universe",
    "esports_regular",
    "esports_playoffs",
    "esports_final",
    "worlds_regular",
    "worlds_key",
    "roster_transfer",
    "skin_release",
    "service_incident",
    "community",
    "other",
]
ImportanceScale = Literal["minor", "standard", "major"]
CompetitionRegion = Literal["none", "lpl", "lck", "international", "other"]
Prominence = Literal["normal", "notable", "star"]
AudienceRegion = Literal["cn", "global", "international_only", "unknown"]
SkinTier = Literal[
    "none",
    "standard",
    "legendary",
    "prestige_or_mythic",
    "ultimate",
]


@dataclass(frozen=True, slots=True)
class ScoreBand:
    base: float
    floor: float
    cap: float


SCORE_BANDS: Final[dict[ImportanceSubtype, ScoreBand]] = {
    "shop_daily_standard": ScoreBand(0.50, 0.45, 0.55),
    "shop_cosmetic_rotation": ScoreBand(0.60, 0.56, 0.64),
    "shop_rare_cosmetic": ScoreBand(0.66, 0.62, 0.70),
    "shop_bulk_refresh": ScoreBand(0.66, 0.60, 0.70),
    "patch_preview": ScoreBand(0.87, 0.84, 0.90),
    "patch_full_preview": ScoreBand(0.90, 0.87, 0.93),
    "patch_official_notes": ScoreBand(0.92, 0.87, 0.95),
    "patch_hotfix": ScoreBand(0.77, 0.70, 0.85),
    "new_champion": ScoreBand(0.93, 0.87, 0.95),
    "new_game_mode": ScoreBand(0.92, 0.87, 0.95),
    "major_gameplay_change": ScoreBand(0.92, 0.87, 0.95),
    "activity_paid": ScoreBand(0.75, 0.70, 0.80),
    "activity_standard": ScoreBand(0.78, 0.70, 0.85),
    "activity_free_skin": ScoreBand(0.90, 0.86, 0.94),
    "riot_corporate": ScoreBand(0.65, 0.60, 0.70),
    "lol_universe": ScoreBand(0.65, 0.60, 0.70),
    "esports_regular": ScoreBand(0.60, 0.55, 0.68),
    "esports_playoffs": ScoreBand(0.70, 0.68, 0.75),
    "esports_final": ScoreBand(0.73, 0.70, 0.76),
    "worlds_regular": ScoreBand(0.66, 0.60, 0.70),
    "worlds_key": ScoreBand(0.75, 0.70, 0.80),
    "roster_transfer": ScoreBand(0.60, 0.55, 0.72),
    "skin_release": ScoreBand(0.70, 0.68, 0.80),
    "service_incident": ScoreBand(0.66, 0.55, 0.85),
    "community": ScoreBand(0.40, 0.20, 0.55),
    "other": ScoreBand(0.55, 0.35, 0.70),
}

_FULL_PREVIEW = re.compile(r"full\s+preview|完整预览", re.IGNORECASE)
_PATCH_PREVIEW = re.compile(r"patch\s+preview|版本(?:改动)?预览", re.IGNORECASE)
_PATCH_NOTES = re.compile(
    r"patch\s+notes|版本更新公告|完整(?:版本)?更新",
    re.IGNORECASE,
)
_HOTFIX = re.compile(
    r"hotfix|micropatch|热修复|微补丁|不停机更新|临时更新",
    re.IGNORECASE,
)
_MYTHIC_SHOP = re.compile(r"神话商(?:城|店)|mythic\s+shop", re.IGNORECASE)
_ROTATION = re.compile(r"轮换|每日更新|每周更新|rotation", re.IGNORECASE)
_RARE_COSMETIC = re.compile(
    r"稀有|限定|绝版|rare|limited|神话炫彩",
    re.IGNORECASE,
)
_COSMETIC = re.compile(r"炫彩|皮肤|chroma|skin", re.IGNORECASE)
_BULK_REFRESH = re.compile(
    r"大量|批量|上新|版本更新|全新内容|多款|multiple|refresh",
    re.IGNORECASE,
)


class EditorialImportanceAnalysis(TypedDict):
    editorial_subtype: ImportanceSubtype
    scale: ImportanceScale
    audience_region: AudienceRegion
    competition_region: CompetitionRegion
    prominence: Prominence
    skin_tier: SkinTier
    is_bulk_update: bool
    is_first_concrete_disclosure: bool
    is_duplicate_or_reminder: bool
    evidence: list[str]


def _content_guardrail(
    subtype: ImportanceSubtype,
    *,
    primary_topic: str,
    content: str,
) -> ImportanceSubtype:
    if _MYTHIC_SHOP.search(content) and _ROTATION.search(content):
        if _RARE_COSMETIC.search(content):
            return "shop_rare_cosmetic"
        if _BULK_REFRESH.search(content):
            return "shop_bulk_refresh"
        if _COSMETIC.search(content):
            return "shop_cosmetic_rotation"
        return "shop_daily_standard"

    if primary_topic == "patch":
        if _FULL_PREVIEW.search(content):
            return "patch_full_preview"
        if _HOTFIX.search(content):
            return "patch_hotfix"
        if _PATCH_NOTES.search(content):
            return "patch_official_notes"
        if _PATCH_PREVIEW.search(content):
            return "patch_preview"
    return subtype


def _modifier(
    key: str,
    value: float,
    evidence: str,
) -> dict[str, object]:
    return {
        "key": key,
        "value": round(value, 4),
        "evidence": evidence,
    }


def calculate_importance(
    analysis: EditorialImportanceAnalysis,
    *,
    primary_topic: str,
    content: str = "",
) -> tuple[float, dict[str, object]]:
    requested_subtype = analysis["editorial_subtype"]
    subtype = _content_guardrail(
        requested_subtype,
        primary_topic=primary_topic,
        content=content,
    )
    band = SCORE_BANDS[subtype]
    modifiers: list[dict[str, object]] = []

    scale = analysis["scale"]
    if subtype == "patch_hotfix":
        scale_delta = {"minor": -0.07, "standard": 0.0, "major": 0.08}[scale]
    elif subtype == "service_incident":
        scale_delta = {"minor": -0.06, "standard": 0.0, "major": 0.10}[scale]
    else:
        scale_delta = {"minor": -0.03, "standard": 0.0, "major": 0.03}[scale]
    if scale_delta:
        modifiers.append(
            _modifier("scale", scale_delta, f"内容规模识别为 {scale}")
        )

    if (
        analysis["is_bulk_update"]
        and subtype
        not in {"shop_bulk_refresh", "patch_full_preview", "patch_official_notes"}
    ):
        modifiers.append(_modifier("bulk_update", 0.03, "包含批量新增或大量改动"))

    if analysis["is_first_concrete_disclosure"]:
        modifiers.append(
            _modifier("first_concrete_disclosure", 0.02, "首次披露具体可验证信息")
        )

    if subtype == "esports_regular":
        region_delta = {
            "lpl": 0.03,
            "lck": 0.0,
            "international": -0.01,
            "other": -0.03,
            "none": -0.03,
        }[analysis["competition_region"]]
        if region_delta:
            modifiers.append(
                _modifier(
                    "competition_region",
                    region_delta,
                    f"常规赛赛区为 {analysis['competition_region']}",
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
        prominence_delta = {
            "normal": 0.0,
            "notable": 0.03,
            "star": 0.07,
        }[analysis["prominence"]]
        if prominence_delta:
            modifiers.append(
                _modifier(
                    "prominence",
                    prominence_delta,
                    f"涉及对象知名度为 {analysis['prominence']}",
                )
            )

    if subtype == "skin_release":
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
                    f"皮肤档次为 {analysis['skin_tier']}",
                )
            )

    modifier_total = sum(float(item["value"]) for item in modifiers)
    pre_clamp = band.base + modifier_total
    band_score = max(band.floor, min(band.cap, pre_clamp))
    duplicate_penalty = 0.12 if analysis["is_duplicate_or_reminder"] else 0.0
    region_penalty = 0.0
    if analysis["audience_region"] == "international_only":
        if subtype.startswith("shop_"):
            region_penalty = 0.20
        elif subtype.startswith("activity_"):
            region_penalty = 0.15
        elif subtype in {
            "patch_preview",
            "patch_full_preview",
            "patch_official_notes",
            "patch_hotfix",
            "new_champion",
            "new_game_mode",
            "major_gameplay_change",
            "skin_release",
            "service_incident",
        }:
            region_penalty = 0.15
    final = round(
        max(0.05, band_score - duplicate_penalty - region_penalty),
        4,
    )
    return final, {
        "policy_version": IMPORTANCE_POLICY_VERSION,
        "requested_subtype": requested_subtype,
        "editorial_subtype": subtype,
        "base_score": band.base,
        "score_band": {"floor": band.floor, "cap": band.cap},
        "modifiers": modifiers,
        "modifier_total": round(modifier_total, 4),
        "pre_clamp_score": round(pre_clamp, 4),
        "band_score": round(band_score, 4),
        "audience_region": analysis["audience_region"],
        "region_penalty": region_penalty,
        "duplicate_penalty": duplicate_penalty,
        "final_score": final,
    }
