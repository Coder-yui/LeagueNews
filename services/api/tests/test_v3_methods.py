import asyncio
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.methods import (
    FeaturedCandidate,
    ImportanceScoringInput,
    MessageAnalysisInput,
    MethodAssembly,
    MethodAssemblyConfig,
)
from app.services.daily_reports import DailyReportCandidate
from app.services.llm import MessageContentAnalysisResult


def test_method_assembly_switches_implementation_and_records_actual_configuration() -> None:
    async def alternate(*, client, payload, selection):
        del client
        assert payload["title"] == "输入标题"
        assert selection.prompt_ref == "prompt:test-message-analysis"
        assert selection.model_parameters == {"temperature": 0.2}
        return MessageContentAnalysisResult(
            title="替代方法标题",
            summary="替代方法摘要",
            products=["lol_pc"],
            content_form="original",
        )

    assembly = MethodAssembly(
        MethodAssemblyConfig(
            message_analysis="alternate",
            prompt_refs={"message_analysis": "prompt:test-message-analysis"},
            model_parameters={"message_analysis": {"temperature": 0.2}},
            strategy_parameters={"message_analysis": {"summary_style": "short"}},
        ),
        implementations={"message_analysis": {"alternate": alternate}},
    )
    result = asyncio.run(
        assembly.invoke(
            "message_analysis",
            client=object(),
            payload={
                "title": "输入标题",
                "content": "输入正文",
                "evidence_structure": {},
                "source_context": {},
                "knowledge_rules": [],
            },
        )
    )

    assert result.title == "替代方法标题"
    assert assembly.calls[0].model_dump(mode="json") == {
        "method": "message_analysis",
        "implementation": "alternate",
        "prompt_ref": "prompt:test-message-analysis",
        "model_parameters": {"temperature": 0.2},
        "strategy_parameters": {"summary_style": "short"},
    }


def test_method_inputs_are_serializable_and_frozen() -> None:
    value = MessageAnalysisInput(title="标题", content="正文")
    assert value.model_dump(mode="json")["title"] == "标题"
    with pytest.raises(ValidationError):
        value.title = "不能修改"

    importance = ImportanceScoringInput(content_form="original")
    assert importance.model_dump(mode="json")["content_form"] == "original"


def test_featured_and_daily_methods_return_plans_without_database() -> None:
    assembly = MethodAssembly()
    featured = assembly.select_featured(
        [
            FeaturedCandidate(
                normalized_item_id=1,
                importance_score=0.8,
                content_form="original",
            ),
            FeaturedCandidate(
                normalized_item_id=2,
                importance_score=0.8,
                content_form="repost",
            ),
        ]
    )
    assert featured.selected_item_ids == (1,)

    daily = assembly.plan_daily_report(
        [
            DailyReportCandidate(
                message_id=1,
                importance_score=0.7,
                published_at=datetime(2026, 9, 6, 1, tzinfo=UTC),
                content_form="original",
                products=("lol_pc",),
            )
        ]
    )
    assert [value["message_id"] for value in daily.sections["lolpc"]] == [1]
    assert [call.method for call in assembly.calls] == [
        "featured_selection",
        "daily_report",
    ]
