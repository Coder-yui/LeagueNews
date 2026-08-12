from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final, Literal, TypedDict, cast

from app.domain.message_taxonomy import MESSAGE_TYPES, TOPICS, MessageType, Topic


IMPORTANCE_POLICY_VERSION: Final = "importance-v11-repost-weekly-rotation"

ImportanceScale = Literal["minor", "standard", "major"]
CompetitionRegion = Literal["none", "lpl", "lck", "international", "other"]
Prominence = Literal["normal", "notable", "star"]
AudienceRegion = Literal["cn", "global", "international_only", "unknown"]
SkinTier = Literal["none", "standard", "legendary", "prestige_or_mythic", "ultimate"]

ImportanceProfile = Literal[
    "unknown",
    "patch_official_notes",
    "patch_full_preview",
    "official_gameplay_preview",
    "official_content_preview",
    "gameplay_announcement",
    "tft_announcement",
    "activity_announcement",
    "cosmetic_announcement",
    "commerce_announcement",
    "weekly_free_champion_rotation",
    "game_announcement_general",
    "patch_hotfix",
    "service_notice",
    "security_notice",
    "promotion_gameplay",
    "promotion_activity",
    "promotion_cosmetic",
    "promotion_community",
    "promotion_general",
    "shop_daily_standard",
    "shop_cosmetic_rotation",
    "shop_rare_cosmetic",
    "shop_bulk_refresh",
    "free_reward",
    "activity_free_skin",
    "community_game_notice",
    "community_service_notice",
    "community_notice_general",
    "leak_gameplay",
    "leak_content",
    "leak_general",
    "gameplay_guide",
    "game_discussion",
    "esports_schedule",
    "esports_regular",
    "esports_playoffs",
    "esports_final",
    "worlds_regular",
    "worlds_key",
    "roster_announcement",
    "esports_announcement_general",
    "esports_promotion",
    "esports_rumor",
    "esports_analysis",
    "esports_discussion",
    "universe_announcement",
    "universe_promotion",
    "universe_leak",
    "universe_discussion",
    "other_product_announcement",
    "other_product_promotion",
    "other_product_leak",
    "other_product_discussion",
    "merch_release",
    "partnership",
    "media_release",
    "riot_announcement",
    "riot_promotion",
    "riot_leak",
    "riot_discussion",
]

TopicFamily = Literal[
    "gameplay",
    "tft",
    "service",
    "security",
    "cosmetics",
    "commerce",
    "activity",
    "community",
    "guide",
    "esports_competition",
    "esports_schedule",
    "esports_matches",
    "esports_rosters",
    "esports_analysis",
    "esports_media",
    "universe",
    "media",
    "merchandise",
    "corporate",
    "platform",
    "unknown",
]


@dataclass(frozen=True, slots=True)
class ScoreBand:
    base: float
    floor: float
    cap: float


@dataclass(frozen=True, slots=True)
class ProfileRoute:
    families: frozenset[TopicFamily]
    profile: ImportanceProfile


@dataclass(frozen=True, slots=True)
class DomainImportanceResult:
    profile: ImportanceProfile
    score: float
    features: DomainImportanceFeatures
    band: ScoreBand
    modifiers: tuple[dict[str, object], ...]
    modifier_total: float
    pre_clamp_score: float


class DomainImportanceFeatures(TypedDict):
    scale: ImportanceScale
    competition_region: CompetitionRegion
    prominence: Prominence
    skin_tier: SkinTier
    is_bulk_update: bool


class ImportanceFeatures(DomainImportanceFeatures):
    audience_region: AudienceRegion
    evidence: list[str]


SCORE_BANDS: Final[dict[ImportanceProfile, ScoreBand]] = {
    "unknown": ScoreBand(0.0, 0.0, 0.0),
    "patch_official_notes": ScoreBand(0.92, 0.88, 0.95),
    "patch_full_preview": ScoreBand(0.90, 0.87, 0.93),
    "official_gameplay_preview": ScoreBand(0.86, 0.80, 0.93),
    "official_content_preview": ScoreBand(0.76, 0.66, 0.84),
    "gameplay_announcement": ScoreBand(0.86, 0.78, 0.95),
    "tft_announcement": ScoreBand(0.82, 0.74, 0.90),
    "activity_announcement": ScoreBand(0.72, 0.62, 0.82),
    "cosmetic_announcement": ScoreBand(0.68, 0.58, 0.80),
    "commerce_announcement": ScoreBand(0.58, 0.48, 0.70),
    "weekly_free_champion_rotation": ScoreBand(0.50, 0.44, 0.56),
    "game_announcement_general": ScoreBand(0.62, 0.52, 0.76),
    "patch_hotfix": ScoreBand(0.72, 0.62, 0.84),
    "service_notice": ScoreBand(0.68, 0.54, 0.86),
    "security_notice": ScoreBand(0.82, 0.72, 0.92),
    "promotion_gameplay": ScoreBand(0.52, 0.38, 0.68),
    "promotion_activity": ScoreBand(0.50, 0.36, 0.66),
    "promotion_cosmetic": ScoreBand(0.48, 0.34, 0.64),
    "promotion_community": ScoreBand(0.34, 0.16, 0.50),
    "promotion_general": ScoreBand(0.42, 0.26, 0.58),
    "shop_daily_standard": ScoreBand(0.48, 0.42, 0.54),
    "shop_cosmetic_rotation": ScoreBand(0.58, 0.52, 0.64),
    "shop_rare_cosmetic": ScoreBand(0.66, 0.60, 0.72),
    "shop_bulk_refresh": ScoreBand(0.66, 0.60, 0.72),
    "free_reward": ScoreBand(0.62, 0.52, 0.75),
    "activity_free_skin": ScoreBand(0.84, 0.78, 0.90),
    "community_game_notice": ScoreBand(0.54, 0.42, 0.68),
    "community_service_notice": ScoreBand(0.58, 0.46, 0.72),
    "community_notice_general": ScoreBand(0.46, 0.34, 0.60),
    "leak_gameplay": ScoreBand(0.62, 0.50, 0.75),
    "leak_content": ScoreBand(0.54, 0.42, 0.68),
    "leak_general": ScoreBand(0.50, 0.38, 0.64),
    "gameplay_guide": ScoreBand(0.42, 0.32, 0.52),
    "game_discussion": ScoreBand(0.34, 0.16, 0.55),
    "esports_schedule": ScoreBand(0.52, 0.46, 0.62),
    "esports_regular": ScoreBand(0.60, 0.54, 0.68),
    "esports_playoffs": ScoreBand(0.70, 0.66, 0.76),
    "esports_final": ScoreBand(0.76, 0.71, 0.82),
    "worlds_regular": ScoreBand(0.68, 0.62, 0.74),
    "worlds_key": ScoreBand(0.80, 0.74, 0.86),
    "roster_announcement": ScoreBand(0.62, 0.54, 0.74),
    "esports_announcement_general": ScoreBand(0.56, 0.44, 0.70),
    "esports_promotion": ScoreBand(0.44, 0.28, 0.62),
    "esports_rumor": ScoreBand(0.50, 0.38, 0.68),
    "esports_analysis": ScoreBand(0.44, 0.30, 0.60),
    "esports_discussion": ScoreBand(0.34, 0.18, 0.54),
    "universe_announcement": ScoreBand(0.66, 0.58, 0.78),
    "universe_promotion": ScoreBand(0.46, 0.32, 0.62),
    "universe_leak": ScoreBand(0.54, 0.42, 0.68),
    "universe_discussion": ScoreBand(0.38, 0.22, 0.56),
    "other_product_announcement": ScoreBand(0.68, 0.56, 0.82),
    "other_product_promotion": ScoreBand(0.46, 0.32, 0.62),
    "other_product_leak": ScoreBand(0.54, 0.42, 0.70),
    "other_product_discussion": ScoreBand(0.38, 0.22, 0.56),
    "merch_release": ScoreBand(0.52, 0.44, 0.64),
    "partnership": ScoreBand(0.58, 0.50, 0.68),
    "media_release": ScoreBand(0.58, 0.48, 0.70),
    "riot_announcement": ScoreBand(0.66, 0.58, 0.76),
    "riot_promotion": ScoreBand(0.46, 0.32, 0.62),
    "riot_leak": ScoreBand(0.54, 0.42, 0.68),
    "riot_discussion": ScoreBand(0.38, 0.22, 0.56),
}


TOPIC_FAMILIES: Final[dict[Topic, TopicFamily]] = {
    "balance_gameplay": "gameplay",
    "champions": "gameplay",
    "items_runes_systems": "gameplay",
    "game_modes": "gameplay",
    "gameplay": "gameplay",
    "service_technical": "service",
    "cosmetics": "cosmetics",
    "shop_monetization": "commerce",
    "activities_rewards": "activity",
    "security_fair_play": "security",
    "community": "community",
    "guides_education": "guide",
    "tft_gameplay": "tft",
    "esports_competition": "esports_competition",
    "esports_schedule": "esports_schedule",
    "esports_matches": "esports_matches",
    "esports_rosters": "esports_rosters",
    "esports_analysis": "esports_analysis",
    "esports_broadcast": "esports_media",
    "esports_fandom_live": "esports_media",
    "lore_universe": "universe",
    "media_entertainment": "media",
    "merchandise_collectibles": "merchandise",
    "corporate_partnerships": "corporate",
    "platform_services": "platform",
    "unknown": "unknown",
}


def _route(families: set[TopicFamily], profile: ImportanceProfile) -> ProfileRoute:
    return ProfileRoute(frozenset(families), profile)


_GAME_PROMOTION_ROUTES = (
    _route({"activity", "commerce"}, "promotion_activity"),
    _route({"cosmetics"}, "promotion_cosmetic"),
    _route({"gameplay", "tft"}, "promotion_gameplay"),
    _route({"community", "guide", "media"}, "promotion_community"),
    _route(set(TOPIC_FAMILIES.values()), "promotion_general"),
)


PROFILE_ROUTES: Final[dict[MessageType, tuple[ProfileRoute, ...]]] = {
    "game_patch_notes": (_route(set(TOPIC_FAMILIES.values()), "patch_official_notes"),),
    "game_official_preview": (
        _route({"gameplay", "tft"}, "official_gameplay_preview"),
        _route(set(TOPIC_FAMILIES.values()), "official_content_preview"),
    ),
    "game_announcement": (
        _route({"security"}, "security_notice"),
        _route({"service"}, "service_notice"),
        _route({"activity"}, "activity_announcement"),
        _route({"commerce"}, "commerce_announcement"),
        _route({"cosmetics"}, "cosmetic_announcement"),
        _route({"tft"}, "tft_announcement"),
        _route({"gameplay"}, "gameplay_announcement"),
        _route(set(TOPIC_FAMILIES.values()), "game_announcement_general"),
    ),
    "game_notice": (
        _route({"security"}, "security_notice"),
        _route(set(TOPIC_FAMILIES.values()), "service_notice"),
    ),
    "game_promotion_interaction": _GAME_PROMOTION_ROUTES,
    "game_community_notice": (
        _route({"commerce"}, "shop_daily_standard"),
        _route({"activity"}, "free_reward"),
        _route({"service", "security"}, "community_service_notice"),
        _route({"gameplay", "tft", "cosmetics"}, "community_game_notice"),
        _route(set(TOPIC_FAMILIES.values()), "community_notice_general"),
    ),
    "game_community_promotion_interaction": _GAME_PROMOTION_ROUTES,
    "game_leak": (
        _route({"gameplay", "tft", "service", "security"}, "leak_gameplay"),
        _route({"cosmetics", "commerce", "activity"}, "leak_content"),
        _route(set(TOPIC_FAMILIES.values()), "leak_general"),
    ),
    "game_community_discussion": (
        _route({"guide"}, "gameplay_guide"),
        _route(set(TOPIC_FAMILIES.values()), "game_discussion"),
    ),
    "esports_announcement": (
        _route({"esports_matches"}, "esports_regular"),
        _route({"esports_schedule"}, "esports_schedule"),
        _route({"esports_rosters"}, "roster_announcement"),
        _route(set(TOPIC_FAMILIES.values()), "esports_announcement_general"),
    ),
    "esports_promotion_interaction": (
        _route(set(TOPIC_FAMILIES.values()), "esports_promotion"),
    ),
    "esports_rumor_speculation": (
        _route(set(TOPIC_FAMILIES.values()), "esports_rumor"),
    ),
    "esports_community_discussion": (
        _route({"esports_analysis"}, "esports_analysis"),
        _route(set(TOPIC_FAMILIES.values()), "esports_discussion"),
    ),
    "lol_universe_announcement": (
        _route(set(TOPIC_FAMILIES.values()), "universe_announcement"),
    ),
    "lol_universe_promotion_interaction": (
        _route(set(TOPIC_FAMILIES.values()), "universe_promotion"),
    ),
    "lol_universe_leak": (
        _route(set(TOPIC_FAMILIES.values()), "universe_leak"),
    ),
    "lol_universe_community_discussion": (
        _route(set(TOPIC_FAMILIES.values()), "universe_discussion"),
    ),
    "other_lol_product_announcement": (
        _route(set(TOPIC_FAMILIES.values()), "other_product_announcement"),
    ),
    "other_lol_product_promotion_interaction": (
        _route(set(TOPIC_FAMILIES.values()), "other_product_promotion"),
    ),
    "other_lol_product_leak": (
        _route(set(TOPIC_FAMILIES.values()), "other_product_leak"),
    ),
    "other_lol_product_community_discussion": (
        _route(set(TOPIC_FAMILIES.values()), "other_product_discussion"),
    ),
    "riot_ecosystem_announcement": (
        _route({"merchandise"}, "merch_release"),
        _route({"corporate"}, "partnership"),
        _route({"media"}, "media_release"),
        _route(set(TOPIC_FAMILIES.values()), "riot_announcement"),
    ),
    "riot_ecosystem_promotion_interaction": (
        _route(set(TOPIC_FAMILIES.values()), "riot_promotion"),
    ),
    "riot_ecosystem_leak": (
        _route(set(TOPIC_FAMILIES.values()), "riot_leak"),
    ),
    "riot_ecosystem_community_discussion": (
        _route(set(TOPIC_FAMILIES.values()), "riot_discussion"),
    ),
    "unknown": (_route(set(TOPIC_FAMILIES.values()), "unknown"),),
}


_HOTFIX_SIGNAL = re.compile(
    r"不停机.{0,8}(?:更新|维护)|(?:无需|不用)停机.{0,8}(?:更新|维护)|"
    r"热(?:更新|修复)|hotfix|micro\s*patch|server[-\s]?side\s+update",
    re.IGNORECASE,
)
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
_WEEKLY_FREE_CHAMPION = re.compile(
    r"周免(?:英雄)?|每周免费英雄|免费英雄(?:更新|轮换)|"
    r"weekly\s+free\s+champions?|free(?:-to-play)?\s+champion\s+rotation",
    re.IGNORECASE,
)


def has_hotfix_signal(value: str) -> bool:
    return _HOTFIX_SIGNAL.search(value) is not None


def _topic_families(topics: list[str]) -> frozenset[TopicFamily]:
    invalid = set(topics) - TOPICS
    if invalid:
        raise ValueError(f"unsupported importance topics: {sorted(invalid)}")
    return frozenset(TOPIC_FAMILIES[cast(Topic, topic)] for topic in topics)


def _shop_profile(content: str) -> ImportanceProfile:
    if _RARE_COSMETIC.search(content):
        return "shop_rare_cosmetic"
    if _BULK_REFRESH.search(content):
        return "shop_bulk_refresh"
    if _COSMETIC.search(content):
        return "shop_cosmetic_rotation"
    return "shop_daily_standard"


def _match_profile(content: str) -> ImportanceProfile:
    if _WORLD_EVENT.search(content):
        return "worlds_key" if _FINAL.search(content) or _PLAYOFF.search(content) else "worlds_regular"
    if _FINAL.search(content):
        return "esports_final"
    if _PLAYOFF.search(content):
        return "esports_playoffs"
    return "esports_regular"


def derive_importance_profile(
    *,
    message_type: str,
    topics: list[str],
    content: str,
) -> ImportanceProfile:
    """Resolve one scoring profile directly from the approved message taxonomy."""
    if message_type not in MESSAGE_TYPES:
        raise ValueError(f"unsupported importance message_type: {message_type}")
    if not topics:
        raise ValueError("importance topics must not be empty")
    typed_message_type = cast(MessageType, message_type)
    families = _topic_families(topics)

    if typed_message_type == "unknown":
        return "unknown"
    if typed_message_type == "game_notice" and has_hotfix_signal(content):
        return "patch_hotfix"
    if typed_message_type == "game_official_preview" and _FULL_PREVIEW.search(content):
        return "patch_full_preview"
    if (
        typed_message_type == "game_announcement"
        and "gameplay" in families
        and _WEEKLY_FREE_CHAMPION.search(content)
    ):
        return "weekly_free_champion_rotation"
    if typed_message_type == "game_community_notice":
        if "commerce" in families:
            return _shop_profile(content)
        if "activity" in families and _FREE_SKIN.search(content) and not _LOTTERY.search(content):
            return "activity_free_skin"
    if typed_message_type == "esports_announcement" and "esports_matches" in families:
        return _match_profile(content)

    for route in PROFILE_ROUTES[typed_message_type]:
        if families & route.families:
            return route.profile
    raise ValueError(
        f"no importance profile for message_type={message_type}, topics={topics}"
    )


def normalize_importance_features(
    features: DomainImportanceFeatures,
    *,
    profile: ImportanceProfile,
    content: str,
) -> DomainImportanceFeatures:
    normalized = dict(features)
    if profile not in {"cosmetic_announcement", "promotion_cosmetic"}:
        normalized["skin_tier"] = "none"
    elif (
        normalized["skin_tier"] == "prestige_or_mythic"
        and _CN_PAID_CHROMA.search(content)
        and not _EXPLICIT_PRESTIGE_OR_MYTHIC.search(content)
    ):
        normalized["skin_tier"] = "standard"
    return cast(DomainImportanceFeatures, normalized)


def _modifier(key: str, value: float, evidence: str) -> dict[str, object]:
    return {"key": key, "value": round(value, 4), "evidence": evidence}


def score_importance_profile(
    profile: ImportanceProfile,
    features: DomainImportanceFeatures,
    *,
    content: str = "",
) -> DomainImportanceResult:
    """Score one controlled profile with the shared domain policy."""
    features = normalize_importance_features(
        features,
        profile=profile,
        content=content,
    )
    band = SCORE_BANDS[profile]
    modifiers: list[dict[str, object]] = []
    scale = features["scale"]
    if profile in {"patch_hotfix", "service_notice", "security_notice"}:
        scale_delta = {"minor": -0.07, "standard": 0.0, "major": 0.09}[scale]
    else:
        scale_delta = {"minor": -0.03, "standard": 0.0, "major": 0.03}[scale]
    if scale_delta:
        modifiers.append(_modifier("scale", scale_delta, f"内容规模识别为 {scale}"))
    if features["is_bulk_update"] and profile not in {
        "shop_bulk_refresh",
        "patch_full_preview",
        "patch_official_notes",
        "weekly_free_champion_rotation",
    }:
        modifiers.append(_modifier("bulk_update", 0.03, "包含批量新增或大量改动"))
    if profile.startswith("esports_") or profile.startswith("worlds_"):
        region_delta = {
            "lpl": 0.03,
            "lck": 0.0,
            "international": 0.01,
            "other": -0.03,
            "none": -0.03,
        }[features["competition_region"]]
        if region_delta:
            modifiers.append(
                _modifier(
                    "competition_region",
                    region_delta,
                    f"赛事赛区为 {features['competition_region']}",
                )
            )
    if profile in {
        "esports_regular",
        "esports_playoffs",
        "esports_final",
        "worlds_regular",
        "worlds_key",
        "roster_announcement",
        "esports_rumor",
    }:
        prominence_delta = {"normal": 0.0, "notable": 0.03, "star": 0.07}[
            features["prominence"]
        ]
        if prominence_delta:
            modifiers.append(
                _modifier(
                    "prominence",
                    prominence_delta,
                    f"涉及对象知名度为 {features['prominence']}",
                )
            )
    if profile in {"cosmetic_announcement", "promotion_cosmetic"}:
        skin_tier_delta = {
            "none": 0.0,
            "standard": 0.0,
            "legendary": 0.04,
            "prestige_or_mythic": 0.06,
            "ultimate": 0.10,
        }[features["skin_tier"]]
        if skin_tier_delta:
            modifiers.append(
                _modifier(
                    "skin_tier",
                    skin_tier_delta,
                    f"外观档次为 {features['skin_tier']}",
                )
            )
    profile_modifier_total = sum(float(item["value"]) for item in modifiers)
    pre_clamp = band.base + profile_modifier_total
    profile_score = round(max(band.floor, min(band.cap, pre_clamp)), 4)
    return DomainImportanceResult(
        profile=profile,
        score=profile_score,
        features=features,
        band=band,
        modifiers=tuple(modifiers),
        modifier_total=round(profile_modifier_total, 4),
        pre_clamp_score=round(pre_clamp, 4),
    )


def score_domain_importance(
    features: ImportanceFeatures,
    *,
    message_type: str,
    topics: list[str],
    content: str = "",
) -> DomainImportanceResult:
    """Derive and score the domain importance of a whole message."""
    profile = derive_importance_profile(
        message_type=message_type,
        topics=topics,
        content=content,
    )
    return score_importance_profile(profile, features, content=content)


def calculate_importance(
    features: ImportanceFeatures,
    *,
    message_type: str,
    topics: list[str],
    content_form: str = "original",
    content: str = "",
) -> tuple[float, dict[str, object]]:
    """Calculate message importance from domain score and message-only modifiers."""
    domain = score_domain_importance(
        features,
        message_type=message_type,
        topics=topics,
        content=content,
    )
    modifiers = list(domain.modifiers)
    if content_form == "repost":
        modifiers.append(_modifier("content_form", -0.08, "内容形式为 repost"))
    modifier_total = sum(float(item["value"]) for item in modifiers)
    content_form_delta = modifier_total - domain.modifier_total
    final = round(max(0.0, min(1.0, domain.score + content_form_delta)), 4)
    return final, {
        "policy_version": IMPORTANCE_POLICY_VERSION,
        "score_kind": "message_importance",
        "importance_profile": domain.profile,
        "message_type": message_type,
        "topics": topics,
        "base_score": domain.band.base,
        "score_band": {"floor": domain.band.floor, "cap": domain.band.cap},
        "modifiers": modifiers,
        "modifier_total": round(modifier_total, 4),
        "profile_modifier_total": domain.modifier_total,
        "pre_clamp_score": domain.pre_clamp_score,
        "profile_score": domain.score,
        "final_score": final,
    }


def calculate_message_priority(
    importance_score: float,
    *,
    content_form: str,
    audience_region: str,
) -> tuple[float, dict[str, object]]:
    """Adjust message importance only for feed delivery constraints."""
    modifiers: list[dict[str, object]] = []
    form_delta = {"quote": -0.02}.get(content_form, 0.0)
    if form_delta:
        modifiers.append(_modifier("content_form", form_delta, f"内容形式为 {content_form}"))
    if audience_region == "international_only":
        modifiers.append(_modifier("audience_region", -0.12, "明确仅影响非国服受众"))
    total = sum(float(item["value"]) for item in modifiers)
    final = round(max(0.0, min(1.0, importance_score + total)), 4)
    return final, {
        "policy_version": IMPORTANCE_POLICY_VERSION,
        "score_kind": "message_priority",
        "importance_score": importance_score,
        "modifiers": modifiers,
        "modifier_total": round(total, 4),
        "final_score": final,
    }
