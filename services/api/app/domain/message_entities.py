import re
import unicodedata
from typing import Final, Literal, get_args


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

ENTITY_TYPES: Final = frozenset(get_args(EntityType))
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

_KEY_TOKEN_PATTERN = re.compile(r"[^\w\u4e00-\u9fff]+", re.UNICODE)
_TEAM_SUFFIX_PATTERN = re.compile(
    r"(?:电子竞技俱乐部|电竞俱乐部|电子竞技战队|电竞战队|战队)$",
    re.IGNORECASE,
)
_LATIN_TOKEN_PATTERN = re.compile(
    r"(?<![a-z0-9])[a-z][a-z0-9]{1,15}(?![a-z0-9])",
    re.IGNORECASE,
)
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
_TEAM_ALIASES: Final = {
    # Match identity compares teams from independently extracted messages. Keep
    # the small set of established production aliases in the event display form.
    "ig": "IG",
    "invictus gaming": "IG",
    "wbg": "WBG",
    "weibo gaming": "WBG",
}


def _key_token(value: str) -> str:
    return _KEY_TOKEN_PATTERN.sub("-", value.strip().casefold()).strip("-")[:80]


def canonical_entity_name(entity_type: str, value: str) -> str:
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
        alias = _TEAM_ALIASES.get(" ".join(shortened.casefold().split()))
        if alias:
            return alias
        latin_tokens = _LATIN_TOKEN_PATTERN.findall(shortened)
        if latin_tokens and any("\u4e00" <= char <= "\u9fff" for char in shortened):
            return latin_tokens[0].casefold()
        return shortened
    if entity_type in {"player", "coach", "person"}:
        latin_tokens = _LATIN_TOKEN_PATTERN.findall(normalized)
        if latin_tokens and any("\u4e00" <= char <= "\u9fff" for char in normalized):
            return max(latin_tokens, key=len)
    return normalized


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
        entity["display_name"] = str(entity.get("display_name") or entity.get("name") or "")
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
        entity["canonical_id"] = str(entity.get("canonical_id") or f"{entity_type}:{canonical_key}")
        role = str(entity.get("role") or "context").casefold()
        entity["role"] = role if role in {"core", "context", "affected"} else "context"
        normalized.append(entity)
    return normalized
