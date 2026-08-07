from __future__ import annotations

from typing import Final

from app.domain.importance import SCORE_BANDS, ImportanceSubtype
from app.models.normalized_item import NormalizedItem

EVENT_IMPORTANCE_POLICY_VERSION: Final = "event-importance-v5-component-baselines"

_EVENT_KIND_SUBTYPES: Final[dict[str, ImportanceSubtype]] = {
    "gameplay_update": "major_gameplay_change",
    "gameplay_release": "new_game_mode",
    "cosmetic_release": "skin_release",
    "roster_change": "roster_transfer",
    "esports_match": "esports_regular",
    "esports_schedule": "esports_schedule",
    "qualification_change": "esports_regular",
    "commercial_offer": "shop_standard_offer",
    "player_activity": "activity_standard",
    "service_incident": "service_incident",
    "disciplinary_action": "disciplinary_action",
    "security_notice": "security_notice",
    "media_release": "media_release",
    "corporate_announcement": "riot_corporate",
    "community_activity": "community_event",
    "other": "other",
}


def membership_importance_contribution(
    item: NormalizedItem,
    *,
    event_kind: str,
    membership_role: str,
    evidence_stance: str,
    update_kind: str,
) -> tuple[float, list[str]]:
    if evidence_stance == "context" or update_kind == "context":
        return 0.0, ["该成员仅提供上下文，不主张事件新事实"]
    event_baseline = SCORE_BANDS[
        _EVENT_KIND_SUBTYPES.get(event_kind, "other")
    ].base
    if membership_role == "primary":
        contribution_basis = item.importance_score
        role_factor = 1.0
    else:
        contribution_basis = min(item.importance_score, event_baseline)
        role_factor = 1.0 if membership_role == "component" else 0.35
    contribution = round(
        max(0.0, min(1.0, contribution_basis * role_factor)),
        4,
    )
    return contribution, [
        f"消息内在重要性={item.importance_score:.3f}",
        f"事件类型标准基准={event_baseline:.3f}",
        f"事件成员角色={membership_role}, 贡献基数={contribution_basis:.3f}",
        f"角色系数={role_factor:.2f}",
    ]


def calculate_event_importance(
    *,
    event_kind: str,
    aggregation_strategy: str,
    product_scope: str,
    aggregation_key: str | None,
    contributions: list[float],
    independent_source_count: int,
) -> tuple[float, dict[str, object], list[str]]:
    significant = [value for value in contributions if value > 0]
    evidence_signal = max(significant, default=0.0)
    market_reach_modifier = (
        -0.12
        if event_kind == "commercial_offer"
        and str(aggregation_key or "").startswith("shop_rotation:")
        and ":global:" in str(aggregation_key)
        else 0.0
    )
    score = round(
        max(0.05, min(1.0, evidence_signal + market_reach_modifier)),
        4,
    )
    dimensions = {
        "event_kind": event_kind,
        "aggregation_strategy": aggregation_strategy,
        "product_scope": product_scope,
        "member_evidence_signal": evidence_signal,
        "market_reach_modifier": market_reach_modifier,
        "significant_member_count": len(significant),
        "independent_source_count": independent_source_count,
        "breadth_boost": 0.0,
    }
    evidence = [
        f"成员消息最高内在重要性贡献={evidence_signal:.3f}",
        f"独立信源数={independent_source_count}，仅进入可信度，不改变重要性",
        f"事件事实类型={event_kind}，仅用于一致性校验",
    ]
    if market_reach_modifier:
        evidence.append("外服商城轮换仅影响非国服玩家，事件重要性下调 0.120")
    return score, dimensions, evidence
