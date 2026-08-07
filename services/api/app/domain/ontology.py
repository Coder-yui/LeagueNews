import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal, get_args
from zoneinfo import ZoneInfo

ONTOLOGY_VERSION: Final = "lol-news-v6"

# These axes answer different questions. Keeping them separate prevents source
# authority, publication stage, presentation form, and subject matter from
# leaking into one overloaded label.
SourceKind = Literal[
    "first_party", "attributed_report", "data_mined", "community", "unknown"
]
InformationStage = Literal[
    "announcement",
    "preview",
    "active",
    "result",
    "update",
    "correction",
    "rumor",
    "speculation",
    "reminder",
    "commentary",
]
ContentForm = Literal["original", "repost", "roundup"]
EventAssertion = Literal["asserted", "speculative", "negated", "context_only"]
ProductScope = Literal[
    "lol_pc",
    "lol_esports",
    "tft",
    "lol_universe",
    "riot_corporate",
    "lol_merch_music",
    "wild_rift",
    "2xko",
    "valorant",
    "uncertain",
]
PrimaryTopic = Literal[
    "patch",
    "esports",
    "roster",
    "champion",
    "skin",
    "activity",
    "game_mode",
    "tft",
    "commerce",
    "service",
    "community",
    "business",
    "universe",
    "media",
    "other",
]
Subtopic = Literal[
    "patch_notes",
    "patch_preview",
    "hotfix",
    "pbe_change",
    "champion_release",
    "champion_update",
    "item_rune_system",
    "game_mode_release",
    "game_mode_update",
    "tft_set",
    "tft_patch",
    "skin_release",
    "tft_cosmetic",
    "shop_rotation",
    "shop_offer",
    "free_rotation",
    "free_reward",
    "event_pass",
    "in_game_activity",
    "ticketing",
    "match_schedule",
    "match_result",
    "standings_qualification",
    "roster_move",
    "disciplinary",
    "maintenance",
    "outage",
    "security",
    "merch",
    "partnership",
    "corporate",
    "lore",
    "music_video",
    "community_event",
    "gameplay_guide",
    "community_post",
    "other",
]
EntityType = Literal[
    "champion",
    "skin",
    "item",
    "rune",
    "patch",
    "game_mode",
    "team",
    "player",
    "coach",
    "person",
    "tournament",
    "league",
    "activity",
    "region",
    "organization",
    "product",
    "system",
    "other",
    "unknown",
]
EventKind = Literal[
    "gameplay_update",
    "gameplay_release",
    "cosmetic_release",
    "roster_change",
    "esports_match",
    "esports_schedule",
    "qualification_change",
    "commercial_offer",
    "player_activity",
    "service_incident",
    "disciplinary_action",
    "security_notice",
    "media_release",
    "corporate_announcement",
    "community_activity",
    "other",
]
AggregationStrategy = Literal[
    "timeline",
    "patch_cycle",
    "calendar_day",
    "recurring_window",
    "release",
    "singleton",
]

SOURCE_KINDS: Final = frozenset(get_args(SourceKind))
INFORMATION_STAGES: Final = frozenset(get_args(InformationStage))
CONTENT_FORMS: Final = frozenset(get_args(ContentForm))
EVENT_ASSERTIONS: Final = frozenset(get_args(EventAssertion))
PRODUCT_SCOPES: Final = frozenset(get_args(ProductScope))
PRIMARY_TOPICS: Final = frozenset(get_args(PrimaryTopic))
SUBTOPICS: Final = frozenset(get_args(Subtopic))
ENTITY_TYPES: Final = frozenset(get_args(EntityType))
EVENT_KINDS: Final = frozenset(get_args(EventKind))
AGGREGATION_STRATEGIES: Final = frozenset(get_args(AggregationStrategy))
ENTITY_TYPE_ALIASES: Final = {
    "hero": "champion",
    "pro_player": "player",
    "professional_player": "player",
    "club": "team",
    "competition": "tournament",
    "event": "activity",
    "mode": "game_mode",
    "version": "patch",
}
TIMELINE_EVENT_KINDS: Final = {
    "gameplay_update",
    "roster_change",
    "qualification_change",
    "service_incident",
    "disciplinary_action",
    "security_notice",
}

_PATCH_PATTERN = re.compile(r"(?<!\d)(\d{1,2}\.\d{1,2})(?!\d)")
_EVENT_DATE_PATTERN = re.compile(
    r"(?:(?P<year>20\d{2})[\-/\u5e74])?(?P<month>\d{1,2})[\-/\u6708](?P<day>\d{1,2})(?:\u65e5)?"
)
_KEY_TOKEN_PATTERN = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class EventRoute:
    event_kind: str
    aggregation_strategy: str
    product_scope: str
    aggregation_key: str | None
    membership_role: str = "primary"
    creation_policy: Literal["allow", "existing_only"] = "allow"
    assertion: EventAssertion = "asserted"


def _key_token(value: str) -> str:
    return _KEY_TOKEN_PATTERN.sub("-", value.strip().casefold()).strip("-")[:80]


_TEAM_SUFFIX_PATTERN = re.compile(
    r"(?:电子竞技俱乐部|电竞俱乐部|电子竞技战队|电竞战队|战队)$",
    re.IGNORECASE,
)
_LATIN_TOKEN_PATTERN = re.compile(r"(?<![a-z0-9])[a-z][a-z0-9]{1,15}(?![a-z0-9])", re.IGNORECASE)
_COMPETITION_CODES: Final = (
    "worlds",
    "cblol",
    "lpl",
    "lck",
    "lec",
    "lcs",
    "lcp",
    "pcs",
    "vcs",
    "lla",
    "msi",
    "ewc",
)


def canonical_entity_name(entity_type: str, value: str) -> str:
    """Normalize identity syntax without trying to replace semantic resolution."""
    normalized = unicodedata.normalize("NFKC", value).strip()
    if not normalized:
        return normalized
    lowered = normalized.casefold()
    if entity_type in {"league", "tournament"}:
        for code in _COMPETITION_CODES:
            if re.search(rf"(?<![a-z]){re.escape(code)}(?![a-z])", lowered):
                return code
        latin_tokens = _LATIN_TOKEN_PATTERN.findall(lowered)
        if len(latin_tokens) == 1:
            return latin_tokens[0]
    if entity_type == "team":
        shortened = _TEAM_SUFFIX_PATTERN.sub("", normalized).strip()
        latin_tokens = _LATIN_TOKEN_PATTERN.findall(shortened)
        if latin_tokens and any("\u4e00" <= character <= "\u9fff" for character in shortened):
            return latin_tokens[0].casefold()
        return shortened
    if entity_type in {"player", "coach", "person"}:
        latin_tokens = _LATIN_TOKEN_PATTERN.findall(normalized)
        if latin_tokens and any("\u4e00" <= character <= "\u9fff" for character in normalized):
            return max(latin_tokens, key=len)
    return normalized


def _entity_names(
    entities: list[dict[str, object]],
    *,
    types: set[str] | None = None,
    roles: set[str] | None = None,
) -> list[str]:
    values = []
    for entity in entities:
        entity_type = str(entity.get("type") or "").casefold()
        role = str(entity.get("role") or "context").casefold()
        if types is not None and entity_type not in types:
            continue
        if roles is not None and role not in roles:
            continue
        value = str(
            entity.get("canonical_name")
            or entity.get("canonical_id")
            or entity.get("name")
            or ""
        ).strip()
        value = canonical_entity_name(entity_type, value)
        if token := _key_token(value):
            values.append(token)
    return values


def _local_date(published_at: datetime | None) -> datetime | None:
    if published_at is not None and published_at.tzinfo is not None:
        return published_at.astimezone(ZoneInfo("Asia/Shanghai"))
    return published_at


def _iso_week(published_at: datetime | None) -> str | None:
    local = _local_date(published_at)
    if local is None:
        return None
    year, week, _ = local.isocalendar()
    return f"{year}-W{week:02d}"


def _event_date(
    text: str,
    facets: dict[str, object],
    published_at: datetime | None,
    *,
    allow_publication_fallback: bool = True,
) -> str | None:
    temporal = facets.get("temporal")
    if isinstance(temporal, dict):
        explicit = str(temporal.get("event_date") or "").strip()
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", explicit):
            return explicit
    match = _EVENT_DATE_PATTERN.search(text)
    local = _local_date(published_at)
    if match:
        year = int(match.group("year") or (local.year if local else datetime.now().year))
        try:
            return datetime(year, int(match.group("month")), int(match.group("day"))).date().isoformat()
        except ValueError:
            return None
    lowered = text.casefold()
    if local is not None:
        if "明天" in text or re.search(r"\btomorrow\b", lowered):
            return (local.date() + timedelta(days=1)).isoformat()
        if "昨天" in text or re.search(r"\byesterday\b", lowered):
            return (local.date() - timedelta(days=1)).isoformat()
        if "今天" in text or "今日" in text or re.search(r"\btoday\b", lowered):
            return local.date().isoformat()
    if allow_publication_fallback and local:
        return local.date().isoformat()
    return None


def _claim_mentions_entity_type(value: object, entity_type: str) -> bool:
    if isinstance(value, dict):
        if str(value.get("type") or "").casefold() == entity_type:
            return True
        return any(
            _claim_mentions_entity_type(entry, entity_type)
            for entry in value.values()
        )
    if isinstance(value, list):
        return any(
            _claim_mentions_entity_type(entry, entity_type) for entry in value
        )
    return False


def route_event_cluster(
    *,
    topic: str,
    title: str,
    summary: str,
    entities: list[dict[str, object]],
    facets: dict[str, object],
    published_at: datetime | None,
    source_kind: str = "unknown",
    information_stage: str = "update",
    content_form: str = "original",
    subtopic: str = "other",
    product_scope: str = "uncertain",
    fact_claims: list[dict[str, object]] | None = None,
    _mention_role: str = "primary",
    _mention_assertion: str | None = None,
) -> list[EventRoute]:
    text = f"{title} {summary}"
    local = _local_date(published_at)
    year = local.year if local else datetime.now().year
    date_key = _event_date(text, facets, published_at)
    event_assertion = str(
        _mention_assertion or facets.get("event_assertion") or "asserted"
    )
    creation_policy: Literal["allow", "existing_only"] = (
        "existing_only"
        if event_assertion in {"negated", "context_only"}
        or information_stage in {"commentary", "reminder"}
        or content_form == "repost"
        else "allow"
    )

    def _route(
        event_kind: str,
        aggregation_strategy: str,
        scope: str,
        aggregation_key: str | None,
        membership_role: str | None = None,
    ) -> EventRoute:
        return EventRoute(
            event_kind=event_kind,
            aggregation_strategy=aggregation_strategy,
            product_scope=scope,
            aggregation_key=aggregation_key,
            membership_role=membership_role or _mention_role,
            creation_policy=creation_policy,
            assertion=(
                event_assertion
                if event_assertion in EVENT_ASSERTIONS
                else "asserted"
            ),
        )

    if source_kind == "data_mined" and subtopic == "other":
        products = _entity_names(entities, roles={"core", "affected"})
        if not products:
            return []
        return [
            _route(
                "gameplay_update",
                "timeline",
                product_scope,
                f"dev_preview:{product_scope}:{products[0]}",
            )
        ]

    if topic == "roster" or subtopic == "roster_move":
        people = _entity_names(entities, types={"player", "coach", "person"})
        if not people:
            return []
        return [
            _route(
                "roster_change",
                "timeline",
                product_scope,
                f"transfer:{year}:{people[0]}",
            )
        ]

    patch_match = _PATCH_PATTERN.search(text)
    version = patch_match.group(1) if patch_match else None
    if topic == "patch" or subtopic in {
        "patch_notes",
        "patch_preview",
        "hotfix",
        "pbe_change",
    }:
        if version:
            routes = [
                _route(
                    "gameplay_update",
                    "patch_cycle",
                    product_scope,
                    f"patch:{product_scope}:{version}",
                )
            ]
        elif subtopic == "hotfix" and date_key:
            routes = [
                _route(
                    "gameplay_update",
                    "timeline",
                    product_scope,
                    f"hotfix:{product_scope}:{date_key}",
                )
            ]
        else:
            return []
        releases_mode = any(
            str(claim.get("predicate") or "")
            in {"adds_mode", "goes_live", "releases"}
            and _claim_mentions_entity_type(claim, "game_mode")
            for claim in fact_claims or []
        )
        if releases_mode:
            features = _entity_names(entities, types={"game_mode", "product"})
            if features:
                routes.append(
                    _route(
                        "gameplay_release",
                        "release",
                        product_scope,
                        f"gameplay:{product_scope}:{features[0]}",
                        "component",
                    )
                )
        return routes

    if subtopic == "tft_patch":
        if not version:
            return []
        return [
            _route(
                "gameplay_update",
                "patch_cycle",
                product_scope,
                f"patch:tft:{version}",
            )
        ]

    if subtopic in {
        "champion_release",
        "champion_update",
        "game_mode_release",
        "game_mode_update",
        "skin_release",
        "tft_set",
        "tft_cosmetic",
    }:
        products = _entity_names(entities, roles={"core", "affected"})
        if not products:
            return []
        if subtopic in {"skin_release", "tft_cosmetic"}:
            kind = "cosmetic_release"
        elif subtopic in {"champion_update", "game_mode_update"}:
            kind = "gameplay_update"
        else:
            kind = "gameplay_release"
        strategy = (
            "timeline"
            if subtopic in {"champion_update", "game_mode_update"}
            else "release"
        )
        key_prefix = (
            "gameplay"
            if subtopic in {"champion_update", "game_mode_update"}
            else "release"
        )
        return [
            _route(
                kind,
                strategy,
                product_scope,
                f"{key_prefix}:{product_scope}:{products[0]}",
            )
        ]

    if subtopic == "shop_rotation":
        period = _iso_week(published_at)
        if not period:
            return []
        return [
            _route(
                "commercial_offer",
                "recurring_window",
                product_scope,
                f"shop_rotation:{product_scope}:{period}",
            )
        ]

    if subtopic == "free_reward":
        activities = _entity_names(
            entities,
            types={"activity"},
            roles={"core", "affected"},
        )
        if activities:
            return [
                _route(
                    "player_activity",
                    "timeline",
                    product_scope,
                    f"activity:{product_scope}:{activities[0]}",
                )
            ]
        rewards = _entity_names(
            entities,
            types={"skin", "champion", "product", "other"},
            roles={"core", "affected"},
        )
        if not rewards:
            return []
        return [
            _route(
                "player_activity",
                "singleton",
                product_scope,
                f"free_reward:{product_scope}:{rewards[0]}",
            )
        ]

    if subtopic in {"shop_offer", "merch"}:
        products = _entity_names(
            entities,
            types={"skin", "champion", "activity", "product", "other"},
            roles={"core", "affected"},
        )
        if not products:
            return []
        return [
            _route(
                "commercial_offer",
                "release",
                product_scope,
                f"{subtopic}:{product_scope}:{products[0]}",
            )
        ]

    if subtopic == "free_rotation":
        period = _iso_week(published_at)
        if not period:
            return []
        return [
            _route(
                "player_activity",
                "recurring_window",
                product_scope,
                f"free_rotation:{product_scope}:{period}",
            )
        ]

    if topic == "esports" and subtopic in {
        "match_schedule",
        "match_result",
        "standings_qualification",
    }:
        date_key = _event_date(
            text,
            facets,
            published_at,
            allow_publication_fallback=subtopic == "match_result",
        )
        leagues = _entity_names(entities, types={"league", "tournament"})
        teams = sorted(set(_entity_names(entities, types={"team"})))
        if not date_key:
            return []
        if subtopic == "standings_qualification":
            if not leagues:
                return []
            league = leagues[0]
            subject = teams[0] if teams else league
            return [
                _route(
                    "qualification_change",
                    "timeline",
                    product_scope,
                    f"qualification:{league}:{year}:{subject}",
                )
            ]
        if len(teams) == 2:
            return [
                _route(
                    "esports_match",
                    "timeline",
                    product_scope,
                    f"match:{date_key}:{'-vs-'.join(teams)}",
                )
            ]
        if not leagues:
            return []
        league = leagues[0]
        kind = "esports_match" if subtopic == "match_result" else "esports_schedule"
        return [
            _route(
                kind,
                "calendar_day",
                product_scope,
                f"matchday:{league}:{date_key}",
            )
        ]

    if subtopic == "ticketing":
        date_key = _event_date(
            text,
            facets,
            published_at,
            allow_publication_fallback=False,
        )
        tournaments = _entity_names(entities, types={"league", "tournament", "activity"})
        if not tournaments or not date_key:
            return []
        return [
            _route(
                "commercial_offer",
                "singleton",
                product_scope,
                f"ticketing:{tournaments[0]}:{date_key}",
            )
        ]

    if topic == "activity" or subtopic in {"in_game_activity", "event_pass", "community_event"}:
        activities = _entity_names(entities, types={"activity", "product"})
        if not activities:
            return []
        return [
            _route(
                "community_activity" if subtopic == "community_event" else "player_activity",
                "timeline",
                product_scope,
                f"activity:{product_scope}:{activities[0]}",
            )
        ]

    if subtopic in {"maintenance", "outage", "security", "disciplinary"}:
        targets = _entity_names(entities, roles={"core", "affected"})
        if not date_key:
            return []
        kind = {
            "security": "security_notice",
            "disciplinary": "disciplinary_action",
        }.get(subtopic, "service_incident")
        target = targets[0] if targets else subtopic
        return [
            _route(
                kind,
                "timeline",
                product_scope,
                f"incident:{product_scope}:{date_key}:{target}",
            )
        ]

    if subtopic in {"music_video", "partnership", "corporate"}:
        subjects = _entity_names(
            entities,
            types={"organization", "product", "person", "other"},
            roles={"core", "affected"},
        )
        if not subjects:
            return []
        kind = (
            "media_release"
            if subtopic == "music_video"
            else "corporate_announcement"
        )
        return [
            _route(
                kind,
                "release" if subtopic == "music_video" else "singleton",
                product_scope,
                f"{subtopic}:{product_scope}:{subjects[0]}",
            )
        ]
    return []


def normalize_entities(values: list[dict[str, object]]) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    identities: set[tuple[str, str]] = set()
    for value in values:
        entity = dict(value)
        raw_type = str(entity.get("type") or "unknown").strip().casefold()
        entity_type = ENTITY_TYPE_ALIASES.get(raw_type, raw_type)
        if entity_type not in ENTITY_TYPES:
            raise ValueError(f"unsupported entity type: {raw_type or '<empty>'}")
        entity["type"] = entity_type
        entity["display_name"] = str(
            entity.get("display_name") or entity.get("name") or ""
        )
        entity["canonical_name"] = canonical_entity_name(
            entity_type,
            str(entity.get("canonical_name") or entity["display_name"]),
        )
        canonical_key = _key_token(str(entity["canonical_name"]))
        if not canonical_key:
            continue
        identity = (entity_type, canonical_key)
        if identity in identities:
            continue
        identities.add(identity)
        entity["canonical_id"] = str(
            entity.get("canonical_id") or f"{entity_type}:{canonical_key}"
        )
        role = str(entity.get("role") or "context").casefold()
        entity["role"] = (
            role if role in {"core", "context", "affected"} else "context"
        )
        normalized.append(entity)
    return normalized


def normalize_event_mentions(
    values: list[dict[str, object]],
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    identities: set[tuple[object, ...]] = set()
    for value in values[:12]:
        topic = str(value.get("topic") or "other").casefold()
        subtopic = str(value.get("subtopic") or "other").casefold()
        assertion = str(value.get("assertion") or "asserted").casefold()
        membership_role = str(
            value.get("membership_role") or "primary"
        ).casefold()
        if topic not in PRIMARY_TOPICS or subtopic not in SUBTOPICS:
            raise ValueError("event mention must use controlled topic and subtopic")
        if assertion not in EVENT_ASSERTIONS:
            raise ValueError("event mention has unsupported assertion")
        if membership_role not in {"primary", "component", "cross_ref"}:
            raise ValueError("event mention has unsupported membership role")
        entities = normalize_entities(
            [
                dict(entity)
                for entity in value.get("identity_entities", [])
                if isinstance(entity, dict)
            ]
        )
        if not entities:
            continue
        temporal = value.get("temporal")
        temporal_value = dict(temporal) if isinstance(temporal, dict) else {}
        identity = (
            topic,
            subtopic,
            assertion,
            str(temporal_value.get("event_date") or ""),
            tuple(
                (entity["type"], entity["canonical_id"])
                for entity in entities
            ),
        )
        if identity in identities:
            continue
        identities.add(identity)
        normalized.append(
            {
                "topic": topic,
                "subtopic": subtopic,
                "identity_entities": entities,
                "assertion": assertion,
                "temporal": temporal_value,
                "membership_role": membership_role,
            }
        )
    return normalized
