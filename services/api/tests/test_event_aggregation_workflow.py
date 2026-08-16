import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, attributes

import app.models  # noqa: F401
from app.core.database import Base
from app.domain.event_admission import derive_event_space, minimal_event_filter
from app.domain.esports_match_identity import esports_match_identity_conflict
from app.domain.event_types import AGGREGATION_POLICY_VERSION
from app.models.event import Event, EventAggregationRun, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.event_aggregation import EventAggregationResult
from app.services.event_candidates import recall_event_candidates
from app.services.event_metrics import refresh_event_metrics
from app.services.events import create_event
from app.repositories.events import current_event_mention_conditions
from app.services.llm import LLMAnalysisError
from app.workflows.event_aggregation import (
    EsportsMatchIdentityConflictError,
    STALE_RUNNING_RUN_AFTER,
    aggregate_normalized_item,
)


def _engine():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return engine


def _item(
    db: Session,
    *,
    source: Source,
    external_id: str,
    title: str,
    text: str | None = None,
    products: list[str] | None = None,
    topics: list[str] | None = None,
    message_type: str = "game_announcement",
    entities: list[dict[str, object]] | None = None,
    content_form: str = "original",
    publication_status: str = "published",
    importance_score: float = 0.72,
    importance_profile: str = "gameplay_announcement",
    published_at: datetime | None = None,
) -> NormalizedItem:
    content = title if text is None else text
    raw = RawItem(
        source_id=source.id,
        external_id=external_id,
        native_title=title,
        canonical_url=f"https://example.com/{external_id}",
        content_blocks=(
            [{"type": "paragraph", "text": content}] if title or content else []
        ),
        published_at=published_at or datetime(2026, 8, 12, 8, tzinfo=UTC),
    )
    db.add(raw)
    db.flush()
    item = NormalizedItem(
        raw_item_id=raw.id,
        normalized_title=title,
        normalized_text=content,
        summary=content,
        entities=entities or [],
        products=products or ["lol_pc"],
        message_type=message_type,
        topics=topics or ["balance_gameplay"],
        content_form=content_form,
        importance_score=importance_score,
        importance_calculation={
            "importance_profile": importance_profile,
            "profile_score": importance_score,
            "final_score": importance_score,
        },
        translated_title=title or None,
        translated_text=content or None,
        translated_content_blocks=(
            [{"type": "paragraph", "text": content}] if content else []
        ),
        translation_status="not_required",
        analysis_model="test",
        analysis_version="test",
        publication_status=publication_status,
    )
    db.add(item)
    db.flush()
    return item


class StaticClient:
    def __init__(self, result: EventAggregationResult) -> None:
        self.result = result
        self.calls = 0
        self.payloads: list[dict[str, object]] = []

    async def aggregate_events(self, **payload: object) -> EventAggregationResult:
        self.calls += 1
        self.payloads.append(payload)
        return self.result


class ErrorThenSuccessClient(StaticClient):
    async def aggregate_events(self, **payload: object) -> EventAggregationResult:
        self.calls += 1
        self.payloads.append(payload)
        if self.calls == 1:
            raise LLMAnalysisError("temporary invalid model response")
        return self.result


def _result(*mentions: dict[str, object]) -> EventAggregationResult:
    return EventAggregationResult.model_validate({"mentions": list(mentions)})


def _aggregate(
    db: Session, item: NormalizedItem, client: StaticClient
) -> EventAggregationRun:
    return asyncio.run(aggregate_normalized_item(db, item, llm_client=client))


def _create_decision(
    *,
    mention_index: int = 0,
    product: str | None = None,
    family: str = "gameplay_balance",
    title: str = "26.17 版本平衡调整",
    summary: str = "26.17 版本平衡调整已经公布。",
    evidence: str = "26.17 版本平衡调整",
) -> dict[str, object]:
    return {
        "mention_index": mention_index,
        "action": "create",
        "event_id": None,
        "product": product,
        "event_family": family,
        "relation": "reports",
        "source_role": "known_leaker",
        "materiality": "material_update",
        "evidence_excerpt": evidence,
        "new_event": {
            "title": title,
            "summary": summary,
            "canonical_anchors": {"patch_version": "26.17"},
            "latest_development": "首次出现",
            "key_facts": [],
        },
    }


def _esports_attach_decision(
    *,
    event_id: int,
    match_identity: dict[str, object],
    candidate_match_identity: dict[str, object],
) -> dict[str, object]:
    return {
        "mention_index": 0,
        "action": "attach",
        "event_id": event_id,
        "product": "lol_esports",
        "event_family": "esports_match",
        "relation": "reports",
        "source_role": "unknown",
        "materiality": "material_update",
        "evidence_excerpt": "BLG 对阵 TES",
        "match_identity": match_identity,
        "candidate_match_identity": candidate_match_identity,
    }


def test_minimal_filter_only_skips_unpublished_or_semantically_empty_items() -> None:
    engine = _engine()
    with Session(engine) as db:
        source = Source(name="filter")
        db.add(source)
        db.flush()
        unpublished = _item(
            db,
            source=source,
            external_id="unpublished",
            title="有语义但未发布",
            publication_status="withdrawn",
        )
        empty = _item(db, source=source, external_id="empty", title="", text="")
        repost = _item(
            db,
            source=source,
            external_id="repost",
            title="有明确事件语义的转发",
            content_form="repost",
        )
        leak = _item(db, source=source, external_id="leak", title="没有锚点的爆料")

        assert minimal_event_filter(unpublished).decision == "skip"
        assert minimal_event_filter(empty).decision == "skip"
        assert minimal_event_filter(repost).decision == "process"
        assert minimal_event_filter(leak).decision == "process"


@pytest.mark.parametrize("content_form", ["media_only", "link_only"])
def test_nonsemantic_content_forms_are_audited_without_event_model_call(
    content_form: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name=f"{content_form}-filter")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id=content_form,
            title="仅媒体消息" if content_form == "media_only" else "仅链接消息",
            content_form=content_form,
        )
        db.commit()
        client = StaticClient(_result())
        monkeypatch.setattr(
            "app.workflows.event_aggregation.recall_event_candidates",
            lambda *args, **kwargs: pytest.fail("nonsemantic content entered candidate recall"),
        )

        run = _aggregate(db, item, client)

        assert run.status == "completed"
        assert run.outcome == "skipped_by_minimal_filter"
        assert run.admission_decision == "skip"
        assert run.model_call_count == 0
        assert run.candidate_snapshot == []
        assert client.calls == 0
        assert db.scalar(select(func.count(Event.id))) == 0


def test_event_space_routes_real_products_and_topics() -> None:
    engine = _engine()
    with Session(engine) as db:
        source = Source(name="routing")
        db.add(source)
        db.flush()
        esports = _item(
            db,
            source=source,
            external_id="esports-routing",
            title="BLG 阵容变动",
            products=["lol_esports"],
            topics=["esports_rosters"],
        )
        lol_pc = _item(
            db,
            source=source,
            external_id="lol-routing",
            title="英雄平衡调整",
            products=["lol_pc"],
            topics=["balance_gameplay"],
        )

        esports_space = derive_event_space(esports)
        lol_space = derive_event_space(lol_pc)

        assert esports_space.products == ("lol_esports",)
        assert esports_space.possible_families == ("roster_change",)
        assert lol_space.possible_families == ("gameplay_balance",)


def test_apply_ignores_family_outside_routed_event_space_and_keeps_valid_mentions() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="routing-validation")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="routing-validation",
            title="阵容变化",
            products=["lol_pc"],
            topics=["balance_gameplay"],
        )
        db.commit()
        client = StaticClient(
            _result(
                _create_decision(family="gameplay_balance"),
                {
                    **_create_decision(
                        mention_index=1,
                        family="player_activity",
                        evidence="抽奖活动",
                    ),
                },
            )
        )

        run = _aggregate(db, item, client)

        assert run.outcome == "applied"
        assert db.scalar(select(func.count(Event.id))) == 1
        assert run.decision_draft["mentions"][1]["action"] == "ignore"
        assert run.decision_draft["suppressed_mentions"][0]["reason"] == (
            "outside_upstream_event_space"
        )


def test_membership_schema_checks_only_structural_action_invariants() -> None:
    create = EventAggregationResult.model_validate(
        {"mentions": [_create_decision()]}
    )
    assert create.mentions[0].new_event is not None
    assert create.mentions[0].new_event.canonical_anchors == {"patch_version": "26.17"}

    with pytest.raises(ValidationError, match="attach requires event_id"):
        _result(
            {
                "mention_index": 0,
                "action": "attach",
                "event_family": "gameplay_balance",
                "evidence_excerpt": "证据",
            }
        )
    with pytest.raises(ValidationError, match="create requires new_event"):
        _result(
            {
                "mention_index": 0,
                "action": "create",
                "event_family": "gameplay_balance",
                "evidence_excerpt": "证据",
            }
        )
    with pytest.raises(ValidationError, match="contiguous"):
        EventAggregationResult.model_validate(
            {
                "mentions": [
                    {
                        **_create_decision(),
                        "mention_index": 1,
                    }
                ]
            }
        )
    with pytest.raises(ValidationError, match="evidence_excerpt"):
        _result({**_create_decision(), "evidence_excerpt": ""})


def test_zero_mentions_completes_with_one_model_call_and_run_idempotency() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="zero")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="zero", title="只有背景讨论")
        db.commit()
        client = StaticClient(_result())

        first = _aggregate(db, item, client)
        second = _aggregate(db, item, client)

        assert first.id == second.id
        assert first.status == "completed"
        assert first.outcome == "ignored"
        assert first.model_call_count == 1
        assert client.calls == 1
        assert db.scalar(select(func.count(Event.id))) == 0
        assert db.scalar(select(func.count(EventMention.id))) == 0


def test_minimal_skip_is_audited_without_model_call() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="skip")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="skip",
            title="尚未发布",
            publication_status="withdrawn",
        )
        db.commit()
        client = StaticClient(_result())

        run = _aggregate(db, item, client)

        assert run.admission_decision == "skip"
        assert run.outcome == "skipped_by_minimal_filter"
        assert run.model_call_count == 0
        assert client.calls == 0


def test_create_persists_membership_then_refreshes_projections() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="leaker", reliability_score=0.8)
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="create",
            title="26.17 版本平衡调整",
            importance_score=0.81,
            importance_profile="leak_gameplay",
        )
        db.commit()
        client = StaticClient(_result(_create_decision()))

        run = _aggregate(db, item, client)
        event = db.scalar(select(Event))
        mention = db.scalar(
            select(EventMention).where(EventMention.normalized_item_id == item.id)
        )

        assert run.outcome == "applied"
        assert event is not None and mention is not None
        assert event.importance_score == 0.81
        assert event.importance_breakdown["dominant_normalized_item_id"] == item.id
        assert event.heat_score > 0
        assert event.message_count_total == 1
        assert mention.evidence_excerpt == "26.17 版本平衡调整"
        assert db.scalar(select(func.count(EventRevision.id))) == 1
        assert client.payloads[0].keys() == {
            "message",
            "possible_event_families",
            "candidates",
        }


def test_mixed_attach_and_create_is_atomic_and_uses_one_call() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="official", is_official=True, reliability_score=1)
        db.add(source)
        db.flush()
        seed_item = _item(
            db,
            source=source,
            external_id="seed",
            title="26.17 平衡预览",
            published_at=datetime(2026, 8, 11, 8, tzinfo=UTC),
        )
        db.commit()
        existing, _ = create_event(
            db,
            normalized_item_id=seed_item.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"patch_version": "26.17"},
            title="26.17 平衡预览",
            current_summary="预览已经发布。",
            evidence_excerpt="26.17 平衡预览",
        )
        refresh_event_metrics(db, {existing.id})
        db.commit()

        item = _item(
            db,
            source=source,
            external_id="mixed",
            title="官网确认平衡并公布新皮肤",
            topics=["balance_gameplay", "cosmetics"],
        )
        db.commit()
        client = StaticClient(
            _result(
                {
                    "mention_index": 0,
                    "action": "attach",
                    "event_id": existing.id,
                    "event_family": "gameplay_balance",
                    "relation": "confirms",
                    "source_role": "responsible_official",
                    "materiality": "material_update",
                    "evidence_excerpt": "官网确认平衡调整",
                    "projection": {
                        "summary": "官网已经确认 26.17 平衡调整。",
                        "latest_development": "官网确认",
                    },
                },
                {
                    **_create_decision(
                        mention_index=1,
                        family="cosmetic_release",
                        title="星界系列皮肤",
                        summary="官网公布星界系列皮肤。",
                        evidence="公布星界系列皮肤",
                    ),
                    "source_role": "responsible_official",
                },
            )
        )

        run = _aggregate(db, item, client)
        db.refresh(existing)

        assert run.outcome == "applied"
        assert client.calls == 1
        assert db.scalar(select(func.count(Event.id))) == 2
        assert db.scalar(select(func.count(EventMention.id))) == 3
        assert existing.current_summary == "官网已经确认 26.17 平衡调整。"
        assert existing.current_revision == 2
        attached = db.scalar(
            select(EventMention).where(EventMention.normalized_item_id == item.id)
        )
        assert attached is not None
        assert attached.source_role == "responsible_official"


def test_attach_must_reference_this_request_candidate_set() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="invalid candidate")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="invalid", title="一个新公告")
        db.commit()
        client = StaticClient(
            _result(
                {
                    "mention_index": 0,
                    "action": "attach",
                    "event_id": 999,
                    "event_family": "gameplay_balance",
                    "relation": "reports",
                    "source_role": "unknown",
                    "materiality": "material_update",
                    "evidence_excerpt": "一个新公告",
                }
            )
        )

        with pytest.raises(ValueError, match="non-candidate"):
            _aggregate(db, item, client)

        assert db.scalar(select(func.count(Event.id))) == 0
        run = db.scalar(select(EventAggregationRun))
        assert run is not None
        assert run.status == "failed"
        assert run.outcome == "apply_error"


def test_esports_match_date_conflict_blocks_forced_attach_before_membership_write() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="match occurrence conflict")
        db.add(source)
        db.flush()
        seed_item = _item(
            db,
            source=source,
            external_id="blg-tes-aug14",
            title="8 月 14 日 BLG 对阵 TES",
            products=["lol_esports"],
            topics=["esports_matches"],
            published_at=datetime(2026, 8, 14, 8, tzinfo=UTC),
        )
        db.commit()
        existing, _ = create_event(
            db,
            normalized_item_id=seed_item.id,
            mention_index=0,
            event_family="esports_match",
            products=["lol_esports"],
            canonical_anchors={
                "participants": ["BLG", "TES"],
                "match_date": "2026-08-14",
                "series_format": "BO3",
            },
            title="BLG 对阵 TES（8 月 14 日）",
            current_summary="BLG 与 TES 于 8 月 14 日进行 BO3。",
            evidence_excerpt="8 月 14 日 BLG 对阵 TES",
        )
        item = _item(
            db,
            source=source,
            external_id="blg-tes-aug16",
            title="8 月 16 日 BLG 对阵 TES",
            products=["lol_esports"],
            topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        db.commit()
        client = StaticClient(
            _result(
                _esports_attach_decision(
                    event_id=existing.id,
                    match_identity={
                        "participants": ["BLG", "TES"],
                        "match_date": "2026-08-16",
                        "series_format": "BO3",
                    },
                    candidate_match_identity={
                        "participants": ["BLG", "TES"],
                        "match_date": "2026-08-14",
                        "series_format": "BO3",
                    },
                )
            )
        )

        with pytest.raises(EsportsMatchIdentityConflictError, match="match_date"):
            _aggregate(db, item, client)

        db.refresh(existing)
        assert db.scalar(select(func.count(EventMention.id))) == 1
        assert existing.current_revision == 1
        assert existing.canonical_anchors["match_date"] == "2026-08-14"
        run = db.scalar(
            select(EventAggregationRun).where(
                EventAggregationRun.normalized_item_id == item.id
            )
        )
        assert run is not None
        assert run.status == "failed"
        assert run.outcome == "apply_error"


def test_same_esports_match_date_attaches_and_enriches_missing_identity() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="same match lifecycle")
        db.add(source)
        db.flush()
        seed_item = _item(
            db,
            source=source,
            external_id="blg-tes-preview",
            title="BLG 对阵 TES 赛前预告",
            products=["lol_esports"],
            topics=["esports_matches"],
            published_at=datetime(2026, 8, 15, 8, tzinfo=UTC),
        )
        db.commit()
        existing, _ = create_event(
            db,
            normalized_item_id=seed_item.id,
            mention_index=0,
            event_family="esports_match",
            products=["lol_esports"],
            canonical_anchors={
                "participants": ["BLG", "TES"],
                "match_date": "2026-08-16",
            },
            title="BLG 对阵 TES",
            current_summary="BLG 与 TES 将于 8 月 16 日比赛。",
            evidence_excerpt="BLG 对阵 TES 赛前预告",
        )
        item = _item(
            db,
            source=source,
            external_id="blg-tes-result",
            title="BLG 对阵 TES 赛果",
            products=["lol_esports"],
            topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 14, tzinfo=UTC),
        )
        db.commit()
        client = StaticClient(
            _result(
                _esports_attach_decision(
                    event_id=existing.id,
                    match_identity={
                        "participants": ["BLG", "TES"],
                        "competition": "LPL",
                        "match_date": "2026-08-16",
                        "series_format": "BO3",
                    },
                    candidate_match_identity={
                        "participants": ["BLG", "TES"],
                        "match_date": "2026-08-16",
                    },
                )
            )
        )

        run = _aggregate(db, item, client)

        db.refresh(existing)
        assert run.outcome == "applied"
        assert db.scalar(select(func.count(EventMention.id))) == 2
        assert existing.canonical_anchors == {
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
            "competition": "LPL",
            "series_format": "BO3",
        }


def test_created_esports_match_persists_explicit_occurrence_identity() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="new match identity")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="new-blg-tes-match",
            title="8 月 16 日 BLG 对阵 TES",
            products=["lol_esports"],
            topics=["esports_matches"],
        )
        db.commit()
        decision = _create_decision(
            product="lol_esports",
            family="esports_match",
            title="BLG 对阵 TES（8 月 16 日）",
            summary="BLG 与 TES 于 8 月 16 日进行 BO3。",
            evidence="8 月 16 日 BLG 对阵 TES",
        )
        decision["match_identity"] = {
            "participants": ["BLG", "TES"],
            "competition": "LPL",
            "match_date": "2026-08-16",
            "series_format": "BO3",
            "external_match_id": "lpl-2026-0816-blg-tes",
        }
        client = StaticClient(_result(decision))

        run = _aggregate(db, item, client)

        event = db.scalar(select(Event))
        mention = db.scalar(select(EventMention))
        assert run.outcome == "applied"
        assert event is not None
        assert mention is not None
        assert run.aggregation_policy_version == AGGREGATION_POLICY_VERSION
        assert event.aggregation_policy_version == AGGREGATION_POLICY_VERSION
        assert mention.aggregation_policy_version == AGGREGATION_POLICY_VERSION
        assert event.canonical_anchors["patch_version"] == "26.17"
        assert event.canonical_anchors["participants"] == ["BLG", "TES"]
        assert event.canonical_anchors["match_date"] == "2026-08-16"
        assert event.canonical_anchors["external_match_id"] == "lpl-2026-0816-blg-tes"


def test_missing_incoming_match_date_does_not_block_semantic_attach() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="missing match date")
        db.add(source)
        db.flush()
        seed_item = _item(
            db,
            source=source,
            external_id="dated-match",
            title="BLG 对阵 TES",
            products=["lol_esports"],
            topics=["esports_matches"],
        )
        db.commit()
        existing, _ = create_event(
            db,
            normalized_item_id=seed_item.id,
            mention_index=0,
            event_family="esports_match",
            products=["lol_esports"],
            canonical_anchors={
                "participants": ["BLG", "TES"],
                "match_date": "2026-08-16",
            },
            title="BLG 对阵 TES",
            current_summary="BLG 与 TES 将于 8 月 16 日比赛。",
            evidence_excerpt="BLG 对阵 TES",
        )
        item = _item(
            db,
            source=source,
            external_id="undated-result",
            title="BLG 对阵 TES 赛果",
            products=["lol_esports"],
            topics=["esports_matches"],
        )
        db.commit()
        client = StaticClient(
            _result(
                _esports_attach_decision(
                    event_id=existing.id,
                    match_identity={"participants": ["BLG", "TES"]},
                    candidate_match_identity={
                        "participants": ["BLG", "TES"],
                        "match_date": "2026-08-16",
                    },
                )
            )
        )

        run = _aggregate(db, item, client)

        assert run.outcome == "applied"
        assert db.scalar(select(func.count(EventMention.id))) == 2


@pytest.mark.parametrize(
    ("existing", "incoming", "expected_field"),
    [
        (
            {"external_match_id": "lpl-100"},
            {"external_match_id": "lpl-101"},
            "external_match_id",
        ),
        ({"stage": "Group Stage"}, {"stage": "Playoffs"}, "stage"),
        ({"round": "Upper Round 1"}, {"round": "Lower Round 2"}, "round"),
        (
            {"scheduled_at": "2026-08-14T23:00:00+08:00"},
            {"scheduled_at": "2026-08-16T19:00:00+08:00"},
            "match_date",
        ),
    ],
)
def test_esports_match_explicit_identity_conflicts_are_deterministic(
    existing: dict[str, object], incoming: dict[str, object], expected_field: str
) -> None:
    assert expected_field in str(esports_match_identity_conflict(existing, incoming))


def test_esports_match_missing_identity_field_is_not_a_hard_conflict() -> None:
    assert (
        esports_match_identity_conflict(
            {"match_date": "2026-08-16", "round": "Upper Round 1"},
            {"participants": ["BLG", "TES"]},
        )
        is None
    )


def test_multi_mention_failure_rolls_back_all_membership_writes() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="atomic")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="atomic", title="综合公告")
        db.commit()
        client = StaticClient(
            _result(
                _create_decision(mention_index=0),
                {
                    "mention_index": 1,
                    "action": "attach",
                    "event_id": 999,
                        "event_family": "gameplay_balance",
                    "relation": "reports",
                    "source_role": "unknown",
                    "materiality": "material_update",
                    "evidence_excerpt": "活动更新",
                },
            )
        )

        with pytest.raises(ValueError, match="non-candidate"):
            _aggregate(db, item, client)

        assert db.scalar(select(func.count(Event.id))) == 0
        assert db.scalar(select(func.count(EventMention.id))) == 0
        assert db.scalar(select(func.count(EventRevision.id))) == 0


def test_official_repost_cannot_claim_responsible_official_role() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="official repost", is_official=True)
        db.add(source)
        db.flush()
        seed_item = _item(
            db,
            source=source,
            external_id="seed-repost",
            title="原始事件",
        )
        db.commit()
        existing, _ = create_event(
            db,
            normalized_item_id=seed_item.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={},
            title="原始事件",
            current_summary="原始事件已发布。",
            evidence_excerpt="原始事件",
        )
        item = _item(
            db,
            source=source,
            external_id="repost",
            title="转发一个事件",
            content_form="repost",
        )
        db.commit()
        decision = _create_decision()
        decision["action"] = "attach"
        decision["event_id"] = existing.id
        decision["new_event"] = None
        decision["source_role"] = "responsible_official"
        client = StaticClient(_result(decision))

        _aggregate(db, item, client)
        mention = db.scalar(
            select(EventMention).where(EventMention.normalized_item_id == item.id)
        )

        assert mention is not None
        assert mention.source_role == "republisher"


def test_manual_retry_accumulates_model_call_audit() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="retry")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="retry", title="可重试公告")
        db.commit()
        client = ErrorThenSuccessClient(_result(_create_decision()))

        with pytest.raises(LLMAnalysisError):
            _aggregate(db, item, client)
        failed = db.scalar(select(EventAggregationRun))
        assert failed is not None
        previous_count = failed.model_call_count
        assert failed.status == "failed"

        completed = _aggregate(db, item, client)

        assert completed.status == "completed"
        assert completed.model_call_count == previous_count + 1
        assert client.calls == 2
        assert db.scalar(select(func.count(Event.id))) == 1


def test_running_event_aggregation_run_cannot_be_reused_for_another_model_call() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="running")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="running", title="正在聚合的消息")
        existing_run = EventAggregationRun(
            normalized_item_id=item.id,
            normalized_item_revision=item.current_revision,
            status="running",
            current_stage="model_decision",
            idempotency_key=(
                f"{item.id}:{item.current_revision}:"
                "event-aggregation-v7-match-occurrence-boundary"
            ),
        )
        db.add(existing_run)
        db.commit()
        client = StaticClient(_result(_create_decision()))

        returned = _aggregate(db, item, client)

        assert client.calls == 0
        assert returned.id == existing_run.id
        run = db.scalar(select(EventAggregationRun))
        assert run is not None
        assert run.status == "running"
        assert db.scalar(select(func.count(Event.id))) == 0


def test_stale_running_run_reuses_persisted_decision_without_duplicate_membership() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="stale recovery")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="stale", title="可恢复的公告")
        db.commit()
        existing, created = create_event(
            db,
            normalized_item_id=item.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"patch_version": "26.17"},
            title="可恢复的公告",
            current_summary="已经存在的事件。",
        )
        assert created is True
        db.commit()
        run = EventAggregationRun(
            normalized_item_id=item.id,
            normalized_item_revision=item.current_revision,
            status="running",
            current_stage="apply_membership",
            aggregation_policy_version="event-aggregation-v7-match-occurrence-boundary",
            idempotency_key=(
                f"{item.id}:{item.current_revision}:"
                "event-aggregation-v7-match-occurrence-boundary"
            ),
            model_call_count=1,
            candidate_snapshot=[],
            decision_draft={"mentions": [_create_decision()]},
            updated_at=datetime.now(UTC) - STALE_RUNNING_RUN_AFTER - timedelta(minutes=1),
        )
        db.add(run)
        db.commit()
        client = StaticClient(_result(_create_decision()))

        recovered = _aggregate(db, item, client)

        assert recovered.status == "completed"
        assert recovered.outcome == "applied"
        assert recovered.model_call_count == 1
        assert client.calls == 0
        assert recovered.decision_draft["recovery"]["type"] == (
            "stale_running_run_reclaimed"
        )
        assert db.scalar(select(func.count(Event.id))) == 1
        assert db.scalar(select(func.count(EventMention.id))) == 1


def test_stale_previous_revision_run_does_not_block_current_revision() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="revision fencing")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="revision-fencing", title="旧版本公告")
        old_run = EventAggregationRun(
            normalized_item_id=item.id,
            normalized_item_revision=1,
            status="running",
            current_stage="model_decision",
            idempotency_key=(
                f"{item.id}:1:event-aggregation-v7-match-occurrence-boundary"
            ),
        )
        db.add(old_run)
        db.commit()

        with Session(engine, expire_on_commit=False) as correction_db:
            current = correction_db.get(NormalizedItem, item.id)
            assert current is not None
            current.current_revision = 2
            correction_db.commit()

        with Session(engine, expire_on_commit=False) as current_db:
            current = current_db.get(NormalizedItem, item.id)
            assert current is not None
            client = StaticClient(_result(_create_decision()))
            current_run = _aggregate(current_db, current, client)

            assert current_run.status == "completed"
            assert current_run.normalized_item_revision == 2
            assert client.calls == 1

        stale = _aggregate(db, item, StaticClient(_result(_create_decision())))
        assert stale.status == "completed"
        assert stale.outcome == "ignored"
        assert stale.normalized_item_revision == 1
        assert db.scalar(select(func.count(EventMention.id))) == 1


def test_superseded_worker_cannot_apply_old_revision_membership() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="superseded worker")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="superseded-worker", title="初始事件")
        db.commit()
        first = _aggregate(db, item, StaticClient(_result(_create_decision())))
        event = db.scalar(select(Event))
        assert event is not None
        assert first.normalized_item_revision == 1
        first.status = "running"
        first.current_stage = "model_decision"
        first.outcome = None
        first.completed_at = None
        db.commit()

        with Session(engine, expire_on_commit=False) as correction_db:
            current = correction_db.get(NormalizedItem, item.id)
            assert current is not None
            current.current_revision = 2
            current.normalized_title = "修正后的事件"
            correction_db.commit()

        db.rollback()
        attributes.set_committed_value(item, "current_revision", 1)
        stale = _aggregate(db, item, StaticClient(_result(_create_decision())))

        assert stale.status == "completed"
        assert stale.outcome == "ignored"
        assert db.scalar(select(func.count(EventMention.id))) == 1
        assert db.scalar(select(EventMention).where(EventMention.normalized_item_revision == 2)) is None


def test_current_event_projection_uses_only_revision_two_evidence() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="current revision evidence")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="current-evidence", title="初始事件")
        db.commit()
        _aggregate(db, item, StaticClient(_result(_create_decision())))
        event_a = db.scalar(select(Event))
        assert event_a is not None

        with Session(engine, expire_on_commit=False) as correction_db:
            current = correction_db.get(NormalizedItem, item.id)
            assert current is not None
            current.current_revision = 2
            current.normalized_title = "修正为同一事件"
            correction_db.commit()

        db.refresh(item)
        current = db.get(NormalizedItem, item.id)
        assert current is not None
        decision = {
            "mention_index": 0,
            "action": "attach",
            "event_id": event_a.id,
            "product": "lol_pc",
            "event_family": "gameplay_balance",
            "relation": "confirms",
            "source_role": "known_leaker",
            "materiality": "corroboration_only",
            "evidence_excerpt": "修正后的同一事件证据",
        }
        second = _aggregate(db, current, StaticClient(_result(decision)))

        assert second.outcome == "applied"
        mentions = list(
            db.scalars(
                select(EventMention)
                .where(EventMention.normalized_item_id == item.id)
                .order_by(EventMention.normalized_item_revision)
            )
        )
        assert [mention.normalized_item_revision for mention in mentions] == [1, 2]
        assert db.scalar(
            select(func.count(EventMention.id)).where(
                EventMention.normalized_item_id == item.id,
                EventMention.normalized_item_revision == 2,
            )
        ) == 1
        current_event_ids = set(
            db.scalars(
                select(EventMention.event_id)
                .join(EventMention.normalized_item)
                .where(
                    EventMention.normalized_item_id == item.id,
                    *current_event_mention_conditions(),
                )
            )
        )
        assert current_event_ids == {event_a.id}


def test_revision_two_can_attach_event_b_without_old_event_a_evidence() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="revision two event switch")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="event-switch", title="初始事件")
        db.commit()
        _aggregate(db, item, StaticClient(_result(_create_decision())))
        event_a = db.scalar(select(Event))
        assert event_a is not None
        other_item = _item(db, source=source, external_id="event-b-seed", title="独立事件")
        db.commit()
        event_b, _ = create_event(
            db,
            normalized_item_id=other_item.id,
            mention_index=0,
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={"name": "事件 B"},
            title="事件 B",
            current_summary="事件 B 摘要",
            evidence_excerpt="事件 B",
        )
        db.commit()

        with Session(engine, expire_on_commit=False) as correction_db:
            current = correction_db.get(NormalizedItem, item.id)
            assert current is not None
            current.current_revision = 2
            current.normalized_title = "改为事件 B"
            correction_db.commit()
        db.refresh(item)
        decision = {
            "mention_index": 0,
            "action": "attach",
            "event_id": event_b.id,
            "product": "lol_pc",
            "event_family": "gameplay_balance",
            "relation": "confirms",
            "source_role": "known_leaker",
            "materiality": "material_update",
            "evidence_excerpt": "改为事件 B 的证据",
            "projection": {
                "summary": "事件 B 的修正摘要",
                "latest_development": "改为事件 B",
            },
        }

        run = _aggregate(db, item, StaticClient(_result(decision)))

        assert run.outcome == "applied"
        current_event_ids = set(
            db.scalars(
                select(EventMention.event_id)
                .join(EventMention.normalized_item)
                .where(
                    EventMention.normalized_item_id == item.id,
                    *current_event_mention_conditions(),
                )
            )
        )
        assert current_event_ids == {event_b.id}
        assert db.scalar(
            select(EventMention).where(
                EventMention.event_id == event_a.id,
                EventMention.normalized_item_id == item.id,
                EventMention.normalized_item_revision == 1,
            )
        ) is not None


def test_revision_two_ignore_removes_old_event_from_current_projection() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="revision two ignore")
        db.add(source)
        db.flush()
        item = _item(db, source=source, external_id="ignore-revision", title="初始事件")
        db.commit()
        _aggregate(db, item, StaticClient(_result(_create_decision())))
        event = db.scalar(select(Event))
        assert event is not None

        with Session(engine, expire_on_commit=False) as correction_db:
            current = correction_db.get(NormalizedItem, item.id)
            assert current is not None
            current.current_revision = 2
            current.normalized_title = "修正为不进入事件"
            correction_db.commit()
        db.refresh(item)

        run = _aggregate(db, item, StaticClient(_result()))

        assert run.outcome == "ignored"
        assert db.scalar(
            select(EventMention).where(
                EventMention.normalized_item_id == item.id,
                EventMention.normalized_item_revision == 2,
            )
        ) is None
        assert db.scalar(
            select(EventMention.event_id)
            .join(EventMention.normalized_item)
            .where(
                EventMention.normalized_item_id == item.id,
                *current_event_mention_conditions(),
            )
        ) is None


def test_cross_product_mentions_isolate_event_products() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="cross-product")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="cross-product",
            title="测试服同时出现 PC 皮肤和云顶物料",
            products=["lol_pc", "tft"],
            topics=["cosmetics", "tft_gameplay"],
        )
        db.commit()
        client = StaticClient(
            _result(
                {
                    **_create_decision(
                        product="lol_pc",
                        title="PC 新皮肤",
                        summary="PC 新增皮肤。",
                        evidence="PC 新皮肤",
                    ),
                },
                {
                    **_create_decision(
                        mention_index=1,
                        product="tft",
                        title="云顶新物料",
                        summary="云顶新增物料。",
                        evidence="云顶新物料",
                    ),
                },
            )
        )

        run = _aggregate(db, item, client)

        assert run.outcome == "applied"
        events = db.scalars(select(Event).order_by(Event.id)).all()
        assert [event.products for event in events] == [["lol_pc"], ["tft"]]


def test_cross_product_mention_requires_explicit_product() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="cross-product-required")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="cross-product-required",
            title="测试服同时出现 PC 皮肤和云顶物料",
            products=["lol_pc", "tft"],
            topics=["cosmetics", "tft_gameplay"],
        )
        db.commit()
        client = StaticClient(_result(_create_decision()))

        with pytest.raises(ValueError, match="must specify product"):
            _aggregate(db, item, client)

        assert db.scalar(select(func.count(Event.id))) == 0


def test_repost_cannot_create_event() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="repost")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="repost-create",
            title="转发一条皮肤爆料",
            content_form="repost",
        )
        db.commit()
        client = StaticClient(_result(_create_decision()))

        with pytest.raises(ValueError, match="repost messages cannot create"):
            _aggregate(db, item, client)

        assert db.scalar(select(func.count(Event.id))) == 0


def test_membership_application_preserves_model_event_boundaries() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="leak batch")
        db.add(source)
        db.flush()
        item = _item(
            db,
            source=source,
            external_id="leak-batch",
            title="测试服皮肤物料",
            message_type="game_leak",
            products=["lol_pc"],
            topics=["cosmetics"],
        )
        db.commit()
        client = StaticClient(
            _result(
                    _create_decision(
                        family="cosmetic_release",
                        product="lol_pc",
                    title="花仙子璐璐炫彩",
                    summary="花仙子璐璐炫彩曝光。",
                    evidence="花仙子璐璐炫彩",
                ),
                {
                    **_create_decision(
                        mention_index=1,
                        family="cosmetic_release",
                        product="lol_pc",
                        title="花仙子格温炫彩",
                        summary="花仙子格温炫彩曝光。",
                        evidence="花仙子格温炫彩",
                    ),
                },
            )
        )
        client.result.mentions[0].new_event.key_facts = [
            {"type": "cosmetic", "target": "花仙子璐璐炫彩"}
        ]
        client.result.mentions[1].new_event.key_facts = [
            {"type": "cosmetic", "target": "花仙子格温炫彩"}
        ]

        run = _aggregate(db, item, client)
        assert run.outcome == "applied"
        assert db.scalar(select(func.count(Event.id))) == 2
        assert db.scalar(select(func.count(EventMention.id))) == 2


def test_candidate_recall_is_generic_high_recall_and_bounded() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="recall")
        db.add(source)
        db.flush()
        now = datetime(2026, 8, 12, 8, tzinfo=UTC)
        for index, (family, products) in enumerate(
                [
                    ("gameplay_balance", ["lol_pc"]),
                    ("gameplay_balance", ["lol_pc"]),
                    ("gameplay_balance", ["lol_pc"]),
                    ("gameplay_balance", ["lol_pc"]),
                    ("commercial_offer", ["tft"]),
                    ("esports_match", ["lol_esports"]),
            ]
        ):
            db.add(
                Event(
                    title=f"候选事件 {index}",
                    current_summary=f"共同实体 星界 {index}",
                    event_family=family,
                    products=products,
                    canonical_anchors={"entity": "星界"},
                    first_seen_at=now - timedelta(days=index),
                    last_seen_at=now - timedelta(days=index),
                )
            )
        item = _item(
            db,
            source=source,
            external_id="recall-message",
            title="星界相关更新",
            entities=[{"type": "activity", "name": "星界"}],
            products=["lol_pc"],
            topics=["balance_gameplay"],
            published_at=now,
        )
        db.commit()

        candidates = recall_event_candidates(
            db,
            item=item,
            possible_families=["gameplay_balance"],
            entity_hints={"activity_name": "星界"},
            total_limit=4,
        )

        assert len(candidates) == 4
        assert candidates[0]["event_family"] == "gameplay_balance"
        assert all(candidate["products"] == ["lol_pc"] for candidate in candidates)
        assert all(candidate["event_family"] == "gameplay_balance" for candidate in candidates)
        assert all("identity_key" not in candidate for candidate in candidates)
        assert all("recall_reasons" in candidate for candidate in candidates)


def test_candidate_recall_uses_translated_semantic_projection() -> None:
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="translated-recall")
        db.add(source)
        db.flush()
        now = datetime(2026, 8, 12, 8, tzinfo=UTC)
        event = Event(
            title="26.17 版本平衡调整",
            current_summary="官方公布了 26.17 版本的平衡调整。",
            event_family="gameplay_balance",
            products=["lol_pc"],
            canonical_anchors={},
            first_seen_at=now,
            last_seen_at=now,
        )
        db.add(event)
        item = _item(
            db,
            source=source,
            external_id="translated-recall",
            title="26.17 balance changes",
            text="The 26.17 balance changes are now available.",
            published_at=now,
        )
        item.translated_title = "26.17 版本平衡调整"
        item.translated_text = "26.17 版本平衡调整已经公布。"
        item.summary = ""
        db.commit()

        candidates = recall_event_candidates(
            db,
            item=item,
            possible_families=["gameplay_balance"],
        )

        assert [candidate["event_id"] for candidate in candidates] == [event.id]
        assert "text_overlap" in candidates[0]["recall_reasons"]
