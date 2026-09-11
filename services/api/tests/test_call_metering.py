"""All prices and usage here are synthetic; no remote requests are made."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel

from app.services.call_metering import (
    MemoryRecorder,
    SQLiteAttemptRecorder,
    measurement_scope,
    summarize_attempts,
    TokenPrice,
)
from app.services.llm import LLMClient, LLMAnalysisError
from app.prompts.registry import RELEVANCE_OPERATION


class Answer(BaseModel):
    value: int


def response(content, usage=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=usage,
        model="test",
        id="response",
        _request_id="request",
    )


async def complete(responses):
    client = LLMClient.__new__(LLMClient)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(side_effect=responses)))
    )
    return await client._validated_json_completion(
        prompt="test", payload={}, max_tokens=10, schema=Answer, operation=RELEVANCE_OPERATION
    )


def test_invalid_then_success_preserves_both_usage():
    async def run_test():
        recorder = MemoryRecorder()
        with measurement_scope(recorder, case_id="one") as scope:
            from app.core.config import settings
            from urllib.parse import urlsplit

            scope.prices[(urlsplit(settings.openai_base_url).hostname, "test")] = TokenPrice(
                "synthetic-v1", 1, 2
            )
            result = await complete(
                [
                    response("bad", {"prompt_tokens": 10, "completion_tokens": 2}),
                    response('{"value":1}', {"prompt_tokens": 20, "completion_tokens": 3}),
                ]
            )
        assert result.value == 1
        attempts = recorder.attempts()
        assert [row["status"] for row in attempts] == ["invalid", "succeeded"]
        assert summarize_attempts(attempts)["token_count"] == 35
        assert summarize_attempts(attempts)["cost_usd"] == pytest.approx(40 / 1_000_000)
        assert len({row["logical_call_id"] for row in attempts}) == 1
        assert len({row["attempt_id"] for row in attempts}) == 2
        recorder.record(attempts[0])
        assert len(recorder.attempts()) == 2

    asyncio.run(run_test())


@pytest.mark.parametrize("ending", [response("bad"), TimeoutError(), asyncio.CancelledError()])
def test_final_failure_retains_known_consumption(ending):
    async def run_test():
        recorder = MemoryRecorder()
        with measurement_scope(recorder):
            with pytest.raises((LLMAnalysisError, TimeoutError, asyncio.CancelledError)):
                await complete(
                    [response("bad", {"prompt_tokens": 7, "completion_tokens": 3}), ending]
                )
        summary = summarize_attempts(recorder.attempts())
        assert summary["application_attempt_count"] == 2
        assert summary["known_token_count"] == 10
        assert summary["token_count"] is None
        assert summary["cost_usd"] is None

    asyncio.run(run_test())


def test_concurrent_scopes_are_isolated():
    async def run_test():
        async def run(key):
            recorder = MemoryRecorder()
            with measurement_scope(recorder, case_id=key):
                await asyncio.sleep(0)
                await complete([response('{"value":1}')])
            return recorder.attempts()

        left, right = await asyncio.gather(run("left"), run("right"))
        assert {row["case_id"] for row in left} == {"left"}
        assert {row["case_id"] for row in right} == {"right"}

    asyncio.run(run_test())


def test_durable_delivery_deduplication(tmp_path):
    import sqlite3

    path = tmp_path / "ledger.sqlite3"
    recorder = SQLiteAttemptRecorder(path)
    for row in [{"attempt_id": "1", "status": "failed"}] * 2 + [
        {"attempt_id": "2", "status": "failed"}
    ]:
        recorder.record(row)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM call_attempts").fetchone()[0] == 2
