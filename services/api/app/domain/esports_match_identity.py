from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any, Final

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

    # participants is the explicit match subject. When both identities name
    # participants and the sets differ, the candidate cannot be the same match
    # regardless of any other field. The only tolerated shape is one-sided
    # follow-up evidence: a single named side that belongs to the candidate's
    # participants ("JDG 拿下第一局" naming just JDG against a JDG/LGD match).
    # A full incoming subject must match the candidate's sides exactly; a side
    # outside the candidate's participants is always a hard conflict. One side
    # missing participants entirely stays unknown rather than a conflict;
    # identical participants are only a positive compatibility signal, never
    # proof of the same occurrence.
    existing_participants = _normalized_participants(existing_identity.get("participants"))
    incoming_participants = _normalized_participants(incoming_identity.get("participants"))
    if (
        existing_participants
        and incoming_participants
        and existing_participants != incoming_participants
        and not (
            len(incoming_participants) == 1
            and incoming_participants < existing_participants
        )
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
        # stage/round are free-text labels; different sources abbreviate the same
        # real-world stage differently ("常规赛组内赛" vs "组内赛"). A containment
        # relation (either normalized value containing the other) is an abbreviated
        # spelling of the same label, not an explicit difference — treating it as a
        # conflict would wrongly veto the create guard, the apply fence and the
        # duplicate audit for what is really the same occurrence. Only values that
        # differ with no containment relationship ("Group Stage" vs "Playoffs") are
        # explicit conflicts. Structured fields (dates, datetimes, external ids)
        # above still compare exactly.
        if (
            existing_value
            and incoming_value
            and existing_value != incoming_value
            and existing_value not in incoming_value
            and incoming_value not in existing_value
        ):
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


def normalized_match_participants(participants: Any) -> list[str]:
    """Canonical, de-duplicated, order-preserving match participant names."""
    if not isinstance(participants, list):
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for part in participants:
        if not isinstance(part, str) or not part.strip():
            continue
        canonical = canonical_entity_name("team", part).strip()
        token = " ".join(canonical.casefold().split())
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(canonical)
    return normalized


def esports_match_has_subject(identity: Mapping[str, Any] | None) -> bool:
    """A recallable esports_match needs a complete match subject.

    A concrete match Event names exactly two distinct normalized participants — the
    two sides of the match. ``external_match_id`` is additional strong identity
    evidence, but it never substitutes for the match sides: an Event without both
    participants cannot be a user-visible concrete match and must not participate in
    candidate recall (repair deletes and rebuilds such Events).
    """
    match_identity = match_identity_from_anchors(identity)
    return len(normalized_match_participants(match_identity.get("participants"))) == 2


def esports_match_attach_subject(identity: Mapping[str, Any] | None) -> bool:
    """An attach needs at least one explicit participant of the current match.

    Follow-up evidence ("JDG 拿下第一局") may name only one side, so attach accepts a
    single participant. An empty identity carries no match subject at all and can
    neither attach nor create.
    """
    match_identity = match_identity_from_anchors(identity)
    return bool(normalized_match_participants(match_identity.get("participants")))


_PLACEHOLDER_PARTICIPANT_MARKERS: Final = (
    "未知",
    "待定",
    "不明",
    "unknown",
    "tbd",
)
_PLACEHOLDER_PARTICIPANT_NAMES: Final[frozenset[str]] = frozenset(
    {"?", "对手", "opponent"}
)


def placeholder_match_participants(identity: Mapping[str, Any] | None) -> list[str]:
    """Return placeholder names that are not real match participants.

    A participant subject must name real teams. Placeholder wording like
    "未知对手" / "TBD" explicitly states the side is *not* known: such a value can
    never satisfy the two-sided create subject (nor a one-sided attach subject),
    because it would fabricate a concrete-match Event whose subject is unknown —
    exactly the shell the subject contract forbids. Real team names never carry
    these markers.
    """
    match_identity = match_identity_from_anchors(identity)
    placeholders: list[str] = []
    for name in normalized_match_participants(match_identity.get("participants")):
        token = name.casefold()
        if token in _PLACEHOLDER_PARTICIPANT_NAMES or any(
            marker in token for marker in _PLACEHOLDER_PARTICIPANT_MARKERS
        ):
            placeholders.append(name)
    return placeholders


def match_identity_from_message_entities(
    entities: list[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Derive a high-confidence incoming match subject from extracted entities.

    ``NormalizedItem.entities`` is a list of extracted entity dicts, each carrying
    ``type``, ``role`` (``core`` / ``context`` / ``affected``) and ``canonical_name``.
    A message may mention many teams ("WBG 2:1 IG，下一轮将面对 JDG；LGD 此前……"),
    so *all* team entities can never be the current match's participants. Only two
    shapes are confident enough to become the pre-LLM ``participants`` signal:

    A. exactly two team entities with ``role == "core"`` -> those two canonical names;
    B. otherwise, exactly two team entities in the whole message -> those two.

    Every other shape (three or more teams, or one team alone) leaves the incoming
    participants unknown: the identity gate then keeps every structurally compatible
    candidate and the LLM keeps full semantic control. Names prefer the normalized
    ``canonical_name`` over raw display names. Dates, stage/round, scheduled_at and
    external ids cannot be structured without the model and stay unknown here; they
    are checked by the fences once the model supplies ``match_identity``.
    """
    if not isinstance(entities, list):
        return {}
    team_entities: list[Mapping[str, Any]] = []
    for entity in entities:
        if not isinstance(entity, Mapping):
            continue
        if str(entity.get("type") or "").strip().casefold() not in {"team", "club"}:
            continue
        name = (
            entity.get("canonical_name")
            or entity.get("display_name")
            or entity.get("name")
        )
        if not isinstance(name, str) or not name.strip():
            continue
        team_entities.append(entity)

    core_names = [
        str(entity.get("canonical_name") or entity.get("display_name") or entity.get("name"))
        for entity in team_entities
        if str(entity.get("role") or "").strip().casefold() == "core"
    ]
    candidate_names = core_names if len(core_names) == 2 else (
        [
            str(entity.get("canonical_name") or entity.get("display_name") or entity.get("name"))
            for entity in team_entities
        ]
        if len(team_entities) == 2
        else []
    )
    participants = normalized_match_participants(candidate_names)
    if len(participants) != 2:
        return {}
    return {"participants": participants}


def _normalized_scalar(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = " ".join(str(value).strip().casefold().split())
    return normalized or None


def _identity_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def ungrounded_match_identity_fields(
    identity: Mapping[str, Any] | None,
    *,
    message_published_at: datetime | None,
    text: str,
) -> dict[str, str]:
    """Return esports_match identity fields the message itself cannot support.

    The model is instructed to leave unevidenced identity fields missing, but a
    fabricated value is worse than a missing one: a hallucinated future match_date
    hard-conflicts the real same-match candidates at the identity gate and forces
    a false split (an early game-progress report created with a wrong future date
    can never be attached by the later true-date reports of the same match). Two
    deterministic checks, no thresholds on how old a match may be:

    - occurrence dates: esports_match is the lifecycle of a match that is actually
      happening or finished. A match_date/scheduled_at more than one day after the
      message's publish date (one day of timezone slack) reports a future
      occurrence — that is a schedule, not a match, and is always ungrounded.
      Same-day and past dates stay allowed ("昨日赛果" recaps legitimately infer
      the previous day).
    - round: a round label is competition-internal numbering with no inference
      path; it is grounded only when it literally appears in the message text.

    stage/competition/participants are deliberately not checked: free-text labels
    are legitimately abbreviated (containment-compatible) and participants come
    from the message's own entities.
    """
    match_identity = match_identity_from_anchors(identity)
    issues: dict[str, str] = {}
    if message_published_at is not None:
        publish_bound = message_published_at.date() + timedelta(days=1)
        for key in ("match_date", "scheduled_at"):
            value = match_identity.get(key)
            occurrence = _identity_date(value)
            if occurrence is not None and occurrence > publish_bound:
                issues[key] = (
                    f"{key}={value} 晚于消息发布时间：esports_match 是已发生比赛的生命周期，"
                    "未来的比赛安排属于 esports_schedule；当前消息无法支持该值"
                )
    round_value = _normalized_scalar(match_identity.get("round"))
    if round_value and round_value not in text.casefold():
        issues["round"] = (
            f"round={match_identity.get('round')} 在消息原文中没有任何字面证据；"
            "没有证据的字段必须保持缺失，不能推断或补齐"
        )
    return issues


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
