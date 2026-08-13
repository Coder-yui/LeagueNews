import json
import re
from datetime import date, datetime
from typing import Any, Final

from app.domain.event_families import canonicalize_event_anchors


_UNKNOWN_VALUES = frozenset({"", "unknown", "未知", "n/a", "none", "null"})
_MATCH_SEPARATOR = re.compile(r"\s*(?:_?vs\.?_?|对阵)\s*", re.IGNORECASE)
_CHANGE_PAIR = re.compile(
    r"(?<![a-z0-9])(\d+(?:\.\d+)?)\s*"
    r"(?:→|->|提高到|提升至|降低到|降至|增加到|减少到|改为|至|to)\s*"
    r"(\d+(?:\.\d+)?)(?![a-z0-9])",
    re.IGNORECASE,
)
_PATCH_VERSION = re.compile(
    r"^(?:(?:patch|version|版本)[:\s-]*)?(\d{1,2}\.\d{1,2})$",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_ZH_DATE = re.compile(r"(?<!\d)(\d{1,2})月(\d{1,2})日")
_COMPACT_TEXT = re.compile(r"[^a-z0-9\u4e00-\u9fff]+", re.IGNORECASE)
_LEAGUE_TEXT = re.compile(
    r"(?<![a-z0-9])(lpl|lck|lec|lcs|lcp|cblol|msi|worlds)(?![a-z0-9])",
    re.IGNORECASE,
)
_TEAM_KEYS = (
    "team1",
    "team2",
    "home_team",
    "away_team",
    "team_home",
    "team_away",
)
_DATE_KEYS = ("date", "match_date", "event_date", "match_time")
_KEY_ALIASES: Final[dict[str, str]] = {
    "patch": "patch_version",
    "game_version": "patch_version",
    "activity": "activity_name",
    "offer": "offer_name",
    "release": "release_name",
    "window": "window_start",
}
# Each alternative is a complete identity shape, ordered from strongest to weakest.
# Optional facts are deliberately absent: learning a release date or market later must
# not change the identity of an existing Event.
_IDENTITY_SHAPES: Final[dict[str, tuple[tuple[str, ...], ...]]] = {
    "gameplay_release": (("release_name",), ("champion",), ("game_mode",)),
    "cosmetic_release": (("release_name",), ("skin_series",), ("skin", "patch_version")),
    "player_activity": (("activity_name",), ("code",)),
    "commercial_offer": (
        ("shop", "market", "rotation_period"),
        ("offer_name",),
        ("shop", "market", "window_start"),
    ),
    "service_incident": (
        ("incident",),
        ("service", "region", "started_at"),
        ("system", "region", "started_at"),
    ),
    "security_enforcement": (
        ("case",),
        ("action", "region", "effective_at"),
        ("action", "region", "window_start"),
    ),
    "esports_schedule": (
        ("tournament", "date"),
        ("league", "date"),
        ("tournament", "stage", "window_start"),
    ),
    "roster_change": (
        ("player", "season"),
        ("player", "window_start"),
        ("player", "team"),
    ),
    "esports_rules": (
        ("tournament", "rule"),
        ("league", "rule", "season"),
        ("league", "rule", "effective_at"),
    ),
    "universe_release": (("release_name",), ("character", "published_at")),
    "media_release": (("release_name",), ("product", "media_type", "published_at")),
    "corporate_change": (
        ("organization", "partner"),
        ("organization", "change", "effective_at"),
    ),
    "platform_service": (
        ("service", "region", "effective_at"),
        ("system", "region", "effective_at"),
    ),
    "other_named_development": (("development_name",), ("release_name",)),
}


def event_identity_contract(event_family: str) -> list[list[str]]:
    if event_family == "gameplay_balance":
        return [
            ["patch_version"],
            ["one of champion/item/rune/system", "numeric change evidence"],
        ]
    if event_family == "esports_match":
        return [
            ["two teams present in evidence_excerpt", "league", "date or source-relative date"],
        ]
    return [list(shape) for shape in _IDENTITY_SHAPES.get(event_family, ())]


def _values(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    return [
        str(item).strip()
        for item in values
        if str(item).strip().casefold() not in _UNKNOWN_VALUES
    ]


def _normalized_scalar(value: str) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff:>.+-]+", "-", value.casefold()).strip("-")


def _normalized_value(value: Any) -> str | list[str] | None:
    values = sorted({_normalized_scalar(item) for item in _values(value)})
    values = [item for item in values if item]
    if not values:
        return None
    return values[0] if len(values) == 1 else values


def balance_change_signature(text: str) -> str | None:
    pairs = sorted(f"{left}>{right}" for left, right in _CHANGE_PAIR.findall(text))
    return "changes:" + "|".join(pairs) if pairs else None


def select_identity_evidence(
    event_family: str,
    *,
    message_text: str,
    mention_excerpt: str,
) -> str:
    """Prefer mention-scoped balance facts over unrelated pairs in a roundup message."""
    if event_family == "gameplay_balance" and balance_change_signature(mention_excerpt):
        return mention_excerpt
    return "\n".join(value for value in (message_text, mention_excerpt) if value)


def identity_anchors_with_hints(
    event_family: str,
    proposed_anchors: dict[str, Any],
    message_anchors: dict[str, Any],
) -> dict[str, Any]:
    """Combine mention identity with only deterministic family-safe message hints."""
    combined = dict(proposed_anchors)
    if event_family == "gameplay_balance":
        for key in ("patch_version", "champion", "item", "rune", "system"):
            hint = message_anchors.get(key)
            if len(_values(hint)) == 1:
                combined[key] = hint
    elif event_family == "esports_match" and "league" not in combined:
        if "league" in message_anchors:
            combined["league"] = message_anchors["league"]
    return combined


def _team(value: str) -> str:
    normalized = value.split(":", 1)[-1]
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", normalized.casefold())


def _compact(value: str) -> str:
    return _COMPACT_TEXT.sub("", value.casefold())


def _value_is_supported(
    value: Any,
    *,
    evidence_text: str,
    message_anchors: dict[str, Any],
) -> bool:
    evidence = _compact(evidence_text)
    anchor_values = {
        _compact(candidate.split(":", 1)[-1])
        for anchor_value in message_anchors.values()
        for candidate in _values(anchor_value)
    }
    for candidate in _values(value):
        normalized = _compact(candidate.split(":", 1)[-1])
        if not normalized:
            continue
        if normalized in evidence or normalized in anchor_values:
            return True
    return False


def _observed_date(observed_on: date | datetime | None) -> date | None:
    if isinstance(observed_on, datetime):
        return observed_on.date()
    return observed_on


def _date_from_evidence(
    evidence_text: str,
    *,
    observed_on: date | datetime | None,
) -> date | None:
    observed = _observed_date(observed_on)
    explicit = _ISO_DATE.search(evidence_text)
    if explicit:
        try:
            return date(*(int(value) for value in explicit.groups()))
        except ValueError:
            return None
    chinese = _ZH_DATE.search(evidence_text)
    if chinese and observed is not None:
        try:
            return date(observed.year, int(chinese.group(1)), int(chinese.group(2)))
        except ValueError:
            return None
    if observed is None:
        return None
    if re.search(r"明天|明日", evidence_text):
        return date.fromordinal(observed.toordinal() + 1)
    if re.search(r"昨天|昨日", evidence_text):
        return date.fromordinal(observed.toordinal() - 1)
    return observed


def _explicit_date_from_evidence(
    evidence_text: str,
    *,
    observed_on: date | datetime | None,
) -> date | None:
    observed = _observed_date(observed_on)
    explicit = _ISO_DATE.search(evidence_text)
    if explicit:
        try:
            return date(*(int(value) for value in explicit.groups()))
        except ValueError:
            return None
    chinese = _ZH_DATE.search(evidence_text)
    if chinese and observed is not None:
        try:
            return date(observed.year, int(chinese.group(1)), int(chinese.group(2)))
        except ValueError:
            return None
    return None


def resolve_esports_match_anchors(
    anchors: dict[str, Any],
    *,
    message_text: str,
    mention_excerpt: str,
    observed_on: date | datetime | None,
) -> dict[str, Any]:
    resolved = dict(anchors)
    explicit_date = _explicit_date_from_evidence(
        mention_excerpt, observed_on=observed_on
    ) or _explicit_date_from_evidence(message_text, observed_on=observed_on)
    if explicit_date is not None:
        resolved["date"] = explicit_date.isoformat()
    elif re.search(r"明天|明日|昨天|昨日", mention_excerpt):
        inferred = _date_from_evidence(mention_excerpt, observed_on=observed_on)
        if inferred is not None:
            resolved["date"] = inferred.isoformat()
    elif (observed := _observed_date(observed_on)) is not None:
        resolved["date"] = observed.isoformat()
    league = _LEAGUE_TEXT.search("\n".join((mention_excerpt, message_text)))
    if league:
        resolved["league"] = league.group(1).casefold()
    return resolved


def _match_pair_is_supported(
    pair: tuple[str, str],
    *,
    message_text: str,
    mention_excerpt: str,
) -> bool:
    separator = r"(?:vs\.?|对战|对阵|\d+\s*[-:]\s*\d+)"
    evidence = "\n".join((mention_excerpt, message_text))
    left, right = (re.escape(team) for team in pair)
    return bool(
        re.search(rf"{left}\s*{separator}\s*{right}", evidence, re.IGNORECASE)
        or re.search(rf"{right}\s*{separator}\s*{left}", evidence, re.IGNORECASE)
    )


def _date_supported(
    event_date: str,
    *,
    evidence_text: str,
    observed_on: date | datetime | None,
) -> bool:
    try:
        expected = date.fromisoformat(event_date)
    except ValueError:
        return False
    inferred = _date_from_evidence(evidence_text, observed_on=observed_on)
    return inferred is None or expected == inferred


def identity_is_supported_by_message(
    event_family: str,
    anchors: dict[str, Any],
    *,
    message_text: str,
    mention_excerpt: str,
    message_anchors: dict[str, Any] | None = None,
    observed_on: date | datetime | None = None,
) -> bool:
    """Require current-message evidence for identity; candidates are never evidence."""
    identity = project_event_identity(
        event_family,
        anchors,
        evidence_text=select_identity_evidence(
            event_family,
            message_text=message_text,
            mention_excerpt=mention_excerpt,
        ),
        observed_on=observed_on,
    )
    if not identity:
        return False
    known_anchors = message_anchors or {}
    if event_family == "esports_match":
        pair_values = str(identity["match"]).split("_vs_", 1)
        if len(pair_values) != 2 or not _match_pair_is_supported(
            (pair_values[0], pair_values[1]),
            message_text=message_text,
            mention_excerpt=mention_excerpt,
        ):
            return False
        resolved = resolve_esports_match_anchors(
            anchors,
            message_text=message_text,
            mention_excerpt=mention_excerpt,
            observed_on=observed_on,
        )
        return _date_supported(
            str(identity["date"]),
            evidence_text=str(resolved.get("date") or ""),
            observed_on=observed_on,
        )
    if event_family == "gameplay_balance":
        patch = identity.get("patch_version")
        if patch is not None:
            return _value_is_supported(
                patch,
                evidence_text=message_text,
                message_anchors=known_anchors,
            )
        return bool(balance_change_signature(mention_excerpt or message_text)) and any(
            _value_is_supported(
                identity[key],
                evidence_text="\n".join((message_text, mention_excerpt)),
                message_anchors=known_anchors,
            )
            for key in ("champion", "item", "rune", "system")
            if key in identity
        )
    return True


def _match_pair(anchors: dict[str, Any]) -> tuple[str, str] | None:
    explicit = [value for key in _TEAM_KEYS for value in _values(anchors.get(key))]
    if len(explicit) == 2:
        pair = tuple(sorted(_team(value) for value in explicit))
        if pair[0] and pair[0] != pair[1]:
            return pair
    teams = _values(anchors.get("team"))
    if len(teams) == 2:
        pair = tuple(sorted(_team(value) for value in teams))
        if pair[0] and pair[0] != pair[1]:
            return pair
    for value in _values(anchors.get("match")):
        parts = _MATCH_SEPARATOR.split(value.split(":", 1)[-1])
        if len(parts) == 2:
            pair = tuple(sorted(_team(part) for part in parts))
            if pair[0] and pair[0] != pair[1]:
                return pair
    return None


def _event_date(anchors: dict[str, Any]) -> str | None:
    for key in _DATE_KEYS:
        values = _values(anchors.get(key))
        if values:
            return values[0][:10]
    return None


def _normalized_anchors(anchors: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {}
    for raw_key, raw_value in anchors.items():
        key = _KEY_ALIASES.get(raw_key.casefold(), raw_key.casefold())
        value = _normalized_value(raw_value)
        if value is not None:
            normalized[key] = value
    return normalized


def _project_generic(event_family: str, anchors: dict[str, Any]) -> dict[str, Any]:
    normalized = _normalized_anchors(anchors)
    for shape in _IDENTITY_SHAPES.get(event_family, ()):
        if all(key in normalized for key in shape):
            return {key: normalized[key] for key in shape}
    return {}


def _patch_identity(value: Any) -> str | None:
    for candidate in _values(value):
        match = _PATCH_VERSION.fullmatch(candidate)
        if match:
            return f"patch:{match.group(1)}"
    return None


def project_event_identity(
    event_family: str,
    anchors: dict[str, Any],
    *,
    evidence_text: str = "",
    observed_on: date | datetime | None = None,
) -> dict[str, Any]:
    normalized = canonicalize_event_anchors(event_family, anchors)
    if event_family == "gameplay_balance":
        patch = _patch_identity(
            normalized.get("patch_version")
            or normalized.get("patch")
            or normalized.get("game_version")
        )
        if patch is not None:
            return {"patch_version": patch}
        subjects = {
            key: value
            for key in ("champion", "item", "rune", "system")
            if (value := _normalized_value(normalized.get(key))) is not None
        }
        computed_signature = balance_change_signature(evidence_text)
        signature = (
            computed_signature
            if evidence_text.strip()
            else _normalized_value(normalized.get("change_signature"))
        )
        return {**subjects, "change_signature": signature} if subjects and signature else {}
    if event_family == "esports_match":
        pair = _match_pair(normalized)
        event_date = _event_date(normalized) or (
            inferred.isoformat()
            if (inferred := _date_from_evidence(evidence_text, observed_on=observed_on))
            else None
        )
        league_values = _values(normalized.get("league"))
        if not pair or not event_date or not league_values:
            return {}
        return {
            "match": f"{pair[0]}_vs_{pair[1]}",
            "date": event_date,
            "league": _normalized_scalar(league_values[0]),
        }
    return _project_generic(event_family, normalized)


def event_identity_key(
    event_family: str,
    anchors: dict[str, Any],
    *,
    evidence_text: str = "",
    observed_on: date | datetime | None = None,
) -> str | None:
    identity = project_event_identity(
        event_family,
        anchors,
        evidence_text=evidence_text,
        observed_on=observed_on,
    )
    if not identity:
        return None
    return json.dumps(
        [event_family, identity],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def event_identity_matches(
    event_family: str,
    existing_anchors: dict[str, Any],
    incoming_anchors: dict[str, Any],
    *,
    evidence_text: str = "",
    observed_on: date | datetime | None = None,
) -> bool:
    existing = project_event_identity(event_family, existing_anchors)
    if not existing:
        return False
    if event_family in {"gameplay_balance", "esports_match"}:
        if event_family == "gameplay_balance" and "change_signature" in existing:
            incoming = project_event_identity(
                event_family,
                {
                    **incoming_anchors,
                    "patch_version": None,
                    "patch": None,
                    "game_version": None,
                },
                evidence_text=evidence_text,
                observed_on=observed_on,
            )
        else:
            incoming = project_event_identity(
                event_family,
                incoming_anchors,
                evidence_text=evidence_text,
                observed_on=observed_on,
            )
        return existing == incoming

    normalized_incoming = _normalized_anchors(
        canonicalize_event_anchors(event_family, incoming_anchors)
    )
    return all(normalized_incoming.get(key) == value for key, value in existing.items())
