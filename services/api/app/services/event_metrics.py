from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.event_credibility import CredibilityEvidence, calculate_event_credibility
from app.domain.event_heat import HeatEvidence, calculate_event_heat
from app.domain.event_importance import EventImportanceEvidence, calculate_event_importance
from app.domain.event_types import (
    CREDIBILITY_POLICY_VERSION,
    HEAT_POLICY_VERSION,
    IMPORTANCE_POLICY_VERSION,
)
from app.models.event import Event, EventMention
from app.models.normalized_item import NormalizedItem


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
        if not isinstance(calculation, dict):
            calculation = {}
        evidence.append(
            EventImportanceEvidence(
                normalized_item_id=mention.normalized_item_id,
                profile=calculation.get("importance_profile"),
                domain_score=calculation.get("profile_score"),
                materiality=mention.materiality,
            )
        )
    event.importance_score, event.importance_breakdown = calculate_event_importance(evidence)
    event.importance_policy_version = IMPORTANCE_POLICY_VERSION


def _refresh_references(event: Event, mentions: list[EventMention]) -> None:
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
                .where(
                    EventMention.event_id == event_id,
                    NormalizedItem.publication_status == "published",
                )
            )
        )
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
