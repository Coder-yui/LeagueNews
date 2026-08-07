import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.schemas.workflow import EventMentionTemporalDraft
from app.services.llm import (
    ClassificationResult,
    FactClaimDraft,
    LLMAnalysisError,
    LLMClient,
    LLMConfigurationError,
    PatchPreviewExtraction,
    TranslationResult,
    execution_metadata,
)
from app.workflows.translate_item import build_translation, detect_language


@pytest.mark.parametrize(
    ("source", "expected"),
    [("yearly", "annual"), ("fortnightly", "biweekly")],
)
def test_recurrence_window_aliases_are_normalized(
    source: str, expected: str
) -> None:
    temporal = EventMentionTemporalDraft.model_validate(
        {
            "is_recurring": True,
            "recurrence_window": source,
            "certainty": "confirmed",
            "event_date": "2026-06-01",
        }
    )

    assert temporal.recurrence_window == expected


@pytest.mark.parametrize("source", ["changes", "modifies", "updates"])
def test_generic_change_predicates_normalize_to_adjusts(source: str) -> None:
    claim = FactClaimDraft.model_validate(
        {
            "subject": {"name": "符文系统", "type": "system"},
            "predicate": source,
            "object": {"summary": "数值调整"},
            "temporal_role": "event",
        }
    )

    assert claim.predicate == "adjusts"


def test_missing_api_key_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    client = LLMClient()

    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        asyncio.run(
            client.score_importance(
                content="测试内容",
                extracted_facts={"title": "测试"},
                classification={"topic": "other"},
                source_context={"source_name": "测试来源"},
            )
        )


def test_classification_uses_registered_dual_axis_prompt() -> None:
    response = json.dumps(
        {
            "source_kind": "attributed_report",
            "information_stage": "rumor",
            "content_form": "original",
            "topic": "roster",
            "subtopic": "roster_move",
            "secondary_topics": ["esports"],
            "entity_roles": [
                {"name": "WBG", "type": "team", "role": "core"}
            ],
            "temporal": {
                "is_recurring": False,
                "recurrence_window": None,
                "certainty": "speculative",
                "event_date": None,
            },
        }
    )
    client, completions = _client_with_responses([response])

    result = asyncio.run(
        client.classify(
            content="WBG正在考虑新的打野，最后以官宣为准。",
            extracted_facts={
                "title": "WBG打野传闻",
                "summary": "WBG正在考虑新打野。",
                "entities": [{"name": "WBG", "type": "team"}],
            },
            source_context={"source_name": "_尧阿尧y_"},
        )
    )

    assert isinstance(result, ClassificationResult)
    assert result.source_kind == "attributed_report"
    assert result.information_stage == "rumor"
    assert result.topic == "roster"
    metadata = execution_metadata(result)
    assert metadata["prompt_name"] == "classification"
    assert metadata["prompt_version"] == "v6-cosmetic-releases"
    messages = completions.calls[0]["messages"]
    assert isinstance(messages, list)
    assert "source_kind 只表示事实由谁直接声称" in messages[0]["content"]
    assert "赛程不得用发布日期代替比赛日期" in messages[0]["content"]
    assert "同一赛程列出多场比赛时，每场各一项" in messages[0]["content"]
    assert "现已可领取" in messages[0]["content"]
    assert "新皮肤、单独报道的" in messages[0]["content"]
    assert "国服新增的付费臻彩" in messages[0]["content"]


def test_classification_discards_unknown_optional_secondary_topics() -> None:
    result = ClassificationResult.model_validate(
        {
            "source_kind": "data_mined",
            "information_stage": "preview",
            "content_form": "original",
            "topic": "patch",
            "subtopic": "pbe_change",
            "secondary_topics": ["champion", "balance_change", "champion"],
            "entity_roles": [],
            "temporal": {
                "is_recurring": False,
                "recurrence_window": None,
                "certainty": "likely",
                "event_date": None,
            },
        }
    )

    assert result.secondary_topics == ["champion"]


def test_language_detection() -> None:
    assert detect_language("Patch preview and balance changes") == "en"
    assert detect_language("版本更新与英雄平衡调整") == "zh-CN"


def test_chinese_post_still_translates_english_structured_patch_data(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def fake_translate(_self, **payload):
        captured.update(payload)
        return TranslationResult.model_validate(
                {
                    "translated_title": "版本预览",
                    "translated_blocks": [{"index": 0, "text": "正文"}],
                    "translated_media_extractions": [
                    {
                        "extraction_id": 9,
                        "translated_data": {
                            "sections": [{"entries": [{"target": "亚托克斯"}]}]
                        },
                    }
                ],
            }
        )

    monkeypatch.setattr(LLMClient, "translate", fake_translate)
    raw_item = SimpleNamespace(
        language="zh-CN",
        display_title="版本预览",
        content_blocks=[{"type": "paragraph", "text": "正文"}],
    )
    extraction = SimpleNamespace(
        id=9,
        structured_data={
            "sections": [
                {
                    "entries": [
                        {"target": "Aatrox", "changes": ["Health 100 -> 120"]}
                    ]
                }
            ]
        },
    )

    result = asyncio.run(
        build_translation(
            raw_item,
            media_extractions=[extraction],
            rules=["未实装改动使用将来时。"],
        )
    )

    assert result.translation_status == "translated"
    assert captured["media_extractions"][0]["extraction_id"] == 9
    assert captured["knowledge_rules"] == ["未实装改动使用将来时。"]
    assert result.translated_media_extractions[0]["translated_data"]["sections"][0][
        "entries"
    ][0]["target"] == "亚托克斯"


def test_long_article_translation_uses_large_contextual_chunks(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    async def fake_translate(_self, **payload):
        calls.append(payload)
        context = payload["document_context"]
        preferred_title = context.get("preferred_translated_title")
        return TranslationResult.model_validate(
            {
                "translated_title": preferred_title or "26.12版本公告",
                "translated_blocks": [
                    {
                        "index": block["index"],
                        "text": f"译文 {block['index']}",
                    }
                    for block in payload["text_blocks"]
                ],
                "translated_media_extractions": [],
            }
        )

    monkeypatch.setattr(LLMClient, "translate", fake_translate)
    raw_item = SimpleNamespace(
        language="en",
        display_title="Patch 26.12 Notes",
        content_blocks=[
            {"type": "heading", "level": 2, "text": "Champions"},
            {"type": "paragraph", "text": "first " + ("a" * 5_000)},
            {"type": "paragraph", "text": "second " + ("b" * 5_000)},
            {"type": "paragraph", "text": "third " + ("c" * 5_000)},
        ],
    )

    result = asyncio.run(build_translation(raw_item, media_extractions=[]))

    assert len(calls) == 2
    assert [block["index"] for block in calls[0]["text_blocks"]] == [0, 1, 2]
    assert [block["index"] for block in calls[1]["text_blocks"]] == [3]
    assert calls[0]["document_context"]["document_outline"] == "Champions"
    assert calls[0]["document_context"]["total_chunks"] == 2
    assert calls[1]["document_context"]["preferred_translated_title"] == "26.12版本公告"
    assert calls[1]["document_context"]["previous_translation_tail"]
    assert result.translated_title == "26.12版本公告"
    assert [block["text"] for block in result.translated_content_blocks] == [
        "译文 0",
        "译文 1",
        "译文 2",
        "译文 3",
    ]


def test_patch_preview_accepts_adjustment_sections() -> None:
    result = PatchPreviewExtraction.model_validate(
        {
            "document_type": "patch_preview",
            "preview_kind": "preview",
            "patch": "26.14",
            "title": "26.14版本预览",
            "sections": [
                {
                    "section_type": "champion_adjustment",
                    "label": "英雄调整",
                    "entries": [],
                }
            ],
            "warnings": [],
        }
    )
    assert result.sections[0].section_type == "champion_adjustment"


def test_full_patch_preview_accepts_entries_without_changes() -> None:
    result = PatchPreviewExtraction.model_validate(
        {
            "document_type": "patch_preview",
            "preview_kind": "full_preview",
            "patch": "26.15",
            "title": "26.15版本完整预览",
            "sections": [
                {
                    "section_type": "champion_buff",
                    "label": "英雄增强",
                    "entries": [
                        {
                            "target": "Azir",
                            "target_type": "champion",
                            "changes": [],
                        }
                    ],
                }
            ],
            "warnings": [],
        }
    )

    assert result.sections[0].entries[0].target == "Azir"
    assert result.sections[0].entries[0].changes == []


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


def _client_with_responses(responses: list[str]) -> tuple[LLMClient, _FakeCompletions]:
    client = LLMClient()
    completions = _FakeCompletions(responses)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client, completions


def test_importance_prompt_uses_editorial_policy_contract() -> None:
    response = json.dumps(
        {
            "scale": "standard",
            "audience_region": "global",
            "competition_region": "none",
            "prominence": "normal",
            "skin_tier": "none",
            "is_bulk_update": False,
            "evidence": ["消息公布了活动规则。"],
        }
    )
    client, completions = _client_with_responses([response])

    asyncio.run(
        client.score_importance(
            content="输入含限时兑换码。",
            extracted_facts={"title": "限时活动"},
            classification={"topic": "activity"},
            source_context={"source_name": "英雄联盟"},
        )
    )

    messages = completions.calls[0]["messages"]
    assert isinstance(messages, list)
    prompt = messages[0]["content"]
    assert "不评估行动紧迫性" in prompt
    assert "international_only" in prompt
    assert "不输出最终分数" in prompt
    assert "当前输入没有历史对照" in prompt
    assert "evidence：1-6条消息中的具体文本依据，不得编造" in prompt
    assert '"臻彩"不是"至臻皮肤"' in prompt


def test_claim_generation_uses_atomic_timeline_contract() -> None:
    response = json.dumps(
        {
            "fact_claims": [
                {
                    "subject": {"name": "Beichuan", "type": "player"},
                    "predicate": "considered_for",
                    "object": {"team": "WBG", "position": "jungle"},
                    "temporal_role": "prediction",
                    "supersedes_hint": None,
                }
            ],
            "attribution": {
                "claimed_by": "爆料人",
                "stance": "asserts",
                "certainty": "speculative",
            },
        }
    )
    client, completions = _client_with_responses([response])

    result = asyncio.run(
        client.generate_claims(
            content="爆料人称 WBG 正在考虑 Beichuan 作为打野候选。",
            extracted_facts={"title": "WBG 打野候选"},
            classification={
                "source_kind": "attributed_report",
                "information_stage": "rumor",
                "topic": "roster",
            },
            source_context={"source_name": "爆料人"},
        )
    )

    assert result.fact_claims[0].predicate == "considered_for"
    assert result.fact_claims[0].subject["name"] == "Beichuan"
    assert result.attribution.claimed_by == "爆料人"
    metadata = execution_metadata(result)
    assert metadata["prompt_name"] == "claim-generation"
    assert metadata["prompt_version"] == "v2-timeline"
    messages = completions.calls[0]["messages"]
    assert isinstance(messages, list)
    prompt = messages[0]["content"]
    assert "subject 是事实主体本身" in prompt
    assert "considered_for 用于\"考虑中\"的候选" in prompt
    assert "supersedes_hint 帮助下游" in prompt


def test_event_create_with_candidates_requires_explicit_rejections() -> None:
    invalid_create = json.dumps(
        {
            "memberships": [
                {
                    "target": "new",
                    "event_kind": "cosmetic_release",
                    "aggregation_strategy": "release",
                    "product_scope": "lol_pc",
                    "aggregation_key": "release:lol_pc:retro-bundle",
                    "membership_role": "primary",
                    "evidence_stance": "supports",
                    "update_kind": "new_fact",
                    "lifecycle_status": "developing",
                    "timeline_note": "测试服出现礼包封面",
                }
            ],
            "candidate_rejections": [],
        }
    )
    valid_update = json.dumps(
        {
            "memberships": [
                {
                    "target": "existing:8",
                    "event_kind": "gameplay_release",
                    "aggregation_strategy": "release",
                    "product_scope": "lol_pc",
                    "aggregation_key": "gameplay:lol_pc:经典模式",
                    "membership_role": "component",
                    "evidence_stance": "context",
                    "update_kind": "context",
                    "lifecycle_status": "developing",
                    "timeline_note": "礼包封面提供经典模式旁证",
                }
            ],
            "candidate_rejections": [],
        }
    )
    client, completions = _client_with_responses([invalid_create, valid_update])

    result = asyncio.run(
        client.propose_event(
            item={
                "title": "测试服怀旧玩法礼包封面",
                "summary": "测试服出现相关礼包封面。",
                "product_scope": "lol_pc",
                "information_stage": "preview",
                "event_assertion": "context_only",
                "event_routes": [
                    {
                        "event_kind": "gameplay_release",
                        "aggregation_strategy": "release",
                        "product_scope": "lol_pc",
                        "aggregation_key": "gameplay:lol_pc:经典模式",
                    }
                ],
            },
            candidates=[
                {
                    "event_id": 8,
                    "aggregation_key": "gameplay:lol_pc:经典模式",
                    "title": "经典玩法正式公布",
                    "summary": "拳头公布经典玩法。",
                    "core_entities": ["classic mode"],
                    "match_level": "broad",
                    "event_kind": "gameplay_release",
                    "aggregation_strategy": "release",
                    "product_scope": "lol_pc",
                }
            ],
            knowledge_rules=[],
        )
    )

    assert result.memberships[0].target == "existing:8"
    assert result.memberships[0].update_kind == "context"
    assert result.memberships[0].evidence_stance == "context"
    metadata = execution_metadata(result)
    assert metadata["prompt_name"] == "event-decision"
    assert metadata["prompt_version"] == "v7-semantic-candidate-binding"
    assert metadata["json_schema_version"] == "EventDecisionDraft:v1"
    assert str(metadata["prompt_hash"]).startswith("sha256:")
    assert len(completions.calls) == 1


def test_event_decision_can_semantically_bind_compatible_alias_candidate() -> None:
    semantic_update = json.dumps(
        {
            "memberships": [
                {
                    "target": "existing:8",
                    "event_kind": "gameplay_release",
                    "aggregation_strategy": "release",
                    "product_scope": "lol_pc",
                    "aggregation_key": "release:lol_pc:经典模式",
                    "identity_resolution": "semantic_candidate",
                    "identity_rationale": "联盟经典模式与经典模式是同一玩法的全称和简称",
                    "membership_role": "primary",
                    "evidence_stance": "supports",
                    "update_kind": "confirmation",
                    "lifecycle_status": "live",
                    "timeline_note": "国服确认经典模式上线",
                }
            ],
            "candidate_rejections": [],
        }
    )
    client, completions = _client_with_responses([semantic_update])

    result = asyncio.run(
        client.propose_event(
            item={
                "title": "经典模式正式上线",
                "summary": "国服确认经典模式正式上线。",
                "product_scope": "lol_pc",
                "information_stage": "active",
                "event_assertion": "asserted",
                "event_routes": [
                    {
                        "event_kind": "gameplay_release",
                        "aggregation_strategy": "release",
                        "product_scope": "lol_pc",
                        "aggregation_key": "release:lol_pc:经典模式",
                        "creation_policy": "allow",
                        "assertion": "asserted",
                    }
                ],
            },
            candidates=[
                {
                    "event_id": 8,
                    "aggregation_key": "release:lol_pc:联盟经典模式",
                    "title": "联盟经典模式即将上线",
                    "summary": "Riot公布联盟经典模式。",
                    "core_entities": ["联盟经典模式"],
                    "match_level": "strong",
                    "event_kind": "gameplay_release",
                    "aggregation_strategy": "release",
                    "product_scope": "lol_pc",
                    "deterministic_route_key": "release:lol_pc:经典模式",
                }
            ],
            knowledge_rules=[],
        )
    )

    assert result.memberships[0].target == "existing:8"
    assert result.memberships[0].identity_resolution == "semantic_candidate"
    assert result.memberships[0].identity_rationale
    assert len(completions.calls) == 1


def test_event_create_cannot_duplicate_exact_stable_key_candidate() -> None:
    duplicate_create = json.dumps(
        {
            "memberships": [
                {
                    "target": "new",
                    "event_kind": "esports_match",
                    "aggregation_strategy": "calendar_day",
                    "product_scope": "lol_esports",
                    "aggregation_key": "matchday:lpl:2026-07-26",
                    "membership_role": "primary",
                    "evidence_stance": "supports",
                    "update_kind": "new_fact",
                    "lifecycle_status": "scheduled",
                    "timeline_note": "7月26日赛程发布",
                }
            ],
            "candidate_rejections": [
                {"event_id": 21, "reason": "生命周期不同"}
            ],
        }
    )
    valid_update = json.dumps(
        {
            "memberships": [
                {
                    "target": "existing:21",
                    "event_kind": "esports_match",
                    "aggregation_strategy": "calendar_day",
                    "product_scope": "lol_esports",
                    "aggregation_key": "matchday:lpl:2026-07-26",
                    "membership_role": "primary",
                    "evidence_stance": "supports",
                    "update_kind": "new_fact",
                    "lifecycle_status": "scheduled",
                    "timeline_note": "7月26日赛程发布",
                }
            ],
            "candidate_rejections": [],
        }
    )
    client, completions = _client_with_responses(
        [duplicate_create, valid_update]
    )

    result = asyncio.run(
        client.propose_event(
            item={
                "title": "2026LPL第三赛段7月26日赛程预告",
                "summary": "LNG对阵NIP、TT对阵EDG、AL对阵BLG。",
                "product_scope": "lol_esports",
                "information_stage": "preview",
                "event_routes": [
                    {
                        "event_kind": "esports_match",
                        "aggregation_strategy": "calendar_day",
                        "product_scope": "lol_esports",
                        "aggregation_key": "matchday:lpl:2026-07-26",
                    }
                ],
            },
            candidates=[
                {
                    "event_id": 21,
                    "aggregation_key": "matchday:lpl:2026-07-26",
                    "title": "2026LPL第三赛段7月26日赛果",
                    "summary": "当日三场比赛已经结束。",
                    "event_kind": "esports_match",
                    "aggregation_strategy": "calendar_day",
                    "product_scope": "lol_esports",
                    "lifecycle_status": "completed",
                }
            ],
            knowledge_rules=[],
        )
    )

    assert len(completions.calls) == 1
    membership = result.memberships[0]
    assert membership.target == "existing:21"
    assert membership.lifecycle_status == "completed"
    assert membership.update_kind == "context"
    assert membership.evidence_stance == "context"


def test_event_prompt_explains_topic_cluster_lifecycle() -> None:
    response = json.dumps(
        {
            "memberships": [],
            "candidate_rejections": [],
        }
    )
    client, completions = _client_with_responses([response])

    asyncio.run(
        client.propose_event(
            item={"title": "测试", "summary": "测试"},
            candidates=[],
            knowledge_rules=[],
        )
    )

    messages = completions.calls[0]["messages"]
    assert isinstance(messages, list)
    prompt = messages[0]["content"]
    assert "recurring_window/calendar_day" in prompt
    assert "event_routes 是程序完成主题簇归并后生成的允许路由" in prompt
    assert "esports_match 可以是 calendar_day 比赛日或 timeline 重大单场" in prompt


def test_shop_rotation_route_rejects_empty_membership() -> None:
    invalid = json.dumps(
        {
            "memberships": [],
            "candidate_rejections": [],
        }
    )
    valid = json.dumps(
        {
            "memberships": [
                {
                    "target": "new",
                    "event_kind": "commercial_offer",
                    "aggregation_strategy": "recurring_window",
                    "product_scope": "lol_pc",
                    "aggregation_key": "shop_rotation:lol_pc:2026-W30",
                    "membership_role": "primary",
                    "evidence_stance": "supports",
                    "update_kind": "new_fact",
                    "lifecycle_status": "live",
                    "timeline_note": "本周轮换内容公布",
                }
            ],
            "candidate_rejections": [],
        }
    )
    client, completions = _client_with_responses([invalid, valid])
    result = asyncio.run(
        client.propose_event(
            item={
                "title": "神话商城每周轮换",
                "product_scope": "lol_pc",
                "event_routes": [
                    {
                        "event_kind": "commercial_offer",
                        "aggregation_strategy": "recurring_window",
                        "product_scope": "lol_pc",
                        "aggregation_key": "shop_rotation:lol_pc:2026-W30",
                    }
                ],
            },
            candidates=[],
            knowledge_rules=[],
        )
    )

    assert result.memberships[0].event_kind == "commercial_offer"
    assert result.memberships[0].aggregation_strategy == "recurring_window"
    assert len(completions.calls) == 1


def test_shop_rotation_route_retries_wrong_controlled_event_axes() -> None:
    invalid = json.dumps(
        {
            "memberships": [
                {
                    "target": "new",
                    "event_kind": "other",
                    "aggregation_strategy": "singleton",
                    "product_scope": "lol_pc",
                    "aggregation_key": "shop_rotation:lol_pc:2026-W30",
                    "membership_role": "primary",
                    "evidence_stance": "supports",
                    "update_kind": "new_fact",
                    "lifecycle_status": "live",
                    "timeline_note": "本周轮换内容公布",
                }
            ],
            "candidate_rejections": [],
        }
    )
    valid = json.dumps(
        {
            "memberships": [
                {
                    "target": "new",
                    "event_kind": "commercial_offer",
                    "aggregation_strategy": "recurring_window",
                    "product_scope": "lol_pc",
                    "aggregation_key": "shop_rotation:lol_pc:2026-W30",
                    "membership_role": "primary",
                    "evidence_stance": "supports",
                    "update_kind": "new_fact",
                    "lifecycle_status": "live",
                    "timeline_note": "本周轮换内容公布",
                }
            ],
            "candidate_rejections": [],
        }
    )
    client, completions = _client_with_responses([invalid, valid])

    result = asyncio.run(
        client.propose_event(
            item={
                "title": "神话商城每周轮换",
                "product_scope": "lol_pc",
                "event_routes": [
                    {
                        "event_kind": "commercial_offer",
                        "aggregation_strategy": "recurring_window",
                        "product_scope": "lol_pc",
                        "aggregation_key": "shop_rotation:lol_pc:2026-W30",
                    }
                ],
            },
            candidates=[],
            knowledge_rules=[],
        )
    )

    assert result.memberships[0].event_kind == "commercial_offer"
    assert result.memberships[0].aggregation_strategy == "recurring_window"
    assert len(completions.calls) == 1


def test_missing_program_route_requires_not_event() -> None:
    invalid = json.dumps(
        {
            "memberships": [
                {
                    "target": "new",
                    "event_kind": "commercial_offer",
                    "aggregation_strategy": "recurring_window",
                    "product_scope": "lol_pc",
                    "aggregation_key": "shop_rotation:lol_pc:2026-W30",
                    "membership_role": "primary",
                    "evidence_stance": "supports",
                    "update_kind": "new_fact",
                    "lifecycle_status": "live",
                    "timeline_note": "国际服轮换",
                }
            ],
            "candidate_rejections": [],
        }
    )
    valid = json.dumps(
        {
            "memberships": [],
            "candidate_rejections": [],
        }
    )
    client, completions = _client_with_responses([invalid, valid])

    result = asyncio.run(
        client.propose_event(
            item={
                "title": "Mythic Shop weekly rotation",
                "product_scope": "lol_pc",
                "event_routes": [],
            },
            candidates=[],
            knowledge_rules=[],
        )
    )

    assert result.memberships == []
    assert len(completions.calls) == 2


def test_shop_rotation_exact_key_must_update_existing_event() -> None:
    response = json.dumps(
        {
            "memberships": [
                {
                    "target": "existing:12",
                    "event_kind": "commercial_offer",
                    "aggregation_strategy": "recurring_window",
                    "product_scope": "lol_pc",
                    "aggregation_key": "shop_rotation:lol_pc:2026-W30",
                    "membership_role": "primary",
                    "evidence_stance": "supports",
                    "update_kind": "new_fact",
                    "lifecycle_status": "live",
                    "timeline_note": "每日轮换补充",
                }
            ],
            "candidate_rejections": [],
        }
    )
    client, completions = _client_with_responses([response])

    result = asyncio.run(
        client.propose_event(
            item={
                "title": "神话商城每日轮换",
                "product_scope": "lol_pc",
                "information_stage": "update",
                "event_routes": [
                    {
                        "event_kind": "commercial_offer",
                        "aggregation_strategy": "recurring_window",
                        "product_scope": "lol_pc",
                        "aggregation_key": "shop_rotation:lol_pc:2026-W30",
                    }
                ],
            },
            candidates=[
                {
                    "event_id": 12,
                    "title": "本周国服神话商城轮换",
                    "aggregation_key": "shop_rotation:lol_pc:2026-W30",
                    "event_kind": "commercial_offer",
                    "aggregation_strategy": "recurring_window",
                    "product_scope": "lol_pc",
                }
            ],
            knowledge_rules=[],
        )
    )

    assert result.memberships[0].target == "existing:12"
    assert result.memberships[0].update_kind == "new_fact"
    assert result.memberships[0].evidence_stance == "supports"
    assert len(completions.calls) == 1


def test_schema_business_error_is_returned_to_model_and_retried() -> None:
    invalid = json.dumps(
        {
            "product_scope": "2xko",
            "is_lol_relevant": True,
            "confidence": 0.9,
            "reason": "错误地保留",
        }
    )
    valid = json.dumps(
        {
            "product_scope": "2xko",
            "is_lol_relevant": False,
            "confidence": 0.99,
            "reason": "项目明确排除 2XKO",
        }
    )
    client, completions = _client_with_responses([invalid, valid])

    result = asyncio.run(
        client.judge_relevance(
            title="2XKO update",
            content="A new fighter joins 2XKO.",
            source_context={},
        )
    )

    assert result.is_lol_relevant is False
    assert len(completions.calls) == 2
    retry_messages = completions.calls[1]["messages"]
    assert isinstance(retry_messages, list)
    assert "未通过校验" in retry_messages[-1]["content"]


def test_knowledge_organization_retries_when_source_ids_are_not_fully_covered() -> None:
    incomplete = json.dumps(
        {
            "rules": [
                {
                    "knowledge_type": "analysis",
                    "scope": "global",
                    "rule_text": "精简规则",
                    "source_rule_ids": [1],
                }
            ]
        }
    )
    complete = json.dumps(
        {
            "rules": [
                {
                    "knowledge_type": "analysis",
                    "scope": "global",
                    "rule_text": "合并后的精简规则",
                    "source_rule_ids": [1, 2],
                }
            ]
        }
    )
    client, completions = _client_with_responses([incomplete, complete])

    result = asyncio.run(
        client.organize_knowledge(
            rules=[
                {
                    "id": 1,
                    "knowledge_type": "analysis",
                    "scope": "global",
                    "rule_text": "第一条",
                },
                {
                    "id": 2,
                    "knowledge_type": "analysis",
                    "scope": "global",
                    "rule_text": "第二条",
                },
            ]
        )
    )

    assert result.rules[0].source_rule_ids == [1, 2]
    assert len(completions.calls) == 2
    first_messages = completions.calls[0]["messages"]
    assert isinstance(first_messages, list)
    prompt = first_messages[0]["content"]
    assert "必须删除文章标题、具体日期、消息编号、链接" in prompt
    assert "跨文章复用的判断原则" in prompt
    assert "不得把文章中的偶然事实、实体或结论泛化成新规则" in prompt


def test_translation_retries_when_structured_target_keeps_english_name() -> None:
    def response(target: str) -> str:
        return json.dumps(
            {
                "translated_title": "26.13版本完整预览",
                "translated_blocks": [],
                "translated_media_extractions": [
                    {
                        "extraction_id": 9,
                        "translated_data": {
                            "sections": [
                                {
                                    "entries": [
                                        {
                                            "target": target,
                                            "target_type": "champion",
                                            "changes": ["标记伤害提高。"],
                                        }
                                    ]
                                }
                            ]
                        },
                    }
                ],
            },
            ensure_ascii=False,
        )

    client, completions = _client_with_responses(
        [response("Aphelios"), response("厄斐琉斯")]
    )

    result = asyncio.run(
        client.translate(
            title="Patch 26.13 Full Preview",
            text_blocks=[],
            source_language="en",
            media_extractions=[
                {
                    "extraction_id": 9,
                    "structured_data": {
                        "sections": [
                            {
                                "entries": [
                                    {
                                        "target": "Aphelios",
                                        "target_type": "champion",
                                        "changes": ["Calibrum mark damage increased."],
                                    }
                                ]
                            }
                        ]
                    },
                }
            ],
        )
    )

    translated = result.translated_media_extractions[0].translated_data
    assert translated["sections"][0]["entries"][0]["target"] == "厄斐琉斯"
    assert len(completions.calls) == 2
    initial_messages = completions.calls[0]["messages"]
    assert "必须忠实翻译 target 当前使用的称谓层级" in initial_messages[0]["content"]
    assert "若原文 target 本身是该称号，则应译为“残月之肃”" in initial_messages[0]["content"]
    retry_messages = completions.calls[1]["messages"]
    assert "target 未翻译" in retry_messages[-1]["content"]


def test_translation_allows_structured_change_lines_to_split() -> None:
    response = json.dumps(
        {
            "translated_title": "版本预览",
            "translated_blocks": [],
            "translated_media_extractions": [
                {
                    "extraction_id": 4,
                    "translated_data": {
                        "sections": [
                            {
                                "entries": [
                                    {
                                        "target": "厄斐琉斯",
                                        "target_type": "champion",
                                        "changes": ["伤害提高。", "额外说明。"],
                                    }
                                ]
                            }
                        ]
                    },
                }
            ],
        },
        ensure_ascii=False,
    )

    client, completions = _client_with_responses([response])

    result = asyncio.run(
        client.translate(
            title="Patch preview",
            text_blocks=[],
            source_language="en",
            media_extractions=[
                {
                    "extraction_id": 4,
                    "structured_data": {
                        "sections": [
                            {
                                "entries": [
                                    {
                                        "target": "Aphelios",
                                        "target_type": "champion",
                                        "changes": ["Damage increased."],
                                    }
                                ]
                            }
                        ]
                    },
                }
            ],
        )
    )

    translated = result.translated_media_extractions[0].translated_data
    assert len(translated["sections"][0]["entries"][0]["changes"]) == 2
    assert len(completions.calls) == 1


def test_two_invalid_schema_responses_raise_unified_error() -> None:
    client, completions = _client_with_responses(["{}", '{"product_scope":"lol_pc"}'])

    with pytest.raises(
        LLMAnalysisError,
        match="连续两次未通过结构或业务校验",
    ):
        asyncio.run(
            client.judge_relevance(
                title="Patch preview",
                content="Changes",
                source_context={},
            )
        )

    assert len(completions.calls) == 2
