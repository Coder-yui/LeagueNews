from app.schemas.event_workflow import EventDecisionDraft


def validate_event_decision_business(
    decision: EventDecisionDraft,
    *,
    item: dict[str, object],
    candidates: list[dict[str, object]],
    allowed_new_keys: set[str],
) -> str | None:
    """Shared deterministic validation for AI and human event decisions."""
    candidate_ids = {int(candidate["event_id"]) for candidate in candidates}
    candidates_by_id = {int(candidate["event_id"]): candidate for candidate in candidates}
    candidates_by_key = {
        str(candidate["aggregation_key"]): candidate
        for candidate in candidates
        if candidate.get("aggregation_key")
    }
    lifecycle_rank = {"scheduled": 0, "live": 1, "completed": 2}
    for membership in decision.memberships:
        if membership.evidence_stance == "context" or membership.update_kind == "context":
            membership.evidence_stance = "context"
            membership.update_kind = "context"
        existing_id = membership.existing_event_id
        if existing_id is not None and existing_id not in candidate_ids:
            return "existing target 只能引用输入候选中的 event_id"
        if membership.target == "new":
            if allowed_new_keys and membership.aggregation_key not in allowed_new_keys:
                return "new membership 不能编造程序路由之外的 aggregation_key"
            if membership.aggregation_key in candidates_by_key:
                return "聚合键已对应候选事件，不能重复创建"
        candidate = candidates_by_id.get(existing_id or -1)
        if (
            candidate is not None
            and membership.event_type in {"daily_matches", "major_match"}
            and str(candidate.get("event_type")) in {"daily_matches", "major_match"}
            and membership.lifecycle_status in lifecycle_rank
            and candidate.get("lifecycle_status") in lifecycle_rank
            and lifecycle_rank[membership.lifecycle_status]
            < lifecycle_rank[str(candidate["lifecycle_status"])]
        ):
            membership.lifecycle_status = str(candidate["lifecycle_status"])
            membership.update_kind = "context"
            membership.evidence_stance = "context"
    if any(membership.target == "new" for membership in decision.memberships) and candidate_ids:
        rejected = {entry.event_id for entry in decision.candidate_rejections}
        if rejected != candidate_ids:
            return "存在候选时选择 new，candidate_rejections 必须逐一拒绝全部候选"
    policy = item.get("event_policy")
    if isinstance(policy, dict) and policy.get("policy_type") == "mythic_shop_rotation":
        eligible = bool(policy.get("event_eligible"))
        if eligible and not decision.memberships:
            return "国服神话商城轮换必须形成或更新 shop_rotation"
        if not eligible and decision.memberships:
            return "非国服神话商城轮换不进入事件层"
        for membership in decision.memberships:
            membership.event_type = "shop_rotation"
            if policy.get("cadence") == "daily":
                membership.update_kind = "context"
                membership.evidence_stance = "context"
    return None
