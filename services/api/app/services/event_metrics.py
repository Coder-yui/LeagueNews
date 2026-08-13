from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.domain.event_credibility import CredibilityEvidence, calculate_event_credibility
from app.domain.event_heat import HeatEvidence, calculate_event_heat
from app.domain.event_importance import EventImportanceEvidence, calculate_event_importance
from app.domain.event_types import (
    CREDIBILITY_POLICY_VERSION,
    HEAT_POLICY_VERSION,
    IMPORTANCE_POLICY_VERSION,
)
from app.models.event import Event, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.repositories.events import current_event_mention_conditions


_SOURCE_ROLE_RANK = {
    "responsible_official": 100,
    "direct_subject": 90,
    "first_party_participant": 80,
    "independent_media": 70,
    "known_leaker": 60,
    "ordinary_account": 30,
    "republisher": 10,
    "unknown": 0,
}


def _mention_time(mention: EventMention) -> datetime:
    value = (
        mention.source_published_at
        or mention.normalized_item.raw_item.published_at
        or mention.normalized_item.raw_item.ingested_at
        or mention.added_at
    )
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _has_public_media(mention: EventMention) -> bool:
    return any(asset.public_path for asset in mention.normalized_item.raw_item.media_assets)


def refresh_event_importance(event: Event, mentions: list[EventMention]) -> None:
    evidence = []
    for mention in mentions:
        calculation = mention.normalized_item.importance_calculation
        evidence.append(
            EventImportanceEvidence(
                normalized_item_id=mention.normalized_item_id,
                normalized_item_revision=mention.normalized_item_revision,
                mention_index=mention.mention_index,
                profile=calculation.get("importance_profile"),
                domain_score=mention.normalized_item.importance_score,
                materiality=mention.materiality,
            )
        )
    event.importance_score, event.importance_breakdown = calculate_event_importance(evidence)
    event.importance_policy_version = IMPORTANCE_POLICY_VERSION


def _restore_event_projection(
    db: Session, event: Event, mentions: list[EventMention]
) -> None:
    """Restore material-update fields from the newest still-valid revision snapshot."""
    material_mentions = [
        mention for mention in mentions if mention.materiality == "material_update"
    ]
    snapshots: list[tuple[int, dict[str, object]]] = []
    if material_mentions:
        valid_keys = {
            (
                mention.normalized_item_id,
                mention.normalized_item_revision,
                mention.mention_index,
                mention.aggregation_policy_version,
            )
            for mention in material_mentions
        }
        revisions = db.scalars(
            select(EventRevision)
            .where(EventRevision.event_id == event.id)
            .order_by(EventRevision.revision)
        )
        for revision in revisions:
            evidence = revision.evidence_snapshot or {}
            key = (
                evidence.get("normalized_item_id"),
                evidence.get("normalized_item_revision"),
                evidence.get("mention_index"),
                evidence.get("aggregation_policy_version"),
            )
            snapshot = evidence.get("projection_snapshot")
            if key in valid_keys and isinstance(snapshot, dict):
                snapshots.append((revision.revision, snapshot))

    if snapshots:
        _revision, snapshot = max(snapshots, key=lambda value: value[0])
        event.title = str(snapshot.get("title") or event.title)
        event.current_summary = str(
            snapshot.get("current_summary") or event.current_summary
        )
        event.latest_development = str(
            snapshot.get("latest_development") or ""
        )
        key_facts = snapshot.get("key_facts")
        if isinstance(key_facts, list):
            event.key_facts = key_facts
        canonical_anchors = snapshot.get("canonical_anchors")
        if isinstance(canonical_anchors, dict):
            event.canonical_anchors = canonical_anchors
        lifecycle_status = snapshot.get("lifecycle_status")
        event.lifecycle_status = (
            str(lifecycle_status) if lifecycle_status else "developing"
        )
    else:
        # Legacy revisions do not have a projection snapshot. Keep their stable
        # label, but never let an old lifecycle status survive evidence removal.
        event.lifecycle_status = "developing" if material_mentions else "stale"


def _refresh_event_times(event: Event, mentions: list[EventMention]) -> None:
    if not mentions:
        event.first_seen_at = None
        event.last_seen_at = None
        event.last_material_update_at = None
        return
    event.first_seen_at = min(_mention_time(mention) for mention in mentions)
    event.last_seen_at = max(_mention_time(mention) for mention in mentions)
    material = [mention for mention in mentions if mention.materiality == "material_update"]
    event.last_material_update_at = (
        max(_mention_time(mention) for mention in material) if material else None
    )


def _refresh_references(event: Event, mentions: list[EventMention]) -> None:
    event.origin_message_id = None
    event.latest_update_message_id = None
    event.primary_source_message_id = None
    event.best_media_message_id = None
    material = [value for value in mentions if value.materiality == "material_update"]
    if material:
        event.origin_message_id = min(
            material, key=lambda value: (_mention_time(value), value.id)
        ).normalized_item_id
        event.latest_update_message_id = max(
            material, key=lambda value: (_mention_time(value), value.id)
        ).normalized_item_id
    positive = [
        value
        for value in mentions
        if value.relation in {"reports", "supports", "confirms", "corrects"}
    ]
    if positive:
        event.primary_source_message_id = max(
            positive,
            key=lambda value: (
                _SOURCE_ROLE_RANK.get(value.source_role, 0),
                value.source_reliability_snapshot,
                -_mention_time(value).timestamp(),
                -value.id,
            ),
        ).normalized_item_id
    media = [value for value in mentions if _has_public_media(value)]
    event.best_media_message_id = (
        max(
            media,
            key=lambda value: (
                _SOURCE_ROLE_RANK.get(value.source_role, 0),
                value.materiality == "material_update",
                _mention_time(value).timestamp(),
                value.id,
            ),
        ).normalized_item_id
        if media
        else None
    )


def refresh_event_metrics(
    db: Session,
    event_ids: set[int],
    *,
    as_of: datetime | None = None,
) -> None:
    reference = as_of or datetime.now(UTC)
    for event_id in sorted(event_ids):
        event = db.scalar(select(Event).where(Event.id == event_id).with_for_update())
        if event is None:
            continue
        mentions = list(
            db.scalars(
                select(EventMention)
                .join(EventMention.normalized_item)
                .options(
                    selectinload(EventMention.normalized_item)
                    .selectinload(NormalizedItem.raw_item)
                    .selectinload(RawItem.source),
                    selectinload(EventMention.normalized_item)
                    .selectinload(NormalizedItem.raw_item)
                    .selectinload(RawItem.media_assets),
                )
                .where(
                    EventMention.event_id == event_id,
                    *current_event_mention_conditions(),
                )
            )
        )
        _restore_event_projection(db, event, mentions)
        _refresh_event_times(event, mentions)
        refresh_event_importance(event, mentions)
        credibility_evidence = [
            CredibilityEvidence(
                relation=mention.relation,
                source_role=mention.source_role,
                independence_group=mention.independence_group,
                reliability=mention.source_reliability_snapshot,
            )
            for mention in mentions
        ]
        score, level, breakdown = calculate_event_credibility(credibility_evidence)
        event.credibility_score = score
        event.credibility_level = level
        event.credibility_breakdown = breakdown
        event.credibility_policy_version = CREDIBILITY_POLICY_VERSION
        event.supporting_source_count = int(breakdown["corroboration_groups"])
        event.contradicting_source_count = int(breakdown["conflicting_groups"])
        event.independent_source_count = int(breakdown["independent_groups"])
        event.official_source_count = int(breakdown["official_groups"])
        if level == "officially_confirmed" and event.lifecycle_status in {
            "unconfirmed",
            "developing",
            "disputed",
            "stale",
        }:
            event.lifecycle_status = "confirmed"
        elif level == "denied":
            event.lifecycle_status = "denied"
        elif level == "disputed":
            event.lifecycle_status = "disputed"
        elif event.lifecycle_status in {"confirmed", "denied", "disputed"}:
            event.lifecycle_status = "developing"

        heat_evidence = [
            HeatEvidence(
                normalized_item_id=mention.normalized_item_id,
                source_id=mention.normalized_item.raw_item.source_id,
                published_at=(
                    mention.normalized_item.raw_item.published_at
                    or mention.normalized_item.raw_item.ingested_at
                ),
                content_form=mention.normalized_item.content_form,
                materiality=mention.materiality,
                content_fingerprint=mention.content_fingerprint,
            )
            for mention in mentions
        ]
        heat_score, heat_breakdown = calculate_event_heat(heat_evidence, as_of=reference)
        event.heat_score = heat_score
        event.heat_breakdown = heat_breakdown
        event.heat_policy_version = HEAT_POLICY_VERSION
        event.heat_calculated_at = reference
        event.message_count_total = int(heat_breakdown["message_count_total"])
        event.message_count_24h = int(heat_breakdown["message_count_24h"])
        event.unique_sources_24h = int(heat_breakdown["unique_sources_24h"])
        _refresh_references(event, mentions)


def refresh_stale_event_metrics(
    db: Session,
    *,
    as_of: datetime | None = None,
    ttl: timedelta = timedelta(minutes=5),
    limit: int = 100,
) -> int:
    """Refresh a bounded batch outside request handling."""
    reference = as_of or datetime.now(UTC)
    event_ids = list(
        db.scalars(
            select(Event.id)
            .where(
                (Event.heat_calculated_at.is_(None))
                | (Event.heat_calculated_at <= reference - ttl)
            )
            .order_by(Event.heat_calculated_at.asc().nullsfirst(), Event.id)
            .limit(limit)
        )
    )
    refresh_event_metrics(db, set(event_ids), as_of=reference)
    return len(event_ids)
