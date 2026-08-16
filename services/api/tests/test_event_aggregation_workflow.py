import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, attributes

import app.models  # noqa: F401
from app.core.database import Base
from app.domain.event_admission import derive_event_space, minimal_event_filter
from app.domain.esports_match_identity import (
    esports_match_has_subject,
    esports_match_identity_conflict,
    match_identity_from_anchors,
    match_identity_from_message_entities,
)
from app.domain.event_types import AGGREGATION_POLICY_VERSION
from app.models.event import Event, EventAggregationRun, EventMention, EventRevision
from app.models.normalized_item import NormalizedItem
from app.models.raw_item import RawItem
from app.models.source import Source
from app.schemas.event_aggregation import EventAggregationResult, EventMentionDecision
from app.services.event_candidates import esports_match_identity_gate, recall_event_candidates
from app.services.event_metrics import refresh_event_metrics
from app.services.events import create_event
from app.repositories.events import current_event_mention_conditions
from app.services.llm import (
    LLMAnalysisError,
    LLMClient,
    esports_match_create_continuation_error,
    execution_metadata,
)
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
    latest_development: str | None = None,
) -> dict[str, object]:
    decision = {
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
    }
    # Only a material_update with latest_development satisfies the unified material
    # projection contract; other call sites depend on this helper staying bare to
    # exercise the schema rejection path.
    if latest_development is not None:
        decision["projection"] = {"latest_development": latest_development}
    return decision


def _esports_create_decision(
    *,
    title: str,
    summary: str = "BLG 与 TES 的比赛。",
    latest_development: str | None = None,
    match_identity: dict[str, object],
    evidence: str = "BLG 对阵 TES",
) -> dict[str, object]:
    return {
        "mention_index": 0,
        "action": "create",
        "event_id": None,
        "product": "lol_esports",
        "event_family": "esports_match",
        "relation": "reports",
        "source_role": "unknown",
        "materiality": "material_update",
        "evidence_excerpt": evidence,
        "match_identity": match_identity,
        "new_event": {
            "title": title,
            "summary": summary,
            "canonical_anchors": dict(match_identity),
            "latest_development": latest_development or "首次出现",
            "key_facts": [],
        },
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
                    "projection": {"latest_development": "一个新公告"},
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
                    latest_development="8 月 16 日赛果更新",
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
                    latest_development="BLG 胜出",
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
                    latest_development="获得赛果",
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
            "scheduled_at",
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
                    "projection": {"latest_development": "活动更新"},
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
        decision["projection"] = {"latest_development": "转发事件"}
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
            idempotency_key=f"{item.id}:{item.current_revision}:{AGGREGATION_POLICY_VERSION}",
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
            aggregation_policy_version=AGGREGATION_POLICY_VERSION,
            idempotency_key=f"{item.id}:{item.current_revision}:{AGGREGATION_POLICY_VERSION}",
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
                f"{item.id}:1:{AGGREGATION_POLICY_VERSION}"
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


def _continuation_mention(
    *,
    match_identity: dict[str, object],
    latest_development: str = "后未提供",
) -> EventMentionDecision:
    return _result(
        _esports_create_decision(
            title="BLG 对阵 TES",
            latest_development=latest_development,
            match_identity=match_identity,
        )
    ).mentions[0]


def test_esports_match_create_rejected_when_score_progresses_existing_match(
) -> None:
    """Case A: score progression must not create; validator feeds the model a reason."""
    mention = _continuation_mention(
        latest_development="2:1 最终赛果",
        match_identity={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
        },
    )
    candidates = {
        123: {
            "event_id": 123,
            "event_family": "esports_match",
            "products": ["lol_esports"],
            "canonical_anchors": {
                "participants": ["BLG", "TES"],
                "match_date": "2026-08-16",
            },
        }
    }
    message = {"title": "BLG 2:1 TES", "summary": "BLG 获胜", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    assert error is not None
    assert "123" in error
    assert "不能 create" in error
    assert "material_update" in error


def test_esports_match_create_allowed_for_distinct_new_occurrence() -> None:
    """Case C / F-resolution: a genuinely different match date is not a continuation."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
        }
    )
    candidates = {
        123: {
            "event_id": 123,
            "event_family": "esports_match",
            "products": ["lol_esports"],
            "canonical_anchors": {
                "participants": ["BLG", "TES"],
                "match_date": "2026-08-14",
            },
        }
    }
    message = {"title": "8 月 14 日 BLG 对阵 TES", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    assert error is None


def test_esports_match_create_not_forced_without_state_update_evidence() -> None:
    """Case E: identity ambiguity keeps the model's semantic choice."""
    mention = _continuation_mention(
        match_identity={"participants": ["BLG", "TES"]}
    )
    candidates = {
        123: {
            "event_id": 123,
            "event_family": "esports_match",
            "products": ["lol_esports"],
            "canonical_anchors": {"participants": ["BLG", "TES"]},
        }
    }
    # No score, no result wording: cannot conclude the message continues the match.
    message = {"title": "BLG 对阵 TES 前瞻", "summary": "两队都期待本次对决",
               "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    assert error is None


def test_esports_match_create_not_rejected_for_different_participants() -> None:
    mention = _continuation_mention(
        match_identity={"participants": ["JDG", "WBG"]}
    )
    candidates = {
        123: {
            "event_id": 123,
            "event_family": "esports_match",
            "products": ["lol_esports"],
            "canonical_anchors": {"participants": ["BLG", "TES"]},
        }
    }
    message = {"title": "JDG 1:0 WBG", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    assert error is None


def test_esports_match_lifecycle_score_updates_remain_one_event() -> None:
    """Case A + B: 1:0 -> 1:1 -> 2:1 accumulation produces a single Event."""
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="match lifecycle")
        db.add(source)
        db.flush()
        msg1 = _item(
            db, source=source, external_id="msg1", title="BLG 1:0 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 10, tzinfo=UTC),
        )
        db.commit()
        run1 = _aggregate(
            db, msg1,
            StaticClient(
                _result(
                    _esports_create_decision(
                        title="BLG 对阵 TES", latest_development="1:0 领先",
                        match_identity={
                            "participants": ["BLG", "TES"], "match_date": "2026-08-16",
                        },
                        evidence="BLG 1:0 TES",
                    )
                )
            ),
        )
        assert run1.outcome == "applied"
        event_a = db.scalar(select(Event).where(Event.event_family == "esports_match"))
        assert event_a is not None

        def _attach_projection(latest: str) -> dict[str, object]:
            return {
                "mention_index": 0, "action": "attach", "event_id": event_a.id,
                "product": "lol_esports", "event_family": "esports_match",
                "relation": "reports", "source_role": "unknown",
                "materiality": "material_update",
                "evidence_excerpt": f"BLG {latest} TES",
                "match_identity": {
                    "participants": ["BLG", "TES"], "match_date": "2026-08-16",
                },
                "projection": {"latest_development": latest},
            }

        msg2 = _item(
            db, source=source, external_id="msg2", title="BLG 1:1 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 11, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, msg2, StaticClient(_result(_attach_projection("1:1"))))

        msg3 = _item(
            db, source=source, external_id="msg3", title="BLG 2:1 TES 比赛结束",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, msg3, StaticClient(_result(_attach_projection("2:1 最终赛果"))))

        db.refresh(event_a)
        assert db.scalar(select(func.count(Event.id))) == 1
        assert db.scalar(select(func.count(EventMention.id))) == 3
        assert event_a.latest_development == "2:1 最终赛果"
        assert event_a.current_revision == 3


def test_esports_match_distinct_dates_create_separate_events() -> None:
    """Case C: two clear match occurrences remain two Events."""
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="distinct occurrences")
        db.add(source)
        db.flush()
        msg1 = _item(
            db, source=source, external_id="msg-aug14", title="8 月 14 日 BLG 对阵 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 14, 8, tzinfo=UTC),
        )
        db.commit()
        _aggregate(
            db, msg1,
            StaticClient(
                _result(
                    _esports_create_decision(
                        title="8 月 14 日 BLG 对阵 TES",
                        match_identity={
                            "participants": ["BLG", "TES"], "match_date": "2026-08-14",
                        },
                    )
                )
            ),
        )
        msg2 = _item(
            db, source=source, external_id="msg-aug16", title="8 月 16 日 BLG 对阵 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 8, tzinfo=UTC),
        )
        db.commit()
        _aggregate(
            db, msg2,
            StaticClient(
                _result(
                    _esports_create_decision(
                        title="8 月 16 日 BLG 对阵 TES",
                        match_identity={
                            "participants": ["BLG", "TES"], "match_date": "2026-08-16",
                        },
                    )
                )
            ),
        )

        assert db.scalar(select(func.count(Event.id))) == 2


def test_out_of_order_esports_material_update_keeps_newest_development() -> None:
    """Case G: processing an old 10:00 message after the 12:00 result must not regress."""
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="out of order")
        db.add(source)
        db.flush()
        newer = _item(
            db, source=source, external_id="newer", title="BLG 2:1 TES 比赛结束",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
        )
        db.commit()
        _aggregate(
            db, newer,
            StaticClient(
                _result(
                    _esports_create_decision(
                        title="BLG 对阵 TES", latest_development="2:1 最终赛果",
                        match_identity={
                            "participants": ["BLG", "TES"], "match_date": "2026-08-16",
                        },
                        evidence="BLG 2:1 TES",
                    )
                )
            ),
        )
        event_a = db.scalar(select(Event).where(Event.event_family == "esports_match"))
        assert event_a is not None
        db.refresh(event_a)
        assert event_a.latest_development == "2:1 最终赛果"

        older = _item(
            db, source=source, external_id="older", title="BLG 1:1 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 10, tzinfo=UTC),
        )
        db.commit()
        _aggregate(
            db, older,
            StaticClient(
                _result(
                    {
                        "mention_index": 0, "action": "attach", "event_id": event_a.id,
                        "product": "lol_esports", "event_family": "esports_match",
                        "relation": "reports", "source_role": "unknown",
                        "materiality": "material_update",
                        "evidence_excerpt": "BLG 1:1 TES",
                        "match_identity": {
                            "participants": ["BLG", "TES"], "match_date": "2026-08-16",
                        },
                        "projection": {"latest_development": "1:1"},
                    }
                )
            ),
        )

        db.refresh(event_a)
        assert db.scalar(select(func.count(Event.id))) == 1
        assert db.scalar(select(func.count(EventMention.id))) == 2
        assert event_a.latest_development == "2:1 最终赛果"


def test_esports_match_recall_window_is_seven_days_family_aware() -> None:
    now = datetime(2026, 8, 16, 8, tzinfo=UTC)
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="recall-window")
        db.add(source)
        db.flush()
        esports_recent = Event(
            title="近期 BLG 对阵 TES", current_summary="",
            event_family="esports_match", products=["lol_esports"],
            canonical_anchors={"participants": ["BLG", "TES"]},
            first_seen_at=now - timedelta(days=6),
            last_seen_at=now - timedelta(days=6),
        )
        esports_old = Event(
            title="8 天前 BLG 对阵 TES", current_summary="",
            event_family="esports_match", products=["lol_esports"],
            canonical_anchors={"participants": ["BLG", "TES"]},
            first_seen_at=now - timedelta(days=8),
            last_seen_at=now - timedelta(days=8),
        )
        gameplay_old = Event(
            title="8 天前平衡调整", current_summary="8 天前的平衡调整。",
            event_family="gameplay_balance", products=["lol_pc"],
            canonical_anchors={"patch_version": "26.16"},
            first_seen_at=now - timedelta(days=8),
            last_seen_at=now - timedelta(days=8),
        )
        db.add_all([esports_recent, esports_old, gameplay_old])
        db.flush()
        esports_message = _item(
            db, source=source, external_id="recall-esports",
            title="BLG 对阵 TES 赛果", products=["lol_esports"],
            topics=["esports_matches"], published_at=now,
        )
        db.commit()

        esports_candidates = recall_event_candidates(
            db, item=esports_message, possible_families=["esports_match"],
        )
        esports_ids = {candidate["event_id"] for candidate in esports_candidates}
        assert esports_recent.id in esports_ids
        assert esports_old.id not in esports_ids

        gameplay_message = _item(
            db, source=source, external_id="recall-gameplay",
            title="平衡调整更新", products=["lol_pc"],
            topics=["balance_gameplay"], published_at=now,
        )
        db.commit()
        gameplay_candidates = recall_event_candidates(
            db, item=gameplay_message, possible_families=["gameplay_balance"],
        )
        gameplay_ids = {candidate["event_id"] for candidate in gameplay_candidates}
        assert gameplay_old.id in gameplay_ids


def test_esports_recall_filters_family_in_sql_before_limit() -> None:
    """Family must be filtered in SQL before the limit so unrelated families cannot starve it.

    Many fresh other-family events would otherwise fill the bounded candidate budget; the routed
    ``esports_match`` candidate must still be recalled even under a small limit.
    """
    now = datetime(2026, 8, 16, 8, tzinfo=UTC)
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="recall-family")
        db.add(source)
        db.flush()
        events = [
            Event(
                title=f"其他家族 {index}", current_summary="",
                event_family="gameplay_balance", products=["lol_pc"],
                canonical_anchors={"patch_version": f"26.{index}"},
                first_seen_at=now - timedelta(hours=index),
                last_seen_at=now - timedelta(hours=index),
            )
            for index in range(30)
        ]
        target = Event(
            title="BLG 对阵 TES", current_summary="",
            event_family="esports_match", products=["lol_esports"],
            canonical_anchors={"participants": ["BLG", "TES"]},
            first_seen_at=now - timedelta(days=1),
            last_seen_at=now - timedelta(days=1),
        )
        target.recall_score = 0.0
        db.add_all([*events, target])
        db.flush()
        esports_message = _item(
            db, source=source, external_id="recall-family-msg",
            title="BLG 对阵 TES 赛果", products=["lol_esports"],
            topics=["esports_matches"], published_at=now,
        )
        db.commit()

        candidates = recall_event_candidates(
            db, item=esports_message, possible_families=["esports_match"],
            total_limit=5,
        )
        ids = {candidate["event_id"] for candidate in candidates}
        # The esports_match candidate survives the SQL filter; other families cannot squeeze it out.
        assert target.id in ids
        assert len(candidates) <= 5
        assert all(candidate["event_family"] == "esports_match" for candidate in candidates)


# ---------------------------------------------------------------------------
# v12: identity gate + explicit match subject (participants as subject)
# ---------------------------------------------------------------------------


def _team_event(
    db: Session,
    *,
    title: str,
    anchors: dict[str, object],
    now: datetime,
) -> Event:
    event = Event(
        title=title, current_summary="比赛消息。",
        event_family="esports_match", products=["lol_esports"],
        canonical_anchors=dict(anchors),
        first_seen_at=now, last_seen_at=now,
    )
    db.add(event)
    db.flush()
    return event


def test_esports_match_participants_conflict_identity_gate_removes_candidate() -> None:
    """JDG/LGD candidate + WBG/IG incoming: participants hard conflict -> gate drops it.

    The candidate is recalled by the 7-day window but removed by the pre-LLM identity
    gate, so it can never be attached (the apply fence also refuses it).
    """
    now = datetime(2026, 8, 16, 8, tzinfo=UTC)
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="participants-conflict")
        db.add(source)
        db.flush()
        event = _team_event(
            db, title="JDG 对阵 LGD",
            anchors={"participants": ["JDG", "LGD"], "match_date": "2026-08-16"},
            now=now,
        )
        item = _item(
            db, source=source, external_id="wbg-ig-result",
            title="WBG 2:1 IG", products=["lol_esports"],
            topics=["esports_matches"],
            entities=[
                {"type": "team", "name": "WBG", "canonical_name": "WBG"},
                {"type": "team", "name": "IG", "canonical_name": "IG"},
            ],
            published_at=now + timedelta(hours=2),
        )
        db.commit()

        recalled = recall_event_candidates(
            db, item=item, possible_families=["esports_match"]
        )
        assert any(candidate["event_id"] == event.id for candidate in recalled)

        gated = esports_match_identity_gate(
            recalled,
            incoming_identity=match_identity_from_message_entities(item.entities),
        )
        assert all(candidate["event_id"] != event.id for candidate in gated)

        # Defense in depth: a forced attach to the dropped candidate fails at apply,
        # because the candidate is no longer in the gated candidate set.
        with pytest.raises(ValueError, match="non-candidate"):
            _aggregate(
                db, item,
                StaticClient(
                    _result(
                        _esports_attach_decision(
                            event_id=event.id,
                            match_identity={"participants": ["WBG", "IG"]},
                            latest_development="WBG 2:1 IG",
                        )
                    )
                ),
            )
        run = db.scalar(select(EventAggregationRun))
        assert run is not None
        assert run.outcome == "apply_error"


def test_esports_match_participants_same_date_missing_keeps_candidate() -> None:
    """participants 相同但 date 缺失: no hard conflict, the candidate stays for LLM judgment."""
    now = datetime(2026, 8, 16, 8, tzinfo=UTC)
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="same-participants")
        db.add(source)
        db.flush()
        event = _team_event(
            db, title="BLG 对阵 TES",
            anchors={"participants": ["BLG", "TES"]},
            now=now,
        )
        item = _item(
            db, source=source, external_id="blg-tes-result",
            title="BLG 2:1 TES", products=["lol_esports"],
            topics=["esports_matches"],
            entities=[
                {"type": "team", "name": "BLG", "canonical_name": "BLG"},
                {"type": "team", "name": "TES", "canonical_name": "TES"},
            ],
            published_at=now + timedelta(hours=2),
        )
        db.commit()

        recalled = recall_event_candidates(
            db, item=item, possible_families=["esports_match"]
        )
        gated = esports_match_identity_gate(
            recalled,
            incoming_identity=match_identity_from_message_entities(item.entities),
        )
        assert any(candidate["event_id"] == event.id for candidate in gated)
        assert esports_match_identity_conflict(
            match_identity_from_anchors(event.canonical_anchors),
            {"participants": ["BLG", "TES"]},
        ) is None


def test_esports_match_external_match_id_same_without_participants_compatible() -> None:
    """participants 缺失但 external_match_id 相同: candidate is compatible."""
    now = datetime(2026, 8, 16, 8, tzinfo=UTC)
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="external-id")
        db.add(source)
        db.flush()
        event = _team_event(
            db, title="LPL 焦点战",
            anchors={"external_match_id": "lpl-100"},
            now=now,
        )
        item = _item(
            db, source=source, external_id="lpl-100-result",
            title="LPL 100 号比赛赛果", products=["lol_esports"],
            topics=["esports_matches"],
            entities=[],
            published_at=now + timedelta(hours=2),
        )
        db.commit()

        recalled = recall_event_candidates(
            db, item=item, possible_families=["esports_match"]
        )
        # No team entities -> incoming identity is empty -> gate is a no-op.
        gated = esports_match_identity_gate(
            recalled,
            incoming_identity=match_identity_from_message_entities(item.entities),
        )
        assert any(candidate["event_id"] == event.id for candidate in gated)
        # With an explicit incoming external_match_id the pair stays compatible.
        assert esports_match_identity_conflict(
            match_identity_from_anchors(event.canonical_anchors),
            {"external_match_id": "lpl-100"},
        ) is None


def test_esports_match_participants_conflict_even_with_same_date_is_hard_conflict() -> None:
    """participants 明确不同，即使日期相同也是 hard conflict."""
    conflict = esports_match_identity_conflict(
        {"participants": ["JDG", "LGD"], "match_date": "2026-08-16"},
        {"participants": ["WBG", "IG"], "match_date": "2026-08-16"},
    )
    assert conflict is not None
    assert conflict.startswith("participants")


def test_esports_match_create_requires_match_subject() -> None:
    """esports_match create needs participants or external_match_id; a bare identity fails."""
    with pytest.raises(ValidationError, match="recognizable match subject"):
        _result(
            _esports_create_decision(
                title="未知比赛", match_identity={},
            )
        )
    # participants 满足最小 create identity contract。
    ok = _result(
        _esports_create_decision(
            title="BLG 对阵 TES",
            match_identity={"participants": ["BLG", "TES"]},
        )
    )
    assert ok.mentions[0].action == "create"
    assert ok.mentions[0].match_identity is not None


def test_esports_match_schema_rejects_candidate_match_identity() -> None:
    """The model schema no longer accepts a model-declared candidate identity."""
    with pytest.raises(ValidationError, match="candidate_match_identity"):
        _result({
            **_esports_attach_decision(
                event_id=123,
                match_identity={"participants": ["BLG", "TES"]},
            ),
            "candidate_match_identity": {"participants": ["BLG", "TES"]},
        })


def test_empty_esports_match_shell_is_not_recalled() -> None:
    """A shell esports_match (empty title, or no match subject) never enters the candidate pool."""
    now = datetime(2026, 8, 16, 8, tzinfo=UTC)
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="empty-shell")
        db.add(source)
        db.flush()
        no_title = _team_event(
            db, title="",
            anchors={"participants": ["BLG", "TES"]},
            now=now,
        )
        no_subject = _team_event(
            db, title="BLG 对阵 TES",
            anchors={"match_date": "2026-08-16"},
            now=now,
        )
        valid = _team_event(
            db, title="BLG 对阵 TES",
            anchors={"participants": ["BLG", "TES"]},
            now=now,
        )
        item = _item(
            db, source=source, external_id="blg-tes-result",
            title="BLG 2:1 TES", products=["lol_esports"],
            topics=["esports_matches"],
            entities=[
                {"type": "team", "name": "BLG", "canonical_name": "BLG"},
                {"type": "team", "name": "TES", "canonical_name": "TES"},
            ],
            published_at=now + timedelta(hours=2),
        )
        db.commit()

        candidates = recall_event_candidates(
            db, item=item, possible_families=["esports_match"]
        )
        ids = {candidate["event_id"] for candidate in candidates}
        assert no_title.id not in ids
        assert no_subject.id not in ids
        assert valid.id in ids
        assert not esports_match_has_subject(no_subject.canonical_anchors)


# ---------------------------------------------------------------------------
# v10: strong-positive same-occurrence evidence / ambiguity regression
# ---------------------------------------------------------------------------


def _esports_candidate(
    *,
    event_id: int,
    anchors: dict[str, object],
) -> dict[str, object]:
    return {
        "event_id": event_id,
        "event_family": "esports_match",
        "products": ["lol_esports"],
        "canonical_anchors": dict(anchors),
        "title": "BLG 对阵 TES",
        "current_summary": "BLG 与 TES 的比赛。",
        "latest_development": "进行中",
        "key_facts": [],
        "lifecycle_status": "developing",
        "last_seen_at": "2026-08-16T12:00:00+00:00",
        "recall_score": 0.5,
        "recall_reasons": ["family_hint"],
    }


def test_v10_case_a_same_external_match_id_rejects_create() -> None:
    """Case A: an equal explicit external_match_id is strong same-occurrence evidence."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "external_match_id": "match-123",
        }
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"], "external_match_id": "match-123"},
    )}
    # No score, no result wording: structured external id alone is enough.
    message = {"title": "BLG 对阵 TES 赛果", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    assert error is not None
    assert "match-123" in error
    assert "不能 create" in error


def test_v10_case_b_same_explicit_match_date_rejects_create() -> None:
    """Case B: participants + equal explicit match_date is strong evidence."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
        }
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
    )}
    message = {"title": "BLG 2:1 TES", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    assert error is not None
    assert "2026-08-16" in error


def test_v10_case_c_different_explicit_match_date_allows_create() -> None:
    """Case C: explicitly different match_date is a different occurrence, create is legal."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
        }
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"], "match_date": "2026-08-14"},
    )}
    message = {"title": "BLG 2:1 TES", "summary": "BLG 再胜一场", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    # A genuinely different recorded occurrence -> the model may create a new Event.
    assert error is None


def test_v10_case_e_participants_only_is_not_deterministic_proof() -> None:
    """Case E: same participants with no other occurrence facts never hard-rejects create."""
    mention = _continuation_mention(
        match_identity={"participants": ["BLG", "TES"]},
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"]},
    )}
    message = {"title": "BLG 2:1 TES", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    # participants alone is a recall signal, never deterministic identity proof.
    assert error is None


def test_v10_case_d_missing_incoming_date_must_not_hard_reject() -> None:
    """Case D: participants same but the incoming date is missing is NOT a proof.

    The candidate has a date, the incoming message only names the two teams and a
    score. Absence of a conflict must never be upgraded into positive identity, so
    the Python validator hands the semantic choice to the model.
    """
    mention = _continuation_mention(
        match_identity={"participants": ["BLG", "TES"]},
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"], "match_date": "2026-08-14"},
    )}
    message = {"title": "BLG 2:1 TES", "summary": "BLG 获得本场胜利", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    # No strong positive evidence -> create must not be hard-rejected deterministically.
    assert error is None


def test_v10_case_f_same_date_different_round_conflict_allows_create() -> None:
    """Case F: equal participants/date but a hard round conflict means a new occurrence."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
            "round": "lower-final",
        }
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
            "round": "upper-final",
        },
    )}
    message = {"title": "败者组决赛 BLG 对阵 TES", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    assert error is None


def test_v10_case_g_score_dash_format_does_not_bypass_protection() -> None:
    """Case G: same-occurrence protection does not depend on the score regex (2-1 vs 2:1)."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
        }
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
    )}
    # A dash-formatted score would not have matched the old colon-only regex.
    message = {"title": "BLG 2-1 TES", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    assert error is not None


def test_v10_case_h_chinese_natural_language_result_does_not_bypass_protection() -> None:
    """Case H: natural-language results do not rely on any Chinese keyword parser."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
        }
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
    )}
    message = {"title": "BLG让一追二击败TES", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    assert error is not None


def test_v10_multiple_strong_evidence_candidates_are_ambiguous() -> None:
    """When several candidates all share strong evidence, do not force a specific attach."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
        }
    )
    candidates = {
        111: _esports_candidate(
            event_id=111,
            anchors={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
        ),
        222: _esports_candidate(
            event_id=222,
            anchors={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
        ),
    }
    message = {"title": "BLG 2:1 TES", "content_form": "original"}

    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message=message
    )

    # Ambiguous between two strong candidates -> model keeps semantic control.
    assert error is None


def test_v10_create_rejected_regardless_of_state_wording() -> None:
    """If strong same-occurrence evidence exists, wording such as '晋级' is irrelevant to rejection."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "match_date": "2026-08-16",
        }
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
    )}
    for wording in (
        "BLG 战胜 TES 晋级下一轮",
        "BLG 拿下关键一局",
        "TES 被 BLG 淘汰出局",
    ):
        message = {"title": wording, "content_form": "original"}
        error = esports_match_create_continuation_error(
            mention, candidates=candidates, message=message
        )
        assert error is not None


# ---------------------------------------------------------------------------
# v10: projection chronology (mention-own patch, evidence-time replay)
# ---------------------------------------------------------------------------


def _esports_projection_attach(
    *,
    event_id: int,
    match_identity: dict[str, object],
    latest_development: str,
    materiality: str = "material_update",
) -> dict[str, object]:
    decision = _esports_attach_decision(
        event_id=event_id,
        match_identity=match_identity,
    )
    if materiality != "material_update":
        decision["materiality"] = materiality
    else:
        decision["projection"] = {"latest_development": latest_development}
    return decision


def _bump_revision(db: Session, item: NormalizedItem) -> None:
    item.current_revision += 1


def test_v10_projection_invalidation_restores_previous_material() -> None:
    """Projection case: 10:00 1:1 -> 12:00 final; invalidating 12:00 restores 1:1."""
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="proj-invalidate")
        db.add(source)
        db.flush()
        first = _item(
            db, source=source, external_id="proj-1:1", title="BLG 1:1 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 10, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, first, StaticClient(_result(_esports_create_decision(
            title="BLG 对阵 TES", latest_development="1:1",
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            evidence="BLG 1:1 TES",
        ))))
        event = db.scalar(select(Event).where(Event.event_family == "esports_match"))
        assert event is not None

        second = _item(
            db, source=source, external_id="proj-final", title="BLG 2:1 TES 比赛结束",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, second, StaticClient(_result(_esports_projection_attach(
            event_id=event.id,
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            latest_development="2:1 最终赛果",
        ))))
        db.refresh(event)
        assert event.latest_development == "2:1 最终赛果"
        assert event.latest_update_message_id == second.id

        # Invalidate the 12:00 final evidence (revision advances away from it).
        _bump_revision(db, second)
        db.commit()
        refresh_event_metrics(db, {event.id})
        db.commit()
        db.refresh(event)

        assert event.latest_development == "1:1"
        assert event.latest_update_message_id == first.id


def test_v10_projection_out_of_order_then_invalidate_restores_old_evidence() -> None:
    """Critical case: 12:00 final then 10:00 old 1:1; invalidating final restores 1:1.

    The 10:00 revision must hold only its own '1:1' patch. It must NOT have baked the
    global '2:1 最终赛果' into its snapshot, otherwise restoring after the final is
    invalidated would resurrect the final state.
    """
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="proj-ooo")
        db.add(source)
        db.flush()
        newer = _item(
            db, source=source, external_id="ooo-final", title="BLG 2:1 TES 比赛结束",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, newer, StaticClient(_result(_esports_create_decision(
            title="BLG 对阵 TES", latest_development="2:1 最终赛果",
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            evidence="BLG 2:1 TES",
        ))))
        event = db.scalar(select(Event).where(Event.event_family == "esports_match"))
        assert event is not None

        older = _item(
            db, source=source, external_id="ooo-1:1", title="BLG 1:1 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 10, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, older, StaticClient(_result(_esports_projection_attach(
            event_id=event.id,
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            latest_development="1:1",
        ))))
        db.refresh(event)
        # Out-of-order 10:00 must not regress the live projection.
        assert event.latest_development == "2:1 最终赛果"

        # Invalidate the 12:00 final evidence.
        _bump_revision(db, newer)
        db.commit()
        refresh_event_metrics(db, {event.id})
        db.commit()
        db.refresh(event)

        # The final must NOT resurrect from the older revision's snapshot.
        assert event.latest_development == "1:1"
        assert event.latest_update_message_id == older.id


def test_v10_projection_same_evidence_time_uses_deterministic_winner() -> None:
    """Tie-break: two material updates at the same evidence time share one winner."""
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="proj-tie")
        db.add(source)
        db.flush()
        first = _item(
            db, source=source, external_id="tie-seed", title="BLG 1:1 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 10, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, first, StaticClient(_result(_esports_create_decision(
            title="BLG 对阵 TES", latest_development="1:1",
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            evidence="BLG 1:1 TES",
        ))))
        event = db.scalar(select(Event).where(Event.event_family == "esports_match"))
        assert event is not None

        same_time = datetime(2026, 8, 16, 12, tzinfo=UTC)
        second = _item(
            db, source=source, external_id="tie-b", title="BLG 2:1 TES 赛果",
            products=["lol_esports"], topics=["esports_matches"], published_at=same_time,
        )
        db.commit()
        _aggregate(db, second, StaticClient(_result(_esports_projection_attach(
            event_id=event.id,
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            latest_development="2:1 赛果甲",
        ))))
        third = _item(
            db, source=source, external_id="tie-c", title="BLG 2:1 TES 再确认",
            products=["lol_esports"], topics=["esports_matches"], published_at=same_time,
        )
        db.commit()
        _aggregate(db, third, StaticClient(_result(_esports_projection_attach(
            event_id=event.id,
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            latest_development="2:1 赛果乙",
        ))))
        db.refresh(event)

        # Higher mention.id wins on the evidence-time tie; latest_development and
        # latest_update_message_id must be derived from that same deterministic winner.
        assert event.latest_development == "2:1 赛果乙"
        assert event.latest_update_message_id == third.id
        assert event.last_material_update_at.replace(tzinfo=UTC) == same_time


def test_v10_non_material_attach_does_not_advance_latest_projection() -> None:
    """Projection case: corroboration_only/duplicate/context_only never advance latest."""
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="proj-nonmaterial")
        db.add(source)
        db.flush()
        first = _item(
            db, source=source, external_id="nm-seed", title="BLG 1:1 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 10, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, first, StaticClient(_result(_esports_create_decision(
            title="BLG 对阵 TES", latest_development="1:1",
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            evidence="BLG 1:1 TES",
        ))))
        event = db.scalar(select(Event).where(Event.event_family == "esports_match"))
        assert event is not None

        for index, materiality in enumerate(
            ("corroboration_only", "duplicate", "context_only")
        ):
            msg = _item(
                db, source=source, external_id=f"nm-{index}", title=f"佐证 {index}",
                products=["lol_esports"], topics=["esports_matches"],
                published_at=datetime(2026, 8, 16, 11 + index, tzinfo=UTC),
            )
            db.commit()
            _aggregate(db, msg, StaticClient(_result(_esports_projection_attach(
                event_id=event.id,
                match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
                latest_development="不应推进",
                materiality=materiality,
            ))))

        db.refresh(event)
        assert event.latest_development == "1:1"
        assert event.latest_update_message_id == first.id
        assert (
            event.last_material_update_at.replace(tzinfo=UTC)
            == datetime(2026, 8, 16, 10, tzinfo=UTC)
        )


# ---------------------------------------------------------------------------
# v10: real LLM retry correction integration (create -> reject -> attach)
# ---------------------------------------------------------------------------


class _FakeCompletions:
    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.calls: list[dict[str, object]] = []

    async def create(self, **kwargs: object) -> object:
        self.calls.append(kwargs)
        content = self.responses[len(self.calls) - 1]
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
        )


def _llm_client_with_responses(responses: list[str]) -> tuple[LLMClient, _FakeCompletions]:
    client = LLMClient()
    completions = _FakeCompletions(responses)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def _esports_create_response() -> str:
    return json.dumps(
        {
            "mentions": [
                {
                    "mention_index": 0,
                    "action": "create",
                    "event_id": None,
                    "product": "lol_esports",
                    "event_family": "esports_match",
                    "relation": "reports",
                    "source_role": "unknown",
                    "materiality": "material_update",
                    "evidence_excerpt": "BLG 2:1 TES",
                    "match_identity": {
                        "participants": ["BLG", "TES"],
                        "match_date": "2026-08-16",
                    },
                    "new_event": {
                        "title": "BLG 对阵 TES",
                        "summary": "BLG 2:1 战胜 TES。",
                        "canonical_anchors": {
                            "participants": ["BLG", "TES"],
                            "match_date": "2026-08-16",
                        },
                        "latest_development": "2:1 最终赛果",
                        "key_facts": [],
                    },
                }
            ]
        },
        ensure_ascii=False,
    )


def _esports_attach_response() -> str:
    return json.dumps(
        {
            "mentions": [
                {
                    "mention_index": 0,
                    "action": "attach",
                    "event_id": 123,
                    "product": "lol_esports",
                    "event_family": "esports_match",
                    "relation": "reports",
                    "source_role": "unknown",
                    "materiality": "material_update",
                    "evidence_excerpt": "BLG 2:1 TES",
                    "match_identity": {
                        "participants": ["BLG", "TES"],
                        "match_date": "2026-08-16",
                    },
                    "projection": {"latest_development": "2:1 最终赛果"},
                }
            ]
        },
        ensure_ascii=False,
    )


def test_v10_aggregate_events_retries_create_into_attach_with_business_feedback() -> None:
    """Real retry correction loop: first create is rejected, second attach passes."""
    candidates = [_esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
    )]
    message = {
        "title": "BLG 2:1 TES",
        "summary": "BLG 战胜 TES 晋级下一轮",
        "content_form": "original",
    }

    client, completions = _llm_client_with_responses(
        [_esports_create_response(), _esports_attach_response()]
    )
    result = asyncio.run(
        client.aggregate_events(
            message=message,
            possible_event_families=["esports_match"],
            candidates=candidates,
        )
    )

    assert len(completions.calls) == 2
    mention = result.mentions[0]
    assert mention.action == "attach"
    assert mention.event_id == 123
    assert execution_metadata(result)["retry_count"] == 1

    # The second request must repeat the rejected assistant output and then a user
    # correction that spells out the concrete business validation reason.
    second_messages = completions.calls[1]["messages"]
    assistant_contents = [
        msg["content"] for msg in second_messages if msg["role"] == "assistant"
    ]
    user_contents = [msg["content"] for msg in second_messages if msg["role"] == "user"]
    assert any(
        json.loads(content)["mentions"][0]["action"] == "create"
        for content in assistant_contents
    )
    correction = next(content for content in user_contents if "不能 create" in content)
    assert "强正的" in correction or "同一场次" in correction
    assert "material_update" in correction
    assert "123" in correction


# ---------------------------------------------------------------------------
# v11: final semantics
#  - LLM semantic continuation vs Python strong-evidence guard threshold
#  - scheduled_at identity compares full datetime, never degenerate to a date
#  - external_match_id is decisive on its own (works without participants)
#  - clean projection-restore baseline (invalidated mention evidence never survives)
#  - material_update requires a latest_development projection; non-material never has one
# ---------------------------------------------------------------------------


def test_v11_prompt_teaches_semantic_continuation_beyond_strong_evidence() -> None:
    """Item 1: strong structured evidence is Python's deterministic guard threshold, not
    the LLM's attach threshold. The prompt must let the model attach from lifecycle
    semantics even when no strong structured fact is present."""
    candidates = [_esports_candidate(
        event_id=123,
        anchors={"participants": ["BLG", "TES"]},
    )]
    message = {
        "title": "BLG 2:1 TES 比赛结束",
        "summary": "比分从 1:1 推进到 2:1，比赛结束",
        "content_form": "original",
    }
    client, completions = _llm_client_with_responses([_esports_attach_response()])
    result = asyncio.run(
        client.aggregate_events(
            message=message,
            possible_event_families=["esports_match"],
            candidates=candidates,
        )
    )
    # The model is free to attach on lifecycle semantics; no deterministic rejection.
    assert result.mentions[0].action == "attach"
    system_content = next(
        msg["content"]
        for msg in completions.calls[0]["messages"]
        if msg["role"] == "system"
    )
    # The shortened prompt still teaches the core semantic contract: candidates
    # passed structural identity filtering, but the model must still judge the
    # same-match lifecycle continuation, and participants alone are not enough.
    assert "continuation-first" in system_content
    assert "participants 相同本身不等于同一场" in system_content
    assert "不能因为 score、title、winner、lifecycle status" in system_content


def test_v11_same_day_different_scheduled_at_is_distinct_occurrence() -> None:
    """Item 2: same participants, candidate scheduled 10:00 vs incoming 18:00 on the same
    day -> different occurrence (create is legal). scheduled_at must not degenerate to a date."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "scheduled_at": "2026-08-16T18:00:00+00:00",
        }
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={
            "participants": ["BLG", "TES"],
            "scheduled_at": "2026-08-16T10:00:00+00:00",
        },
    )}
    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message={}
    )
    assert error is None


def test_v11_identical_scheduled_at_is_strong_same_occurrence() -> None:
    """Item 3: same participants + identical full scheduled_at -> strong same-occurrence evidence."""
    mention = _continuation_mention(
        match_identity={
            "participants": ["BLG", "TES"],
            "scheduled_at": "2026-08-16T18:00:00+00:00",
        }
    )
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={
            "participants": ["BLG", "TES"],
            "scheduled_at": "2026-08-16T18:00:00+00:00",
        },
    )}
    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message={}
    )
    assert error is not None
    assert "scheduled_at 一致" in error


def test_v11_external_match_id_alone_is_strong_without_participants() -> None:
    """Item 8: an equal explicit external_match_id is strong evidence even when participants
    are absent; participants must not gate it."""
    mention = _continuation_mention(match_identity={"external_match_id": "match-123"})
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"external_match_id": "match-123"},
    )}
    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message={}
    )
    assert error is not None
    assert "external_match_id 一致" in error


def test_v11_different_external_match_id_is_hard_conflict() -> None:
    """Item 9: explicitly different external_match_id is a hard conflict -> create is legal."""
    mention = _continuation_mention(match_identity={"external_match_id": "match-123"})
    candidates = {123: _esports_candidate(
        event_id=123,
        anchors={"external_match_id": "match-999"},
    )}
    error = esports_match_create_continuation_error(
        mention, candidates=candidates, message={}
    )
    assert error is None


def test_v11_material_update_attach_requires_latest_development_projection() -> None:
    """Item 6: a material_update attach must carry a projection with latest_development."""
    with pytest.raises(ValidationError, match="latest_development"):
        _result({
            **_esports_attach_decision(
                event_id=123,
                match_identity={"participants": ["BLG", "TES"]},
            ),
        })
    with pytest.raises(ValidationError, match="latest_development"):
        _result({
            **_esports_attach_decision(
                event_id=123,
                match_identity={"participants": ["BLG", "TES"]},
            ),
            "projection": {"title": "只改标题，没有最新进展"},
        })
    ok = _result({
        **_esports_attach_decision(
            event_id=123,
            match_identity={"participants": ["BLG", "TES"]},
        ),
        "projection": {"latest_development": "2:1 最终赛果"},
    })
    assert ok.mentions[0].projection is not None
    assert ok.mentions[0].projection.latest_development == "2:1 最终赛果"


def test_v11_non_material_attach_rejects_projection() -> None:
    """Item 7: corroboration_only/duplicate/context_only attach must not carry a projection,
    so they can never advance latest_update_message_id / last_material_update_at /
    latest_development (behavioural no-advance is covered by
    test_v10_non_material_attach_does_not_advance_latest_projection)."""
    for materiality in ("corroboration_only", "duplicate", "context_only"):
        with pytest.raises(ValidationError, match="non-material attach"):
            _result({
                **_esports_attach_decision(
                    event_id=123,
                    match_identity={"participants": ["BLG", "TES"]},
                ),
                "materiality": materiality,
                "projection": {"latest_development": "不应推进"},
            })


def test_v11_invalidating_creator_mention_clears_stale_projection() -> None:
    """Item 4: creator mention A provides title/summary/anchors/latest; attach B only updates
    latest_development. Invalidating A must NOT leave A's presentation evidence behind: the
    restored projection is rebuilt from a clean baseline and only B's still-valid patch survives."""
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="proj-creator-invalid")
        db.add(source)
        db.flush()
        creator = _item(
            db, source=source, external_id="creator-A", title="BLG 1:1 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 10, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, creator, StaticClient(_result(_esports_create_decision(
            title="BLG 对阵 TES 首局", latest_development="1:1 开始",
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            evidence="BLG 1:1 TES",
        ))))
        event = db.scalar(select(Event).where(Event.event_family == "esports_match"))
        assert event is not None
        assert event.canonical_anchors.get("participants") == ["BLG", "TES"]

        followup = _item(
            db, source=source, external_id="followup-B", title="BLG 2:1 TES 结果",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, followup, StaticClient(_result(_esports_projection_attach(
            event_id=event.id,
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            latest_development="2:1 最终赛果",
        ))))
        db.refresh(event)

        # Invalidate the creator evidence; only B's latest_development stays valid.
        _bump_revision(db, creator)
        db.commit()
        refresh_event_metrics(db, {event.id})
        db.commit()
        db.refresh(event)

        assert event.latest_development == "2:1 最终赛果"
        # No stale A projection may survive.
        assert event.title == ""
        assert event.current_summary == ""
        assert event.canonical_anchors == {}
        assert event.key_facts == []


def test_v11_out_of_order_and_invalidation_keep_projection_correct() -> None:
    """Item 5: late-reprocessed older message + invalidation of the newest message must
    restore only still-valid evidence, thanks to the clean restore baseline."""
    engine = _engine()
    with Session(engine, expire_on_commit=False) as db:
        source = Source(name="proj-ooo-baseline")
        db.add(source)
        db.flush()
        newer = _item(
            db, source=source, external_id="ooo-new", title="BLG 2:1 TES 比赛结束",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 12, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, newer, StaticClient(_result(_esports_create_decision(
            title="BLG 对阵 TES", latest_development="2:1 最终赛果",
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            evidence="BLG 2:1 TES",
        ))))
        event = db.scalar(select(Event).where(Event.event_family == "esports_match"))
        assert event is not None

        older = _item(
            db, source=source, external_id="ooo-old", title="BLG 1:1 TES",
            products=["lol_esports"], topics=["esports_matches"],
            published_at=datetime(2026, 8, 16, 10, tzinfo=UTC),
        )
        db.commit()
        _aggregate(db, older, StaticClient(_result(_esports_projection_attach(
            event_id=event.id,
            match_identity={"participants": ["BLG", "TES"], "match_date": "2026-08-16"},
            latest_development="1:1",
        ))))
        # Out-of-order older message must not regress the live projection.
        assert event.latest_development == "2:1 最终赛果"

        _bump_revision(db, newer)
        db.commit()
        refresh_event_metrics(db, {event.id})
        db.commit()
        db.refresh(event)

        assert event.latest_development == "1:1"
        assert event.latest_update_message_id == older.id
