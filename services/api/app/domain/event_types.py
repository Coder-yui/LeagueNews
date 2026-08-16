from typing import Final, Literal, get_args


AGGREGATION_POLICY_VERSION: Final = "event-aggregation-v8-match-time-boundary"
IMPORTANCE_POLICY_VERSION: Final = "event-importance-v4-normalized-item-projection"
CREDIBILITY_POLICY_VERSION: Final = "event-credibility-v1"
HEAT_POLICY_VERSION: Final = "event-heat-v1"

EventFamily = Literal[
    "gameplay_balance",
    "gameplay_release",
    "cosmetic_release",
    "player_activity",
    "commercial_offer",
    "service_incident",
    "security_enforcement",
    "esports_match",
    "esports_schedule",
    "roster_change",
    "esports_rules",
    "universe_release",
    "media_release",
    "corporate_change",
    "platform_service",
    "other_named_development",
]
EventRelation = Literal[
    "reports",
    "supports",
    "confirms",
    "denies",
    "corrects",
    "mentions",
]
EventSourceRole = Literal[
    "responsible_official",
    "direct_subject",
    "first_party_participant",
    "independent_media",
    "known_leaker",
    "ordinary_account",
    "republisher",
    "unknown",
]
EventMateriality = Literal[
    "material_update",
    "corroboration_only",
    "duplicate",
    "context_only",
]
EventLifecycle = Literal[
    "unconfirmed",
    "developing",
    "confirmed",
    "disputed",
    "denied",
    "resolved",
    "stale",
]
CredibilityLevel = Literal[
    "unverified",
    "plausible",
    "corroborated",
    "officially_confirmed",
    "disputed",
    "denied",
]

EVENT_FAMILIES: Final = frozenset(get_args(EventFamily))
EVENT_FAMILY_ORDER: Final = tuple(get_args(EventFamily))
EVENT_RELATIONS: Final = frozenset(get_args(EventRelation))
EVENT_SOURCE_ROLES: Final = frozenset(get_args(EventSourceRole))
EVENT_MATERIALITIES: Final = frozenset(get_args(EventMateriality))
EVENT_LIFECYCLES: Final = frozenset(get_args(EventLifecycle))
CREDIBILITY_LEVELS: Final = frozenset(get_args(CredibilityLevel))
