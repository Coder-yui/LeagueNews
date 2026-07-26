import asyncio
import json
from types import SimpleNamespace

import pytest

from app.core.config import settings
from app.services.llm import (
    AnalysisResult,
    LLMAnalysisError,
    LLMClient,
    LLMConfigurationError,
    PatchPreviewExtraction,
    TranslationResult,
)
from app.workflows.translate_item import build_translation, detect_language


def test_missing_api_key_raises_configuration_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "openai_api_key", "")
    client = LLMClient()

    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY"):
        asyncio.run(client.analyze(title="测试", content="测试内容"))


def test_analysis_result_rejects_missing_fields() -> None:
    with pytest.raises(ValueError):
        AnalysisResult.model_validate({"title": "只有标题"})


def test_analysis_result_accepts_complete_result() -> None:
    result = AnalysisResult.model_validate(
        {
            "title": "版本更新预告",
            "summary": "官方公布调整方向。",
            "category": "版本更新",
            "entities": [{"name": "26.14", "type": "patch"}],
            "importance_score": 0.8,
            "credibility": "official",
            "credibility_score": 0.98,
            "credibility_evidence": ["来自官方账号"],
        }
    )
    assert result.importance_score == 0.8
    assert result.credibility_score == 0.98


def test_analysis_result_rejects_more_than_five_entities() -> None:
    with pytest.raises(ValueError, match="too_long"):
        AnalysisResult.model_validate(
            {
                "title": "版本更新预告",
                "summary": "官方公布调整方向。",
                "category": "版本更新",
                "entities": [
                    {"name": f"实体{index}", "type": "champion"}
                    for index in range(6)
                ],
                "importance_score": 0.8,
                "credibility": "official",
                "credibility_score": 0.98,
                "credibility_evidence": ["来自官方账号"],
            }
        )


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
                "translated_summary": "版本调整摘要",
                "translated_entities": [{"name": "亚托克斯", "type": "champion"}],
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
            canonical_title="版本预览",
            summary="版本调整摘要",
            entities=[{"name": "Aatrox", "type": "champion"}],
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
            knowledge_rules=[],
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


def test_translation_retries_when_structured_target_keeps_english_name() -> None:
    def response(target: str) -> str:
        return json.dumps(
            {
                "translated_title": "26.13版本完整预览",
                "translated_blocks": [],
                "translated_summary": "版本调整预览。",
                "translated_entities": [],
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
            summary="Patch preview.",
            entities=[],
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
            "translated_summary": "版本预览。",
            "translated_entities": [],
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
            summary="Patch preview.",
            entities=[],
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
                knowledge_rules=[],
            )
        )

    assert len(completions.calls) == 2
