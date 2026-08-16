from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from app.domain.message_entities import canonical_entity_name


MATCH_IDENTITY_KEYS = (
    "participants",
    "competition",
    "stage",
    "round",
    "match_date",
    "scheduled_at",
    "series_format",
    "external_match_id",
)


def match_identity_from_anchors(anchors: Mapping[str, Any] | None) -> dict[str, Any]:
    if not anchors:
        return {}
    return {
        key: anchors[key]
        for key in MATCH_IDENTITY_KEYS
        if anchors.get(key) not in (None, "", [])
    }


def merge_match_identity(
    anchors: Mapping[str, Any] | None,
    identity: Mapping[str, Any] | None,
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Merge known occurrence fields, preserving established metadata by default."""

    merged = dict(anchors or {})
    for key in MATCH_IDENTITY_KEYS:
        value = (identity or {}).get(key)
        if value not in (None, "", []) and (
            overwrite or merged.get(key) in (None, "", [])
        ):
            merged[key] = value
    return merged


def esports_match_identity_conflict(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> str | None:
    """Return a hard-conflict reason only when both identities state incompatible facts."""

    existing_identity = match_identity_from_anchors(existing)
    incoming_identity = match_identity_from_anchors(incoming)

    existing_external_id = _normalized_scalar(existing_identity.get("external_match_id"))
    incoming_external_id = _normalized_scalar(incoming_identity.get("external_match_id"))
    if (
        existing_external_id
        and incoming_external_id
        and existing_external_id != incoming_external_id
    ):
        return (
            "external_match_id 明确冲突："
            f"candidate={existing_identity['external_match_id']!s}, "
            f"message={incoming_identity['external_match_id']!s}"
        )

    # participants is the explicit match subject. When both identities name a full
    # participant set and the sets are genuinely distinct (neither is a subset of the
    # other), the candidate cannot be the same match regardless of any other field.
    # One side missing participants stays unknown rather than a conflict; identical
    # participants are only a positive compatibility signal, never proof of the same
    # occurrence. The subset guard keeps an incidental third-team mention in a message
    # (e.g. "参考 JDG 上一场的表现") from wrongly discarding a compatible candidate.
    existing_participants = _normalized_participants(existing_identity.get("participants"))
    incoming_participants = _normalized_participants(incoming_identity.get("participants"))
    if (
        existing_participants
        and incoming_participants
        and existing_participants != incoming_participants
        and not existing_participants.issubset(incoming_participants)
        and not incoming_participants.issubset(existing_participants)
    ):
        return (
            "participants 明确冲突："
            f"candidate={sorted(existing_participants)}, "
            f"message={sorted(incoming_participants)}"
        )

    existing_date = _match_date(existing_identity)
    incoming_date = _match_date(incoming_identity)
    if existing_date and incoming_date and existing_date != incoming_date:
        return f"match_date 明确冲突：candidate={existing_date}, message={incoming_date}"

    # scheduled_at compares full normalized datetimes, never degenerate to a date,
    # so two matches on the same day at different times stay distinct occurrences.
    existing_scheduled = _occurrence_datetime(existing_identity)
    incoming_scheduled = _occurrence_datetime(incoming_identity)
    if (
        existing_scheduled
        and incoming_scheduled
        and existing_scheduled != incoming_scheduled
    ):
        return (
            "scheduled_at 明确冲突："
            f"candidate={existing_scheduled.isoformat()}, "
            f"message={incoming_scheduled.isoformat()}"
        )

    # One side may only carry a match_date while the other only has scheduled_at.
    # That can still prove a date-level conflict, but never exact scheduled_at equality.
    if not (existing_date and incoming_date) and not (
        existing_scheduled and incoming_scheduled
    ):
        existing_day = existing_date or (
            existing_scheduled.date().isoformat() if existing_scheduled else None
        )
        incoming_day = incoming_date or (
            incoming_scheduled.date().isoformat() if incoming_scheduled else None
        )
        if existing_day and incoming_day and existing_day != incoming_day:
            return (
                f"occurrence 日期明确冲突：candidate={existing_day}, message={incoming_day}"
            )

    for key in ("stage", "round"):
        existing_value = _normalized_scalar(existing_identity.get(key))
        incoming_value = _normalized_scalar(incoming_identity.get(key))
        if existing_value and incoming_value and existing_value != incoming_value:
            return (
                f"{key} 明确冲突：candidate={existing_identity[key]!s}, "
                f"message={incoming_identity[key]!s}"
            )
    return None


def esports_match_same_occurrence_evidence(
    existing: Mapping[str, Any] | None,
    incoming: Mapping[str, Any] | None,
) -> str | None:
    """Return a strong positive same-occurrence reason, or None.

    This is deliberately conservative and is the gate Python uses to
    deterministically reject an erroneous ``create``. Only explicit, agreeing
    occurrence facts produce evidence: an equal ``external_match_id`` (independent
    of participants), participants plus an equal explicit match_date, participants
    plus equal scheduled_at (full datetime, not date), or participants plus equal
    competition/stage/round. It never treats "no conflict" as proof of the same
    match. Any hard conflict (match_date/scheduled_at/date/stage/round/external id
    differing) returns None. ``participants`` alone is never sufficient. This rule
    is deliberately stricter than the LLM's semantic continuation judgment, which
    may still decide to attach based on continuous lifecycle + recency + context.
    """
    if esports_match_identity_conflict(existing, incoming):
        return None
    existing_identity = match_identity_from_anchors(existing)
    incoming_identity = match_identity_from_anchors(incoming)

    # external_match_id is decisive on its own: an equal explicit id is strong
    # evidence even when participants are absent, and a differing id is a conflict.
    existing_external = _normalized_scalar(existing_identity.get("external_match_id"))
    incoming_external = _normalized_scalar(incoming_identity.get("external_match_id"))
    if existing_external and incoming_external and existing_external == incoming_external:
        return f"external_match_id 一致：{existing_identity['external_match_id']!s}"

    existing_participants = _normalized_participants(existing_identity.get("participants"))
    incoming_participants = _normalized_participants(incoming_identity.get("participants"))
    if not existing_participants or not incoming_participants:
        return None
    if existing_participants != incoming_participants:
        return None

    existing_date = _match_date(existing_identity)
    incoming_date = _match_date(incoming_identity)
    if existing_date and incoming_date and existing_date == incoming_date:
        return f"match_date 一致：{existing_date}"

    existing_scheduled = _occurrence_datetime(existing_identity)
    incoming_scheduled = _occurrence_datetime(incoming_identity)
    if (
        existing_scheduled
        and incoming_scheduled
        and existing_scheduled == incoming_scheduled
    ):
        return f"scheduled_at 一致：{existing_scheduled.isoformat()}"

    existing_comp = _normalized_scalar(existing_identity.get("competition"))
    incoming_comp = _normalized_scalar(incoming_identity.get("competition"))
    existing_stage = _normalized_scalar(existing_identity.get("stage"))
    incoming_stage = _normalized_scalar(incoming_identity.get("stage"))
    existing_round = _normalized_scalar(existing_identity.get("round"))
    incoming_round = _normalized_scalar(incoming_identity.get("round"))
    if (
        existing_comp
        and incoming_comp
        and existing_comp == incoming_comp
        and existing_stage
        and incoming_stage
        and existing_stage == incoming_stage
        and existing_round
        and incoming_round
        and existing_round == incoming_round
    ):
        return (
            f"competition/stage/round 一致："
            f"{existing_identity['competition']!s}/{existing_identity['stage']!s}/"
            f"{existing_identity['round']!s}"
        )
    return None


def _normalized_participants(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    normalized: set[str] = set()
    for part in value:
        if not isinstance(part, str) or not part.strip():
            continue
        canonical = canonical_entity_name("team", part)
        token = " ".join(canonical.strip().casefold().split())
        if token:
            normalized.add(token)
    return normalized


def esports_match_has_subject(identity: Mapping[str, Any] | None) -> bool:
    """A concrete esports_match needs a recognizable match subject.

    A match Event is only meaningful when it names at least one explicit participant
    (a normal match usually has two sides) or carries an explicit ``external_match_id``.
    A create with neither is rejected, and an Event that lost both cannot keep
    participating in aggregation.
    """
    match_identity = match_identity_from_anchors(identity)
    if _normalized_participants(match_identity.get("participants")):
        return True
    if _normalized_scalar(match_identity.get("external_match_id")):
        return True
    return False


def match_identity_from_message_entities(
    entities: list[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Derive a conservative incoming match identity from already extracted entities.

    ``NormalizedItem.entities`` is a list of extracted entity dicts. The only occurrence
    facts reliably available *before* the LLM decision are the message's explicit team
    entities, which become ``participants`` (a normal match usually has two sides).
    Dates, stage/round, scheduled_at and external ids cannot be structured without the
    model and stay unknown here; they are compared by the code fence at attach/apply
    time once the model supplies ``match_identity``. This is a conservative pre-LLM
    signal: with no team entities it returns an empty identity and the identity gate is
    a no-op rather than a source of false conflicts.
    """
    if not isinstance(entities, list):
        return {}
    participants: list[str] = []
    seen: set[str] = set()
    for entity in entities:
        if not isinstance(entity, Mapping):
            continue
        if str(entity.get("type") or "").strip().casefold() not in {"team", "club"}:
            continue
        name = entity.get("name") or entity.get("display_name") or entity.get("canonical_name")
        if not isinstance(name, str) or not name.strip():
            continue
        key = " ".join(name.strip().casefold().split())
        if key in seen:
            continue
        seen.add(key)
        participants.append(name.strip())
    return {"participants": participants} if participants else {}


def _normalized_scalar(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = " ".join(str(value).strip().casefold().split())
    return normalized or None


def _match_date(identity: Mapping[str, Any]) -> str | None:
    """Extract only an explicit match_date, never folding scheduled_at into a date."""
    value = identity.get("match_date")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _occurrence_datetime(identity: Mapping[str, Any]) -> datetime | None:
    """Extract scheduled_at as a full normalized datetime."""
    value = identity.get("scheduled_at")
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            normalized = datetime.fromisoformat(value.strip())
        except ValueError:
            return None
        return normalized
    return None
