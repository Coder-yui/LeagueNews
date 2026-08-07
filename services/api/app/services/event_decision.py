from app.schemas.event_workflow import (
    CandidateRejection,
    EventDecisionDraft,
    EventMembershipDraft,
)

_IDENTITY_FIELDS = ("event_kind", "aggregation_strategy", "product_scope")
_IDENTITY_REASON_PREFIXES = (
    "短窗口热更新连续：",
    "命名主体包含：",
    "实体重叠：",
    "标题相似度 ",
    "事实相似度 ",
)


def _candidate_identity_rationale(candidate: dict[str, object]) -> str | None:
    reasons = candidate.get("reasons")
    if not isinstance(reasons, (list, tuple)):
        return None
    identity_reasons = [
        str(reason)
        for reason in reasons
        if str(reason).startswith(_IDENTITY_REASON_PREFIXES)
    ]
    return "；".join(identity_reasons[:3]) or None


def stabilize_event_decision(
    decision: EventDecisionDraft,
    *,
    item: dict[str, object],
    candidates: list[dict[str, object]],
) -> EventDecisionDraft:
    """Bind semantic LLM annotations to deterministic route identities."""
    candidates_by_key = {
        str(candidate["aggregation_key"]): candidate
        for candidate in candidates
        if candidate.get("aggregation_key")
    }
    candidates_by_id = {
        int(candidate["event_id"]): candidate
        for candidate in candidates
        if candidate.get("event_id") is not None
    }
    annotations = {
        membership.aggregation_key: membership
        for membership in decision.memberships
    }
    routes = [
        dict(value)
        for value in item.get("event_routes", [])
        if isinstance(value, dict) and value.get("aggregation_key")
    ]
    stage = str(item.get("information_stage") or "update")
    content_form = str(item.get("content_form") or "original")
    fallback_note = str(item.get("summary") or item.get("title") or "事件更新")
    memberships: list[EventMembershipDraft] = []

    for route in routes:
        key = str(route["aggregation_key"])
        exact = candidates_by_key.get(key)
        annotation = annotations.get(key)
        if annotation is None:
            compatible_annotations = []
            for proposed in decision.memberships:
                candidate = candidates_by_id.get(proposed.existing_event_id or -1)
                if candidate is None:
                    continue
                compatible_routes = [
                    candidate_route
                    for candidate_route in routes
                    if all(
                        str(candidate.get(field))
                        == str(candidate_route.get(field))
                        for field in _IDENTITY_FIELDS
                    )
                ]
                if (
                    len(compatible_routes) == 1
                    and str(compatible_routes[0]["aggregation_key"]) == key
                ):
                    compatible_annotations.append(proposed)
            if len(compatible_annotations) == 1:
                annotation = compatible_annotations[0]
        semantic_candidate = (
            candidates_by_id.get(annotation.existing_event_id)
            if annotation is not None and annotation.existing_event_id is not None
            else None
        )
        semantic_match = (
            exact is None
            and semantic_candidate is not None
            and str(semantic_candidate.get("deterministic_route_key") or "") == key
            and all(
                str(semantic_candidate.get(field)) == str(route.get(field))
                for field in _IDENTITY_FIELDS
            )
        )
        if (
            route.get("creation_policy") == "existing_only"
            and exact is None
            and not semantic_match
        ):
            continue
        assertion = str(
            route.get("assertion")
            or item.get("event_assertion")
            or "asserted"
        )
        if annotation is None or (
            exact is None
            and annotation.existing_event_id is not None
            and not semantic_match
        ):
            if assertion == "negated":
                evidence_stance = "contradicts"
                update_kind = "refutation"
            elif assertion == "context_only" or stage in {"commentary", "reminder"}:
                evidence_stance = "context"
                update_kind = "context"
            elif content_form == "repost":
                evidence_stance = "supports"
                update_kind = "duplicate_evidence"
            elif stage == "correction":
                evidence_stance = "supports"
                update_kind = "correction"
            else:
                evidence_stance = "supports"
                update_kind = "confirmation" if exact is not None else "new_fact"
            lifecycle_status = {
                "announcement": "scheduled",
                "preview": "unconfirmed" if assertion == "speculative" else "scheduled",
                "active": "live",
                "result": "completed",
                "rumor": "unconfirmed",
                "speculation": "unconfirmed",
            }.get(stage)
            annotation = EventMembershipDraft(
                target="new",
                event_kind=str(route["event_kind"]),
                aggregation_strategy=str(route["aggregation_strategy"]),
                product_scope=str(route["product_scope"]),
                aggregation_key=key,
                identity_resolution="new_event",
                membership_role=str(route.get("membership_role") or "primary"),
                evidence_stance=evidence_stance,
                update_kind=update_kind,
                lifecycle_status=lifecycle_status,
                timeline_note=fallback_note,
            )
        if exact is not None:
            annotation.target = f"existing:{int(exact['event_id'])}"
            annotation.identity_resolution = "exact_key"
            annotation.identity_rationale = "稳定聚合键精确匹配"
        elif semantic_match:
            annotation.identity_resolution = "semantic_candidate"
            if not str(annotation.identity_rationale or "").strip():
                annotation.identity_rationale = _candidate_identity_rationale(
                    semantic_candidate
                )
        else:
            annotation.target = "new"
            annotation.identity_resolution = "new_event"
        annotation.event_kind = str(route["event_kind"])
        annotation.aggregation_strategy = str(route["aggregation_strategy"])
        annotation.product_scope = str(route["product_scope"])
        annotation.aggregation_key = key
        if assertion == "negated":
            annotation.evidence_stance = "contradicts"
            annotation.update_kind = "refutation"
        elif assertion == "context_only" or stage in {"commentary", "reminder"}:
            annotation.evidence_stance = "context"
            annotation.update_kind = "context"
        memberships.append(annotation)

    selected_ids = {
        membership.existing_event_id
        for membership in memberships
        if membership.existing_event_id is not None
    }
    rejected_by_id = {
        rejection.event_id: rejection for rejection in decision.candidate_rejections
    }
    decision.memberships = memberships
    decision.candidate_rejections = (
        [
            rejected_by_id.get(int(candidate["event_id"]))
            or CandidateRejection(
                event_id=int(candidate["event_id"]),
                reason="稳定聚合键与当前消息的事件身份不同",
            )
            for candidate in candidates
            if int(candidate["event_id"]) not in selected_ids
        ]
        if any(membership.target == "new" for membership in memberships)
        else []
    )
    return decision


def validate_event_decision_business(
    decision: EventDecisionDraft,
    *,
    item: dict[str, object],
    candidates: list[dict[str, object]],
    allowed_new_keys: set[str],
) -> str | None:
    """Shared deterministic validation for AI and human event decisions."""
    candidate_ids = {int(candidate["event_id"]) for candidate in candidates}
    candidates_by_id = {
        int(candidate["event_id"]): candidate for candidate in candidates
    }
    candidates_by_key = {
        str(candidate["aggregation_key"]): candidate
        for candidate in candidates
        if candidate.get("aggregation_key")
    }
    routes_by_key = {
        str(route["aggregation_key"]): route
        for route in item.get("event_routes", [])
        if isinstance(route, dict) and route.get("aggregation_key")
    }
    route_keys = set(routes_by_key) or allowed_new_keys
    mandatory_keys = (
        {
            key
            for key, route in routes_by_key.items()
            if route.get("creation_policy") != "existing_only"
            or key in candidates_by_key
        }
        if routes_by_key
        else allowed_new_keys
    )
    membership_keys = {
        membership.aggregation_key for membership in decision.memberships
    }
    if not mandatory_keys.issubset(membership_keys) or not membership_keys.issubset(
        route_keys
    ):
        return "事件归属必须完整使用程序生成的必选 event_routes"
    lifecycle_rank = {"scheduled": 0, "live": 1, "completed": 2}
    for membership in decision.memberships:
        if (
            membership.evidence_stance == "context"
            or membership.update_kind == "context"
        ):
            membership.evidence_stance = "context"
            membership.update_kind = "context"
        existing_id = membership.existing_event_id
        if membership.aggregation_key not in route_keys:
            return "membership 不能使用程序路由之外的 aggregation_key"
        route = routes_by_key.get(membership.aggregation_key)
        if route is not None:
            for field in _IDENTITY_FIELDS:
                if str(route.get(field)) != str(getattr(membership, field)):
                    return f"membership 的 {field} 必须与程序 event_routes 一致"
        if existing_id is not None and existing_id not in candidate_ids:
            return "existing target 只能引用输入候选中的 event_id"
        exact_candidate = candidates_by_key.get(membership.aggregation_key)
        if exact_candidate is not None:
            if existing_id != int(exact_candidate["event_id"]):
                return "同一 aggregation_key 必须归入已有事件"
            membership.identity_resolution = "exact_key"
        elif existing_id is not None:
            candidate = candidates_by_id.get(existing_id)
            if candidate is None:
                return "语义归并只能引用输入候选中的 event_id"
            if membership.identity_resolution != "semantic_candidate":
                return "不同聚合键归入已有事件时必须标记 semantic_candidate"
            if not str(membership.identity_rationale or "").strip():
                return "语义归并必须说明事件身份相同的依据"
        if membership.target == "new":
            membership.identity_resolution = "new_event"
            if not allowed_new_keys:
                return "没有稳定程序路由键时不能自动创建事件"
            if membership.aggregation_key not in allowed_new_keys:
                return "该路由只能归入已有事件，不能创建新事件"
            if "unknown" in membership.aggregation_key.casefold():
                return "aggregation_key 不得包含 unknown 占位符"
            if membership.aggregation_key in candidates_by_key:
                return "聚合键已对应候选事件，不能重复创建"
        candidate = candidates_by_id.get(existing_id or -1)
        if candidate is not None:
            for field in _IDENTITY_FIELDS:
                if str(candidate.get(field)) != str(getattr(membership, field)):
                    return f"已有事件与 membership 的 {field} 不一致"
        if (
            candidate is not None
            and membership.event_kind == "esports_match"
            and str(candidate.get("event_kind")) == "esports_match"
            and membership.lifecycle_status in lifecycle_rank
            and candidate.get("lifecycle_status") in lifecycle_rank
            and lifecycle_rank[membership.lifecycle_status]
            < lifecycle_rank[str(candidate["lifecycle_status"])]
        ):
            membership.lifecycle_status = str(candidate["lifecycle_status"])
            membership.update_kind = "context"
            membership.evidence_stance = "context"
    if any(membership.target == "new" for membership in decision.memberships):
        rejected = {entry.event_id for entry in decision.candidate_rejections}
        selected = {
            membership.existing_event_id
            for membership in decision.memberships
            if membership.existing_event_id is not None
        }
        if rejected != candidate_ids - selected:
            return "创建新事件时必须逐一拒绝未选择的候选"
    if item.get("information_stage") == "commentary":
        for membership in decision.memberships:
            membership.update_kind = "context"
            membership.evidence_stance = "context"
    return None
