from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Final
from zoneinfo import ZoneInfo

from app.domain.content_semantics import has_hotfix_signal
from app.domain.ontology import EventRoute, route_event_cluster

_PATCH_PATTERN = re.compile(r"(?<!\d)(\d{1,2}\.\d{1,2})(?!\d)")
_TFT_PRODUCT_PATTERN = re.compile(
    r"^(?:云顶之弈|teamfight\s*tactics|tft)$",
    re.IGNORECASE,
)
_LATE_STAGE_PATTERN = re.compile(
    r"总决赛|半决赛|冠亚军|季军赛|四强|决赛日|"
    r"胜者组决赛|败者组决赛|grand\s+final|semi-?final",
    re.IGNORECASE,
)
_FOCUS_PATTERN = re.compile(
    r"焦点(?:战|对决)|宿敌对决|生死战|关键战|focus(?:ed)?\s+match",
    re.IGNORECASE,
)
_INTERNATIONAL_KNOCKOUT_PATTERN = re.compile(
    r"淘汰赛|八强|四分之一决赛|quarter-?final|knockout",
    re.IGNORECASE,
)
_LPL_STAGE_PATTERN = re.compile(
    r"英雄联盟职业联赛|登峰组|涅槃组",
    re.IGNORECASE,
)
_COMPETITION_CODES: Final = (
    "worlds",
    "lpl",
    "lck",
    "msi",
    "ewc",
    "lec",
    "lcs",
    "lcp",
    "pcs",
    "vcs",
    "cblol",
)
_INTERNATIONAL_CODES: Final = {"worlds", "msi", "ewc"}
_CN_MARKERS = re.compile(r"国服|中国服|腾讯|掌盟|cn\s+server", re.IGNORECASE)
_GLOBAL_MARKERS = re.compile(
    r"外服|国际服|美服|欧服|韩服|global|international|na\s+server|eu\s+server",
    re.IGNORECASE,
)
_CN_CONNECTORS: Final = {"tencent_lol", "baidu_tieba", "weibo"}
_GLOBAL_CONNECTORS: Final = {"riot_official", "x_twitter"}
_ROLE_RANK: Final = {"primary": 0, "component": 1, "cross_ref": 2}
_ASSERTION_RANK: Final = {
    "asserted": 0,
    "speculative": 1,
    "context_only": 2,
    "negated": 3,
}


@dataclass(frozen=True, slots=True)
class EventMentionEvidence:
    order: int
    topic: str
    subtopic: str
    entities: tuple[dict[str, object], ...]
    temporal: dict[str, object]
    assertion: str
    membership_role: str


@dataclass(frozen=True, slots=True)
class EventCluster:
    source_order: int
    cluster_kind: str
    topic: str
    subtopic: str
    product_scope: str
    entities: tuple[dict[str, object], ...]
    temporal: dict[str, object]
    assertion: str
    membership_role: str
    aggregation_key: str | None = None


def _entity_identity(entity: dict[str, object]) -> tuple[str, str]:
    entity_type = str(entity.get("type") or "unknown").casefold()
    name = (
        str(entity.get("canonical_id") or entity.get("canonical_name") or entity.get("name") or "")
        .strip()
        .casefold()
    )
    return entity_type, name


def _merge_entities(
    evidences: list[EventMentionEvidence],
) -> tuple[dict[str, object], ...]:
    merged: list[dict[str, object]] = []
    seen: set[tuple[str, str]] = set()
    for evidence in evidences:
        for entity in evidence.entities:
            identity = _entity_identity(entity)
            if not identity[1] or identity in seen:
                continue
            seen.add(identity)
            merged.append(dict(entity))
    return tuple(merged)


def _strongest_assertion(evidences: list[EventMentionEvidence]) -> str:
    return min(
        (evidence.assertion for evidence in evidences),
        key=lambda value: _ASSERTION_RANK.get(value, 4),
    )


def _strongest_role(evidences: list[EventMentionEvidence]) -> str:
    return min(
        (evidence.membership_role for evidence in evidences),
        key=lambda value: _ROLE_RANK.get(value, 3),
    )


def _local_date(published_at: datetime | None) -> str | None:
    if published_at is None:
        return None
    if published_at.tzinfo is not None:
        published_at = published_at.astimezone(ZoneInfo("Asia/Shanghai"))
    return published_at.date().isoformat()


def _event_date(evidence: EventMentionEvidence) -> str | None:
    value = str(evidence.temporal.get("event_date") or "").strip()
    return value if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", value) else None


def _batch_anchor(
    evidence: EventMentionEvidence,
    *,
    patch_version: str | None,
    published_at: datetime | None,
) -> str | None:
    return patch_version or _event_date(evidence) or _local_date(published_at)


def _scope_for(evidence: EventMentionEvidence, product_scope: str) -> str:
    if evidence.topic == "tft" or evidence.subtopic in {
        "tft_cosmetic",
        "tft_set",
        "tft_patch",
    }:
        return "tft"
    if any(
        _TFT_PRODUCT_PATTERN.fullmatch(
            str(
                entity.get("canonical_name")
                or entity.get("display_name")
                or entity.get("name")
                or ""
            ).strip()
        )
        for entity in evidence.entities
    ):
        return "tft"
    if evidence.topic == "esports" or evidence.subtopic.startswith("match_"):
        return "lol_esports"
    return product_scope


def _competition_code(
    evidence: EventMentionEvidence,
    *,
    fallback_entities: list[dict[str, object]],
    text: str,
) -> str | None:
    entities = [*evidence.entities, *fallback_entities]
    for entity in entities:
        if str(entity.get("type") or "").casefold() not in {"league", "tournament"}:
            continue
        value = str(
            entity.get("canonical_name") or entity.get("canonical_id") or entity.get("name") or ""
        ).casefold()
        for code in _COMPETITION_CODES:
            if re.search(rf"(?<![a-z]){re.escape(code)}(?![a-z])", value):
                return code
    lowered = text.casefold()
    if "全球总决赛" in text or "世界赛" in text:
        return "worlds"
    if _LPL_STAGE_PATTERN.search(text):
        return "lpl"
    for code in _COMPETITION_CODES:
        if re.search(rf"(?<![a-z]){re.escape(code)}(?![a-z])", lowered):
            return code
    return None


def is_marquee_match(text: str, competition: str | None) -> bool:
    if _LATE_STAGE_PATTERN.search(text) or _FOCUS_PATTERN.search(text):
        return True
    return bool(
        competition in _INTERNATIONAL_CODES and _INTERNATIONAL_KNOCKOUT_PATTERN.search(text)
    )


def _market_region(
    *,
    text: str,
    source_connector_type: str,
    source_name: str,
) -> str:
    evidence = f"{text} {source_name}"
    if _CN_MARKERS.search(evidence):
        return "cn"
    if _GLOBAL_MARKERS.search(evidence):
        return "global"
    connector = source_connector_type.casefold()
    if connector in _CN_CONNECTORS:
        return "cn"
    if connector in _GLOBAL_CONNECTORS:
        return "global"
    return "global"


def _mention_evidence(
    *,
    topic: str,
    subtopic: str,
    entities: list[dict[str, object]],
    facets: dict[str, object],
) -> list[EventMentionEvidence]:
    evidences: list[EventMentionEvidence] = []
    values = facets.get("event_mentions")
    if values == []:
        return []
    if isinstance(values, list):
        for order, value in enumerate(values):
            if not isinstance(value, dict):
                continue
            identity_entities = value.get("identity_entities")
            if not isinstance(identity_entities, list):
                continue
            mention_entities = tuple(
                dict(entity) for entity in identity_entities if isinstance(entity, dict)
            )
            if not mention_entities:
                continue
            temporal = value.get("temporal")
            evidences.append(
                EventMentionEvidence(
                    order=order,
                    topic=str(value.get("topic") or "other"),
                    subtopic=str(value.get("subtopic") or "other"),
                    entities=mention_entities,
                    temporal=dict(temporal) if isinstance(temporal, dict) else {},
                    assertion=str(value.get("assertion") or "asserted"),
                    membership_role=str(value.get("membership_role") or "primary"),
                )
            )
    if not any(evidence.topic == topic and evidence.subtopic == subtopic for evidence in evidences):
        temporal = facets.get("temporal")
        evidences.append(
            EventMentionEvidence(
                order=len(evidences),
                topic=topic,
                subtopic=subtopic,
                entities=tuple(dict(entity) for entity in entities),
                temporal=dict(temporal) if isinstance(temporal, dict) else {},
                assertion=str(facets.get("event_assertion") or "asserted"),
                membership_role="primary",
            )
        )
    return evidences


def _cluster_from_group(
    evidences: list[EventMentionEvidence],
    *,
    cluster_kind: str,
    product_scope: str,
    aggregation_key: str | None = None,
) -> EventCluster:
    first = min(evidences, key=lambda value: value.order)
    return EventCluster(
        source_order=first.order,
        cluster_kind=cluster_kind,
        topic=first.topic,
        subtopic=first.subtopic,
        product_scope=product_scope,
        entities=_merge_entities(evidences),
        temporal=dict(first.temporal),
        assertion=_strongest_assertion(evidences),
        membership_role=_strongest_role(evidences),
        aggregation_key=aggregation_key,
    )


def build_event_clusters(
    *,
    topic: str,
    subtopic: str,
    product_scope: str,
    title: str,
    summary: str,
    entities: list[dict[str, object]],
    facets: dict[str, object],
    published_at: datetime | None,
    content_form: str,
    source_connector_type: str = "",
    source_name: str = "",
) -> list[EventCluster]:
    """Turn semantic mentions into trackable topic clusters, not one event each."""
    text = f"{title} {summary}"
    patch_match = _PATCH_PATTERN.search(text)
    patch_version = patch_match.group(1) if patch_match else None
    evidences = _mention_evidence(
        topic=topic,
        subtopic=subtopic,
        entities=entities,
        facets=facets,
    )
    pending = {
        evidence.order: evidence for evidence in evidences if evidence.subtopic != "free_rotation"
    }
    primary_evidence = [evidence for evidence in evidences if evidence.membership_role == "primary"]
    has_patch_cluster = any(
        evidence.topic == "patch"
        or evidence.subtopic in {"patch_notes", "patch_preview", "pbe_change"}
        for evidence in evidences
    )
    hotfix_entities = {
        _entity_identity(entity)
        for evidence in primary_evidence
        if evidence.subtopic == "hotfix"
        for entity in evidence.entities
    }
    for order, evidence in list(pending.items()):
        entity_ids = {_entity_identity(entity) for entity in evidence.entities}
        has_named_activity = any(
            str(entity.get("type") or "").casefold() == "activity" for entity in evidence.entities
        )
        dependent_reward = (
            evidence.membership_role != "primary"
            and evidence.subtopic == "free_reward"
            and not has_named_activity
            and bool(primary_evidence)
        )
        dependent_incident = (
            evidence.membership_role != "primary"
            and evidence.subtopic in {"maintenance", "outage", "security"}
            and bool(entity_ids & hotfix_entities)
        )
        patch_component = (
            has_patch_cluster
            and evidence.membership_role != "primary"
            and evidence.subtopic in {"champion_update", "game_mode_update"}
        )
        if dependent_reward or dependent_incident or patch_component:
            pending.pop(order)
    clusters: list[EventCluster] = []

    is_hotfix = any(evidence.subtopic == "hotfix" for evidence in evidences) or has_hotfix_signal(
        text
    )
    if is_hotfix:
        hotfix_orders = [
            order
            for order, evidence in pending.items()
            if evidence.subtopic in {"hotfix", "champion_update", "game_mode_update", "maintenance"}
        ]
        if not hotfix_orders:
            hotfix_orders = [
                order
                for order, evidence in pending.items()
                if evidence.membership_role == "primary"
            ][:1]
        if hotfix_orders:
            group = [pending.pop(order) for order in hotfix_orders]
            first = min(group, key=lambda value: value.order)
            temporal = next(
                (
                    dict(evidence.temporal)
                    for evidence in group
                    if _event_date(evidence) is not None
                ),
                dict(first.temporal),
            )
            clusters.append(
                EventCluster(
                    source_order=first.order,
                    cluster_kind="hotfix",
                    topic="patch",
                    subtopic="hotfix",
                    product_scope=_scope_for(first, product_scope),
                    entities=_merge_entities(group),
                    temporal=temporal,
                    assertion=_strongest_assertion(group),
                    membership_role=_strongest_role(group),
                )
            )

    cosmetic_orders = [
        order
        for order, evidence in pending.items()
        if evidence.subtopic in {"skin_release", "tft_cosmetic"}
    ]
    cosmetic_groups: dict[tuple[str, str, int | None], list[EventMentionEvidence]] = {}
    for order in cosmetic_orders:
        evidence = pending.pop(order)
        scope = _scope_for(evidence, product_scope)
        anchor = (
            _batch_anchor(
                evidence,
                patch_version=patch_version,
                published_at=published_at,
            )
            or "undated"
        )
        independent_roundup = content_form == "roundup" and patch_version is None
        key = (scope, anchor, evidence.order if independent_roundup else None)
        cosmetic_groups.setdefault(key, []).append(evidence)
    for (scope, anchor, _), group in cosmetic_groups.items():
        merged = _merge_entities(group)
        products = {
            _entity_identity(entity)
            for entity in merged
            if str(entity.get("type") or "").casefold() in {"skin", "product", "other"}
            and str(entity.get("role") or "context").casefold() in {"core", "affected"}
        }
        is_batch = len(products) > 1
        clusters.append(
            _cluster_from_group(
                group,
                cluster_kind="cosmetic_batch" if is_batch else "cosmetic_release",
                product_scope=scope,
                aggregation_key=(f"cosmetic_batch:{scope}:{anchor}" if is_batch else None),
            )
        )

    activity_orders = [
        order
        for order, evidence in pending.items()
        if evidence.topic == "activity"
        or evidence.subtopic in {"event_pass", "in_game_activity", "free_reward", "community_event"}
    ]
    activity_groups: dict[tuple[str, str, int | None], list[EventMentionEvidence]] = {}
    for order in activity_orders:
        evidence = pending.pop(order)
        family = "community" if evidence.subtopic == "community_event" else "player"
        anchor = (
            _batch_anchor(
                evidence,
                patch_version=patch_version,
                published_at=published_at,
            )
            or "undated"
        )
        independent_roundup = content_form == "roundup" and patch_version is None
        key = (family, anchor, evidence.order if independent_roundup else None)
        activity_groups.setdefault(key, []).append(evidence)
    for (family, anchor, _), group in activity_groups.items():
        merged = _merge_entities(group)
        activities = {
            _entity_identity(entity)
            for entity in merged
            if str(entity.get("type") or "").casefold() in {"activity", "product"}
            and str(entity.get("role") or "context").casefold() in {"core", "affected"}
        }
        is_batch = family == "player" and len(activities) > 1
        scope = _scope_for(group[0], product_scope)
        clusters.append(
            _cluster_from_group(
                group,
                cluster_kind="activity_batch" if is_batch else f"{family}_activity",
                product_scope=scope,
                aggregation_key=(f"activity_batch:{scope}:{anchor}" if is_batch else None),
            )
        )

    match_orders = [
        order
        for order, evidence in pending.items()
        if evidence.topic == "esports" and evidence.subtopic in {"match_schedule", "match_result"}
    ]
    has_multiple_matches = len(match_orders) > 1
    matchday_groups: dict[tuple[str, str], list[EventMentionEvidence]] = {}
    for order in match_orders:
        evidence = pending.pop(order)
        competition = _competition_code(
            evidence,
            fallback_entities=entities,
            text=text,
        )
        date_key = _event_date(evidence) or _local_date(published_at)
        if (
            competition
            and date_key
            and (has_multiple_matches or not is_marquee_match(text, competition))
        ):
            matchday_groups.setdefault((competition, date_key), []).append(evidence)
            continue
        clusters.append(
            _cluster_from_group(
                [evidence],
                cluster_kind="marquee_match" if competition and date_key else "match",
                product_scope="lol_esports",
            )
        )
    for (competition, date_key), group in matchday_groups.items():
        clusters.append(
            _cluster_from_group(
                group,
                cluster_kind="matchday",
                product_scope="lol_esports",
                aggregation_key=f"matchday:{competition}:{date_key}",
            )
        )

    gameplay_update_orders = [
        order
        for order, evidence in pending.items()
        if evidence.subtopic in {"champion_update", "game_mode_update"}
    ]
    gameplay_update_groups: dict[str, list[EventMentionEvidence]] = {}
    for order in gameplay_update_orders:
        evidence = pending.pop(order)
        anchor = _event_date(evidence) or _local_date(published_at) or "undated"
        gameplay_update_groups.setdefault(anchor, []).append(evidence)
    for anchor, group in gameplay_update_groups.items():
        merged = _merge_entities(group)
        subjects = {
            _entity_identity(entity)
            for entity in merged
            if str(entity.get("type") or "").casefold() in {"champion", "game_mode"}
            and str(entity.get("role") or "context").casefold() in {"core", "affected"}
        }
        is_batch = len(subjects) > 1
        scope = _scope_for(group[0], product_scope)
        clusters.append(
            _cluster_from_group(
                group,
                cluster_kind="gameplay_update_batch" if is_batch else "gameplay_update",
                product_scope=scope,
                aggregation_key=(f"gameplay_update_batch:{scope}:{anchor}" if is_batch else None),
            )
        )

    market = _market_region(
        text=text,
        source_connector_type=source_connector_type,
        source_name=source_name,
    )
    for evidence in pending.values():
        scope = _scope_for(evidence, product_scope)
        aggregation_key = None
        cluster_kind = evidence.subtopic
        if evidence.subtopic == "shop_rotation":
            local = _local_date(published_at)
            if local:
                year, week, _ = datetime.fromisoformat(local).isocalendar()
                aggregation_key = f"shop_rotation:{scope}:{market}:{year}-W{week:02d}"
            cluster_kind = "shop_rotation"
        clusters.append(
            _cluster_from_group(
                [evidence],
                cluster_kind=cluster_kind,
                product_scope=scope,
                aggregation_key=aggregation_key,
            )
        )
    return sorted(clusters, key=lambda cluster: cluster.source_order)


def route_event_clusters(
    *,
    topic: str,
    subtopic: str,
    source_kind: str,
    information_stage: str,
    content_form: str,
    product_scope: str,
    title: str,
    summary: str,
    entities: list[dict[str, object]],
    facets: dict[str, object],
    published_at: datetime | None,
    fact_claims: list[dict[str, object]] | None = None,
    source_connector_type: str = "",
    source_name: str = "",
) -> list[EventRoute]:
    clusters = build_event_clusters(
        topic=topic,
        subtopic=subtopic,
        product_scope=product_scope,
        title=title,
        summary=summary,
        entities=entities,
        facets=facets,
        published_at=published_at,
        content_form=content_form,
        source_connector_type=source_connector_type,
        source_name=source_name,
    )
    has_mode_cluster = any(cluster.subtopic == "game_mode_release" for cluster in clusters)
    routes: list[EventRoute] = []
    for cluster in clusters:
        if cluster.cluster_kind == "matchday" and cluster.aggregation_key:
            creation_policy = (
                "existing_only"
                if cluster.assertion in {"negated", "context_only"}
                or information_stage in {"commentary", "reminder"}
                or content_form == "repost"
                else "allow"
            )
            routes.append(
                EventRoute(
                    event_kind="esports_match",
                    aggregation_strategy="calendar_day",
                    product_scope=cluster.product_scope,
                    aggregation_key=cluster.aggregation_key,
                    membership_role=cluster.membership_role,
                    creation_policy=creation_policy,
                    assertion=cluster.assertion,
                )
            )
            continue
        cluster_facets = {
            "temporal": dict(cluster.temporal),
            "event_assertion": cluster.assertion,
        }
        cluster_routes = route_event_cluster(
            topic=cluster.topic,
            subtopic=cluster.subtopic,
            source_kind=source_kind,
            information_stage=information_stage,
            content_form=content_form,
            product_scope=cluster.product_scope,
            title=title,
            summary=summary,
            entities=[dict(entity) for entity in cluster.entities],
            facets=cluster_facets,
            published_at=published_at,
            fact_claims=([] if has_mode_cluster and cluster.topic == "patch" else fact_claims),
            _mention_role=cluster.membership_role,
            _mention_assertion=cluster.assertion,
        )
        if cluster.aggregation_key and cluster_routes:
            cluster_routes = [replace(cluster_routes[0], aggregation_key=cluster.aggregation_key)]
        routes.extend(cluster_routes)

    routes_by_identity: dict[tuple[str, str, str, str], EventRoute] = {}
    for route in routes:
        if route.aggregation_key is None:
            continue
        identity = (
            route.event_kind,
            route.aggregation_strategy,
            route.product_scope,
            route.aggregation_key,
        )
        existing = routes_by_identity.get(identity)
        if existing is None or _ROLE_RANK.get(route.membership_role, 3) < _ROLE_RANK.get(
            existing.membership_role, 3
        ):
            routes_by_identity[identity] = route
    return list(routes_by_identity.values())
