from collections.abc import Mapping
from datetime import date, datetime
from typing import Any


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

    existing_date = _occurrence_date(existing_identity)
    incoming_date = _occurrence_date(incoming_identity)
    if existing_date and incoming_date and existing_date != incoming_date:
        return f"match_date 明确冲突：candidate={existing_date}, message={incoming_date}"

    for key in ("stage", "round"):
        existing_value = _normalized_scalar(existing_identity.get(key))
        incoming_value = _normalized_scalar(incoming_identity.get(key))
        if existing_value and incoming_value and existing_value != incoming_value:
            return (
                f"{key} 明确冲突：candidate={existing_identity[key]!s}, "
                f"message={incoming_identity[key]!s}"
            )
    return None


def _normalized_scalar(value: Any) -> str | None:
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = " ".join(str(value).strip().casefold().split())
    return normalized or None


def _occurrence_date(identity: Mapping[str, Any]) -> str | None:
    value = identity.get("match_date")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        return value.strip()

    scheduled_at = identity.get("scheduled_at")
    if isinstance(scheduled_at, datetime):
        return scheduled_at.date().isoformat()
    if isinstance(scheduled_at, str) and len(scheduled_at.strip()) >= 10:
        return scheduled_at.strip()[:10]
    return None
