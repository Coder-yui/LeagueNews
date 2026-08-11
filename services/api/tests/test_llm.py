import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.llm import (
    LLMAnalysisError,
    LLMClient,
    LLMConfigurationError,
    MessageClassificationImportanceResult,
    MessageContentAnalysisResult,
    PatchPreviewExtraction,
    TranslationResult,
    execution_metadata,
)
from app.workflows.translate_item import build_translation, detect_language


def test_missing_api_key_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    client = LLMClient()

    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        asyncio.run(
            client.classify_and_score_importance(
                content="测试内容",
                extracted_facts={"title": "测试"},
                products=["lol_pc"],
                content_form="original",
                source_context={"source_name": "测试来源", "is_official_source": True},
            )
        )


def test_message_content_analysis_only_receives_first_stage_catalog() -> None:
    response = json.dumps(
        {
            "title": "WBG 打野传闻",
            "summary": "社区消息称 WBG 正在考虑新的打野选手。",
            "entities": [{"name": "WBG", "type": "team", "canonical_name": "WBG"}],
            "products": ["lol_esports"],
            "content_form": "original",
            "classification_version": "message-taxonomy-v3",
        }
    )
    client, completions = _client_with_responses([response])

    result = asyncio.run(
        client.analyze_message_content(
            title="WBG 打野传闻",
            content="WBG正在考虑新的打野，最后以官宣为准。",
            evidence_structure={"content_block_types": ["paragraph"]},
            source_context={"source_name": "_尧阿尧y_", "is_official_source": False},
        )
    )

    assert isinstance(result, MessageContentAnalysisResult)
    assert result.products == ["lol_esports"]
    metadata = execution_metadata(result)
    assert metadata["prompt_name"] == "message-content-analysis"
    assert metadata["prompt_version"] == "v8-required-summary"
    assert metadata["json_schema_version"] == "MessageContentAnalysisResult:v2"
    messages = completions.calls[0]["messages"]
    assert isinstance(messages, list)
    assert "不判断消息类型、主题、重要性" in messages[0]["content"]
    assert "明确出现具体产品名" in messages[0]["content"]
    assert "生成非空" in messages[0]["content"]
    payload = json.loads(messages[1]["content"])
    assert len(payload["controlled_catalog"]["products"]) == 7
    assert len(payload["controlled_catalog"]["content_forms"]) == 5
    assert "message_types" not in payload["controlled_catalog"]
    assert "topics" not in payload["controlled_catalog"]


def test_media_only_forces_all_semantic_axes_to_unknown() -> None:
    result = MessageContentAnalysisResult.model_validate(
        {
            "title": "无可读正文",
            "summary": "模型不应保留这段摘要",
            "entities": [{"name": "猜测实体", "type": "other"}],
            "products": ["unknown"],
            "content_form": "media_only",
        }
    )

    assert result.summary == ""
    assert result.entities == []


@pytest.mark.parametrize("content_form", ["media_only", "link_only"])
def test_nonsemantic_content_forms_allow_empty_title(content_form: str) -> None:
    result = MessageContentAnalysisResult.model_validate(
        {
            "title": "",
            "summary": "",
            "products": ["unknown"],
            "content_form": content_form,
        }
    )
    assert result.title == ""


def test_original_content_requires_title() -> None:
    with pytest.raises(ValueError, match="可处理消息必须生成标题"):
        MessageContentAnalysisResult.model_validate(
            {
                "title": "",
                "summary": "有摘要",
                "products": ["lol_pc"],
                "content_form": "original",
            }
        )


def test_original_content_requires_summary() -> None:
    with pytest.raises(ValueError, match="可处理消息必须生成摘要"):
        MessageContentAnalysisResult.model_validate(
            {
                "title": "可用标题",
                "summary": "",
                "products": ["lol_pc"],
                "content_form": "repost",
            }
        )


def test_translation_allows_empty_title_only_when_input_title_is_empty() -> None:
    response = json.dumps(
        {
            "translated_title": "",
            "translated_blocks": [],
            "translated_media_extractions": [],
        }
    )
    client, _ = _client_with_responses([response])
    result = asyncio.run(
        client.translate(title="", text_blocks=[], source_language="en")
    )
    assert result.translated_title == ""

    client, _ = _client_with_responses([response, response])
    with pytest.raises(LLMAnalysisError, match="translated_title"):
        asyncio.run(
            client.translate(title="Source title", text_blocks=[], source_language="en")
        )


def test_language_detection() -> None:
    assert detect_language("Patch preview and balance changes") == "en"
    assert detect_language("版本更新与英雄平衡调整") == "zh-CN"


def test_media_without_source_title_keeps_translation_title_empty() -> None:
    raw_item = SimpleNamespace(
        language="unknown",
        display_title="账号名称",
        native_title=None,
        content_blocks=[{"type": "image", "source_url": "https://example.com/a.jpg"}],
    )
    result = asyncio.run(build_translation(raw_item, media_extractions=[]))
    assert result.translated_title == ""
    assert result.translation_status == "not_required"


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
                        "translated_data": {"sections": [{"entries": [{"target": "亚托克斯"}]}]},
                    }
                ],
            }
        )

    monkeypatch.setattr(LLMClient, "translate", fake_translate)
    raw_item = SimpleNamespace(
        language="zh-CN",
        display_title="版本预览",
        native_title="版本预览",
        content_blocks=[{"type": "paragraph", "text": "正文"}],
    )
    extraction = SimpleNamespace(
        id=9,
        structured_data={
            "sections": [{"entries": [{"target": "Aatrox", "changes": ["Health 100 -> 120"]}]}]
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
    assert (
        result.translated_media_extractions[0]["translated_data"]["sections"][0]["entries"][0][
            "target"
        ]
        == "亚托克斯"
    )


def test_long_article_translation_uses_contextual_chunks(monkeypatch) -> None:
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
        native_title="Patch 26.12 Notes",
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
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def _client_with_responses(responses: list[str]) -> tuple[LLMClient, _FakeCompletions]:
    client = LLMClient()
    completions = _FakeCompletions(responses)
    client.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return client, completions


def test_importance_prompt_uses_editorial_policy_contract() -> None:
    response = json.dumps(
        {
            "message_type": "game_announcement",
            "topics": ["community", "activities_rewards", "community"],
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

    result = asyncio.run(
        client.classify_and_score_importance(
            content="输入含限时兑换码。",
            extracted_facts={"title": "限时活动"},
            products=["lol_pc"],
            content_form="original",
            source_context={"source_name": "英雄联盟", "is_official_source": True},
        )
    )

    assert isinstance(result, MessageClassificationImportanceResult)
    assert result.topics == ["activities_rewards", "community"]
    messages = completions.calls[0]["messages"]
    assert isinstance(messages, list)
    prompt = messages[0]["content"]
    assert "不评估行动紧迫性" in prompt
    assert "international_only" in prompt
    assert "不输出最终分数" in prompt
    assert "当前输入没有历史对照" in prompt
    assert "evidence：1-6条消息中的具体文本依据，不得编造" in prompt
    assert '"臻彩"不是"至臻皮肤"' in prompt
    assert "先判断主要传播目的" in prompt
    assert "可独立获取和核验" in prompt
    assert "服务于宣传表达" in prompt
    assert "不能单独作为" in prompt
    assert "message_type 决定消息的信息价值层级" in prompt
    assert "不得再通过 scale、prominence 等字段重复升降档" in prompt
    payload = json.loads(messages[1]["content"])
    candidates = {value["code"] for value in payload["controlled_catalog"]["message_types"]}
    assert "game_announcement" in candidates
    assert "game_leak" not in candidates
    assert payload["known_classification"] == {
        "products": ["lol_pc"],
        "content_form": "original",
    }


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

    client, completions = _client_with_responses([response("Aphelios"), response("厄斐琉斯")])

    result = asyncio.run(
        client.translate(
            title="Patch 26.13 Full Preview",
            text_blocks=[],
            source_language="en",
            document_context={
                "chunk_number": 2,
                "total_chunks": 2,
                "preferred_translated_title": "26.13版本完整预览",
            },
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
    assert "preferred_translated_title 非空" in initial_messages[0]["content"]
    initial_payload = json.loads(initial_messages[1]["content"])
    assert initial_payload["document_context"]["chunk_number"] == 2
    assert initial_payload["document_context"]["preferred_translated_title"] == (
        "26.13版本完整预览"
    )
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
