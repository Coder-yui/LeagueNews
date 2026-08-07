import asyncio
from datetime import UTC, datetime

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

import app.models  # noqa: F401
import app.workflows.reviewed_pipeline as reviewed_pipeline
from app.core.database import Base
from app.domain.evidence import evaluate_evidence_gate
from app.models.media_asset import MediaAsset
from app.models.pipeline import ProcessingCheckpoint
from app.models.raw_item import RawItem
from app.models.source import Source
from app.models.workflow import ProcessingRun, ReviewTask
from app.services.llm import RelevanceResult


def _session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return Session(engine, expire_on_commit=False)


def _raw_item(
    db: Session,
    *,
    title: str | None,
    text: str | None,
    source_key: str = "ordinary",
    official: bool = False,
) -> RawItem:
    source = Source(
        name="x",
        connector_type="x_twitter",
        external_key=source_key,
        connector_config={},
        is_official=official,
    )
    db.add(source)
    db.flush()
    raw_item = RawItem(
        source_id=source.id,
        external_id=f"evidence-{source_key}",
        native_title=title,
        content_blocks=(
            [{"id": "b0001", "type": "paragraph", "text": text}]
            if text
            else [{"id": "b0001", "type": "image", "storage_path": "image.png"}]
        ),
        published_at=datetime(2026, 8, 1, tzinfo=UTC),
    )
    db.add(raw_item)
    db.flush()
    raw_item.media_assets.append(
        MediaAsset(block_index=1, mime_type="image/png", storage_path="image.png")
    )
    db.commit()
    return raw_item


def test_ordinary_images_are_ignored_when_source_text_is_sufficient() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="26.16 版本更新公告",
            text="本次更新包含英雄平衡调整和客户端问题修复。",
        )

        gate = evaluate_evidence_gate(raw_item, designer_patch_images=False)

        assert gate.decision == "process"
        assert gate.requires_manual_review is False
        assert gate.reason_code == "usable_source_evidence"
        assert gate.evidence_sources == ("source_title", "source_text")


def test_ordinary_image_only_post_completes_with_insufficient_evidence(monkeypatch) -> None:
    class FailingClient:
        async def judge_relevance(self, **_kwargs):
            raise AssertionError("evidence gate must stop before relevance LLM")

    monkeypatch.setattr(reviewed_pipeline, "LLMClient", FailingClient)
    with _session() as db:
        raw_item = _raw_item(db, title=None, text=None)

        run = asyncio.run(
            reviewed_pipeline.start_item_processing(db, raw_item, execution_mode="automatic")
        )

        checkpoint = db.scalar(
            select(ProcessingCheckpoint).where(
                ProcessingCheckpoint.processing_run_id == run.id,
                ProcessingCheckpoint.stage == "relevance",
            )
        )
        assert run.status == "completed"
        assert run.outcome == "insufficient_evidence"
        assert checkpoint is not None
        assert checkpoint.decision_source == "automatic"
        assert checkpoint.artifact_references["policy_version"] == ("evidence-insufficient-v1")
        assert checkpoint.output_snapshot["evidence_gate"]["reason_code"] == (
            "source_text_too_short"
        )
        assert not list(
            db.scalars(select(ReviewTask).where(ReviewTask.processing_run_id == run.id))
        )


def test_synthesized_display_title_does_not_inflate_evidence() -> None:
    with _session() as db:
        raw_item = _raw_item(db, title=None, text=None)
        raw_item.source.name = "League of Legends Official Account"

        gate = evaluate_evidence_gate(raw_item, designer_patch_images=False)

        assert raw_item.display_title == "League of Legends Official Account"
        assert gate.meaningful_text_characters == 0
        assert gate.evidence_sources == ()
        assert gate.decision == "insufficient_evidence"
        assert gate.reason_code == "source_text_too_short"


def test_resumed_low_evidence_run_supersedes_stale_relevance_review(
    monkeypatch,
) -> None:
    class FailingClient:
        async def judge_relevance(self, **_kwargs):
            raise AssertionError("evidence gate must stop before relevance LLM")

    monkeypatch.setattr(reviewed_pipeline, "LLMClient", FailingClient)
    with _session() as db:
        raw_item = _raw_item(db, title=None, text=None)
        run = ProcessingRun(
            raw_item_id=raw_item.id,
            workflow_type="item",
            status="awaiting_review",
            current_stage="relevance",
            execution_mode="automatic",
        )
        db.add(run)
        db.flush()
        review = ReviewTask(
            processing_run_id=run.id,
            stage="relevance",
            status="pending",
            proposal={"requires_manual_review": True},
        )
        db.add(review)
        db.commit()

        asyncio.run(reviewed_pipeline._evaluate_relevance(db, run))

        assert run.status == "completed"
        assert run.outcome == "insufficient_evidence"
        assert review.status == "superseded"
        assert review.resolved_at is not None


def test_designer_patch_image_routes_to_ocr_review(monkeypatch) -> None:
    class RelevantClient:
        async def judge_relevance(self, **_kwargs):
            return RelevanceResult(
                product_scope="lol_pc",
                is_lol_relevant=True,
                confidence=0.99,
                reason="设计师版本预览",
            )

    async def fake_ocr_review(db: Session, run, **_kwargs) -> None:
        reviewed_pipeline._replace_pending_review(
            db,
            run=run,
            stage=reviewed_pipeline.OCR_STAGE,
            proposal={"approved_media_extraction_ids": []},
        )
        db.commit()

    monkeypatch.setattr(reviewed_pipeline, "LLMClient", RelevantClient)
    monkeypatch.setattr(reviewed_pipeline, "_generate_ocr_review", fake_ocr_review)
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="Patch 26.16 preview",
            text=None,
            source_key="RiotPhroxzon",
        )

        run = asyncio.run(reviewed_pipeline.start_item_processing(db, raw_item))

        review = db.scalar(select(ReviewTask).where(ReviewTask.processing_run_id == run.id))
        assert review is not None
        assert review.stage == "image_ocr"
        assert run.context["evidence_gate"]["reason"] == ("进入设计师版本改动图片提取")


def test_approved_patch_structure_becomes_usable_evidence() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="Patch 26.16 preview",
            text=None,
            source_key="RiotPhroxzon",
        )

        gate = evaluate_evidence_gate(
            raw_item,
            designer_patch_images=True,
            designer_patch_extraction_count=1,
        )

        assert gate.decision == "process"
        assert gate.reason_code == "usable_source_evidence"
        assert "designer_patch_changes" in gate.evidence_sources
        assert gate.designer_patch_extraction_count == 1


def test_official_repost_cannot_become_first_party_evidence() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="官方账号转发",
            text="转发一则第三方爆料，内容仍待确认。",
            official=True,
        )
        raw_item.content_blocks.append(
            {
                "id": "b0002",
                "type": "embed",
                "embed_kind": "quoted_post",
                "source_url": "https://example.com/original",
                "text": "原帖",
            }
        )

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(
            {"source_kind": "first_party", "content_form": "original"}, raw_item
        )

        assert guarded["source_kind"] == "attributed_report"
        assert guarded["content_form"] == "repost"
        assert {item["field"] for item in guarded["classification_guardrails"]} == {
            "source_kind",
            "content_form",
        }


def test_content_semantics_normalize_classification_before_scoring_and_routing() -> None:
    cases = [
        {
            "source_key": "official-hotfix",
            "official": True,
            "title": "8月6日不停机更新公告",
            "text": "本次更新修复多个英雄和模式BUG。",
            "proposal": {
                "topic": "game_mode",
                "subtopic": "game_mode_update",
                "source_kind": "first_party",
                "information_stage": "update",
                "content_form": "original",
                "event_assertion": "asserted",
                "event_mentions": [
                    {
                        "topic": "game_mode",
                        "subtopic": "game_mode_update",
                        "assertion": "asserted",
                    }
                ],
            },
            "expected": ("patch", "hotfix", "first_party", "active", "asserted", 1),
        },
        {
            "source_key": "pbe-bundle",
            "official": False,
            "title": "【测试服】怀旧模式礼包封面一览",
            "text": "礼包细节都在普通图片中。",
            "proposal": {
                "topic": "game_mode",
                "subtopic": "game_mode_release",
                "source_kind": "community",
                "information_stage": "announcement",
                "content_form": "original",
                "event_assertion": "asserted",
                "event_mentions": [
                    {
                        "topic": "game_mode",
                        "subtopic": "game_mode_release",
                        "assertion": "asserted",
                    }
                ],
            },
            "expected": (
                "commerce",
                "shop_offer",
                "data_mined",
                "preview",
                "speculative",
                0,
            ),
        },
        {
            "source_key": "pbe-balance",
            "official": False,
            "title": "26.16测试服英雄数值改动",
            "text": "多个英雄技能伤害发生变化。",
            "proposal": {
                "topic": "champion",
                "subtopic": "champion_update",
                "source_kind": "community",
                "information_stage": "update",
                "content_form": "original",
                "event_assertion": "asserted",
                "event_mentions": [
                    {
                        "topic": "champion",
                        "subtopic": "champion_update",
                        "assertion": "asserted",
                    }
                ],
            },
            "expected": (
                "patch",
                "pbe_change",
                "data_mined",
                "preview",
                "speculative",
                1,
            ),
        },
        {
            "source_key": "third-party-balance",
            "official": False,
            "title": None,
            "text": "Azir Q base damage changed from 60-140 to 75-135.",
            "proposal": {
                "topic": "champion",
                "subtopic": "champion_update",
                "source_kind": "first_party",
                "information_stage": "update",
                "content_form": "original",
                "event_assertion": "asserted",
                "event_mentions": [
                    {
                        "topic": "champion",
                        "subtopic": "champion_update",
                        "assertion": "asserted",
                    }
                ],
            },
            "expected": (
                "champion",
                "champion_update",
                "attributed_report",
                "preview",
                "speculative",
                1,
            ),
        },
        {
            "source_key": "third-party-cosmetic-update",
            "official": False,
            "title": None,
            "text": "Two skin icons got tweaked.",
            "proposal": {
                "topic": "skin",
                "subtopic": "skin_release",
                "source_kind": "first_party",
                "information_stage": "update",
                "content_form": "original",
                "event_assertion": "asserted",
                "event_mentions": [
                    {
                        "topic": "skin",
                        "subtopic": "skin_release",
                        "assertion": "asserted",
                    }
                ],
            },
            "expected": (
                "skin",
                "skin_release",
                "attributed_report",
                "preview",
                "speculative",
                1,
            ),
        },
        {
            "source_key": "official-interaction-post",
            "official": True,
            "title": "经典模式即将上线",
            "text": "你还记得当年有哪些出圈的梗吗？",
            "proposal": {
                "topic": "game_mode",
                "subtopic": "game_mode_release",
                "source_kind": "first_party",
                "information_stage": "announcement",
                "content_form": "original",
                "event_assertion": "asserted",
                "event_mentions": [
                    {
                        "topic": "game_mode",
                        "subtopic": "game_mode_release",
                        "assertion": "asserted",
                    }
                ],
            },
            "expected": (
                "community",
                "community_post",
                "community",
                "commentary",
                "context_only",
                0,
            ),
        },
    ]

    for case in cases:
        with _session() as db:
            raw_item = _raw_item(
                db,
                title=case["title"],
                text=case["text"],
                source_key=case["source_key"],
                official=case["official"],
            )
            guarded = reviewed_pipeline._apply_classification_evidence_guardrails(
                case["proposal"], raw_item
            )

            actual = (
                guarded["topic"],
                guarded["subtopic"],
                guarded["source_kind"],
                guarded["information_stage"],
                guarded["event_assertion"],
                len(guarded["event_mentions"]),
            )
            assert actual == case["expected"], case["source_key"]
            assert guarded["classification_guardrails"]


def test_lottery_activity_without_pass_mechanics_is_not_an_event_pass() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="赛季征程活动开启",
            text="完成任务可参与抽奖，最高有机会获得限定皮肤。",
        )
        proposal = {
            "topic": "activity",
            "subtopic": "event_pass",
            "event_mentions": [
                {
                    "topic": "activity",
                    "subtopic": "event_pass",
                    "membership_role": "primary",
                }
            ],
        }

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(proposal, raw_item)

        assert guarded["subtopic"] == "in_game_activity"
        assert guarded["event_mentions"][0]["subtopic"] == "in_game_activity"


def test_lottery_evidence_cannot_be_classified_as_a_free_reward() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="庆典抽奖活动",
            text="参与活动有机会获得皮肤奖励。",
        )

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(
            {"topic": "activity", "subtopic": "free_reward"}, raw_item
        )

        assert guarded["subtopic"] == "in_game_activity"


def test_paid_lottery_is_an_activity_not_an_event_pass() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="限时秘宝活动",
            text="消耗点券购买抽奖机会，累计抽数有里程碑奖励，奖池包含臻彩和皮肤。",
        )

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(
            {"topic": "activity", "subtopic": "event_pass"}, raw_item
        )

        assert guarded["subtopic"] == "in_game_activity"


def test_explicit_paid_progression_preserves_event_pass_classification() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="主题宝典开启",
            text="购买宝典后完成任务提升等级并解锁等级奖励。",
        )

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(
            {"topic": "activity", "subtopic": "event_pass"}, raw_item
        )

        assert guarded["subtopic"] == "event_pass"


def test_named_activity_reward_opening_overrides_community_tone() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="【周年庆皮肤开箱】分享大会开始",
            text="活动页面传送门和领取入口已经开放。",
        )
        proposal = {
            "topic": "community",
            "subtopic": "community_post",
            "source_kind": "community",
            "information_stage": "commentary",
            "event_assertion": "context_only",
            "temporal": {
                "is_recurring": False,
                "recurrence_window": None,
                "certainty": "confirmed",
                "event_date": None,
            },
            "entities": [
                {
                    "name": "周年庆典",
                    "canonical_name": "周年庆典",
                    "type": "activity",
                }
            ],
            "entity_roles": [{"name": "周年庆典", "role": "context"}],
            "event_mentions": [],
        }

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(proposal, raw_item)

        assert guarded["topic"] == "activity"
        assert guarded["subtopic"] == "free_reward"
        assert guarded["information_stage"] == "reminder"
        assert guarded["event_assertion"] == "asserted"
        assert guarded["entity_roles"] == [{"name": "周年庆典", "role": "core"}]
        assert guarded["event_mentions"] == [
            {
                "topic": "activity",
                "subtopic": "free_reward",
                "identity_entities": [
                    {
                        "name": "周年庆典",
                        "canonical_name": "周年庆典",
                        "type": "activity",
                        "role": "core",
                    }
                ],
                "assertion": "asserted",
                "temporal": proposal["temporal"],
                "membership_role": "primary",
            }
        ]


def test_activity_unboxing_discussion_without_opening_evidence_stays_community() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="周年庆开箱结果分享",
            text="大家晒晒自己领取到了什么皮肤。",
        )
        proposal = {
            "topic": "community",
            "subtopic": "community_post",
            "source_kind": "community",
            "information_stage": "commentary",
            "entities": [{"name": "周年庆典", "type": "activity"}],
            "event_mentions": [],
        }

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(proposal, raw_item)

        assert guarded["topic"] == "community"
        assert guarded["subtopic"] == "community_post"
        assert guarded["event_mentions"] == []


def test_paid_activity_unboxing_is_not_a_free_reward_opening() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="秘宝开箱活动现已开启",
            text="消耗点券购买宝箱，开启后随机获得皮肤。",
        )
        proposal = {
            "topic": "activity",
            "subtopic": "in_game_activity",
            "source_kind": "first_party",
            "information_stage": "active",
            "entities": [{"name": "秘宝活动", "type": "activity"}],
            "event_mentions": [],
        }

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(proposal, raw_item)

        assert guarded["subtopic"] == "in_game_activity"
        assert guarded["event_mentions"] == []


def test_skin_acquisition_method_with_pass_evidence_is_an_event_pass() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="两款复古外观获取方式",
            text="现有线索推测它们将通过赛季通行证礼包获得。",
        )
        proposal = {
            "title": "两款复古外观获取方式",
            "topic": "skin",
            "subtopic": "skin_release",
            "content_form": "original",
            "entities": [
                {"name": "复古模式", "type": "game_mode"},
                {"name": "通行证礼包", "type": "product"},
            ],
            "event_mentions": [
                {
                    "topic": "skin",
                    "subtopic": "skin_release",
                    "identity_entities": [
                        {
                            "name": "两款复古外观",
                            "canonical_name": "两款复古外观",
                            "type": "skin",
                            "role": "core",
                        }
                    ],
                    "assertion": "speculative",
                    "temporal": {"event_date": None},
                    "membership_role": "primary",
                }
            ],
        }

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(proposal, raw_item)

        assert guarded["topic"] == "activity"
        assert guarded["subtopic"] == "event_pass"
        assert guarded["event_mentions"] == [
            {
                "topic": "activity",
                "subtopic": "event_pass",
                "identity_entities": [
                    {
                        "name": "复古模式通行证",
                        "canonical_name": "复古模式通行证",
                        "type": "product",
                        "role": "core",
                    }
                ],
                "assertion": "speculative",
                "temporal": {"event_date": None},
                "membership_role": "primary",
            }
        ]


def test_same_batch_skin_collection_produces_one_release_mention() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="星辉冠军皮肤即将上线",
            text="六款星辉冠军皮肤及签名版将在同一天上线。",
        )
        proposal = {
            "title": "星辉冠军皮肤即将上线",
            "topic": "skin",
            "subtopic": "skin_release",
            "content_form": "original",
            "entities": [{"name": f"星辉皮肤{index}", "type": "skin"} for index in range(1, 7)],
            "event_mentions": [
                {
                    "topic": "skin",
                    "subtopic": "skin_release",
                    "identity_entities": [
                        {
                            "name": f"星辉皮肤{index}",
                            "canonical_name": f"星辉皮肤{index}",
                            "type": "skin",
                            "role": "core",
                        }
                    ],
                    "assertion": "asserted",
                    "temporal": {"event_date": "2026-08-10"},
                    "membership_role": "primary" if index == 1 else "component",
                }
                for index in range(1, 7)
            ]
            + [
                {
                    "topic": "commerce",
                    "subtopic": "shop_rotation",
                    "identity_entities": [
                        {
                            "name": "星辉冠军皮肤",
                            "canonical_name": "星辉冠军皮肤",
                            "type": "skin",
                            "role": "affected",
                        }
                    ],
                    "assertion": "asserted",
                    "temporal": {"event_date": "2026-11-10"},
                    "membership_role": "component",
                },
                {
                    "topic": "esports",
                    "subtopic": "match_result",
                    "identity_entities": [
                        {
                            "name": "全球邀请赛",
                            "canonical_name": "全球邀请赛",
                            "type": "tournament",
                            "role": "context",
                        }
                    ],
                    "assertion": "asserted",
                    "temporal": {"event_date": "2025-11-10"},
                    "membership_role": "component",
                },
            ],
        }

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(proposal, raw_item)

        assert guarded["topic"] == "skin"
        assert guarded["subtopic"] == "skin_release"
        assert len(guarded["event_mentions"]) == 1
        assert guarded["event_mentions"][0]["topic"] == "skin"
        assert guarded["event_mentions"][0]["subtopic"] == "skin_release"
        identity = guarded["event_mentions"][0]["identity_entities"]
        assert identity == [
            {
                "name": "星辉冠军皮肤",
                "canonical_name": "星辉冠军皮肤",
                "type": "skin",
                "role": "core",
            }
        ]


def test_roundup_keeps_independent_skin_release_mentions() -> None:
    with _session() as db:
        raw_item = _raw_item(
            db,
            title="本月外观发布汇总",
            text="两个独立主题系列将在不同日期发布。",
            official=True,
        )
        mentions = [
            {
                "topic": "skin",
                "subtopic": "skin_release",
                "identity_entities": [],
                "membership_role": "primary",
            },
            {
                "topic": "skin",
                "subtopic": "skin_release",
                "identity_entities": [],
                "membership_role": "component",
            },
        ]

        guarded = reviewed_pipeline._apply_classification_evidence_guardrails(
            {
                "topic": "skin",
                "subtopic": "skin_release",
                "content_form": "roundup",
                "event_mentions": mentions,
            },
            raw_item,
        )

        assert guarded["event_mentions"] == mentions
