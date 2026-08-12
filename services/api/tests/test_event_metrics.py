from datetime import UTC, datetime, timedelta

from app.domain.event_credibility import CredibilityEvidence, calculate_event_credibility
from app.domain.event_heat import HeatEvidence, calculate_event_heat
from app.domain.event_importance import (
    EventImportanceEvidence,
    calculate_event_importance,
    importance_level,
)


def _importance(
    normalized_item_id: int,
    score: object,
    *,
    profile: str | None = "gameplay_announcement",
    materiality: str = "material_update",
) -> EventImportanceEvidence:
    return EventImportanceEvidence(
        normalized_item_id=normalized_item_id,
        profile=profile,
        domain_score=score,
        materiality=materiality,
    )


def test_event_importance_uses_strongest_valid_material_domain_evidence() -> None:
    score, breakdown = calculate_event_importance(
        [
            _importance(1, 0.6, profile="activity_announcement"),
            _importance(2, 0.72, profile="gameplay_announcement"),
            _importance(3, 0.84, profile="activity_free_skin"),
            _importance(4, 0.99, materiality="duplicate"),
        ]
    )

    assert score == 0.84
    assert importance_level(score) == "critical"
    assert breakdown["policy_version"] == "event-importance-v2-domain-evidence"
    assert breakdown["method"] == "max_material_domain_score"
    assert breakdown["dominant_profile"] == "activity_free_skin"
    assert breakdown["dominant_normalized_item_id"] == 3
    assert breakdown["contribution_count"] == 3
    assert breakdown["ignored_evidence_reasons"] == {"non_material": 1}


def test_event_importance_fallback_is_zero_and_audits_invalid_evidence() -> None:
    score, breakdown = calculate_event_importance(
        [
            _importance(1, None),
            _importance(2, 1.2),
            _importance(3, 0.8, profile=None),
            _importance(4, 0.9, materiality="context_only"),
        ]
    )

    assert score == 0.0
    assert breakdown["dominant_profile"] is None
    assert breakdown["contribution_count"] == 0
    assert breakdown["ignored_evidence_count"] == 4


def test_credibility_deduplicates_republishers_and_rewards_independent_support() -> None:
    original = CredibilityEvidence(
        relation="reports",
        source_role="known_leaker",
        independence_group="upstream:leak-a",
        reliability=0.8,
    )
    republishers = [
        CredibilityEvidence(
            relation="supports",
            source_role="republisher",
            independence_group="upstream:leak-a",
            reliability=0.9,
        )
        for _ in range(20)
    ]
    base_score, base_level, base = calculate_event_credibility([original])
    repost_score, repost_level, repost = calculate_event_credibility(
        [original, *republishers]
    )
    independent = CredibilityEvidence(
        relation="supports",
        source_role="independent_media",
        independence_group="source:media-b:claim",
        reliability=0.8,
    )
    supported_score, supported_level, supported = calculate_event_credibility(
        [original, *republishers, independent]
    )

    assert base_score == repost_score == 0.69
    assert base_level == repost_level == "plausible"
    assert base["independent_groups"] == repost["independent_groups"] == 1
    assert supported_score > repost_score
    assert supported_level == "corroborated"
    assert supported["independent_groups"] == 2


def test_official_confirmation_denial_and_conflict_override_numeric_path() -> None:
    confirmation = CredibilityEvidence(
        relation="confirms",
        source_role="responsible_official",
        independence_group="source:official:confirm",
        reliability=1,
    )
    denial = CredibilityEvidence(
        relation="denies",
        source_role="responsible_official",
        independence_group="source:official:deny",
        reliability=1,
    )

    assert calculate_event_credibility([confirmation])[:2] == (
        1.0,
        "officially_confirmed",
    )
    assert calculate_event_credibility([denial])[:2] == (0.0, "denied")
    assert calculate_event_credibility([confirmation, denial])[:2] == (
        0.5,
        "disputed",
    )


def _heat(
    *,
    message_id: int,
    source_id: int,
    published_at: datetime,
    content_form: str = "original",
    materiality: str = "material_update",
    fingerprint: str | None = None,
) -> HeatEvidence:
    return HeatEvidence(
        normalized_item_id=message_id,
        source_id=source_id,
        published_at=published_at,
        content_form=content_form,
        materiality=materiality,
        content_fingerprint=fingerprint,
    )


def test_reposts_raise_heat_while_time_decay_lowers_it_naturally() -> None:
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    original = _heat(message_id=1, source_id=1, published_at=now, fingerprint="original")
    reposts = [
        _heat(
            message_id=index + 2,
            source_id=index + 2,
            published_at=now,
            content_form="repost",
            materiality="duplicate",
            fingerprint="same-upstream",
        )
        for index in range(20)
    ]

    original_score, _ = calculate_event_heat([original], as_of=now)
    spread_score, spread = calculate_event_heat([original, *reposts], as_of=now)
    decayed_score, _ = calculate_event_heat(
        [original, *reposts], as_of=now + timedelta(hours=24)
    )

    assert spread_score > original_score
    assert decayed_score < spread_score
    assert spread["message_count_total"] == 21
    assert spread["unique_sources_24h"] == 21


def test_heat_deduplicates_same_source_and_caps_short_term_repetition() -> None:
    now = datetime(2026, 8, 11, 12, tzinfo=UTC)
    duplicated = [
        _heat(
            message_id=index,
            source_id=1,
            published_at=now,
            fingerprint="same-content",
        )
        for index in (1, 2)
    ]
    _, duplicate_breakdown = calculate_event_heat(duplicated, as_of=now)
    same_source = [
        _heat(
            message_id=index,
            source_id=1,
            published_at=now,
            fingerprint=f"content-{index}",
        )
        for index in range(1, 5)
    ]
    different_sources = [
        _heat(
            message_id=index,
            source_id=index,
            published_at=now,
            fingerprint=f"content-{index}",
        )
        for index in range(1, 5)
    ]
    capped_score, capped = calculate_event_heat(same_source, as_of=now)
    distributed_score, _ = calculate_event_heat(different_sources, as_of=now)

    assert duplicate_breakdown["message_count_total"] == 1
    assert duplicate_breakdown["deduplicated_count"] == 1
    assert [row["source_repeat_factor"] for row in capped["contributions"]] == [
        1.0,
        0.5,
        0.25,
        0.1,
    ]
    assert capped_score < distributed_score
