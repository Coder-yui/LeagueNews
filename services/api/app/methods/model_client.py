"""Small runtime model ports; clients are never part of a serializable config."""

from collections.abc import Callable
from typing import Protocol, TypeVar
from pydantic import BaseModel
from app.methods.contracts import (
    MessageContentAnalysisResult,
    MessageClassificationImportanceResult,
)
from app.schemas.event_aggregation import EventAggregationResult

ResultT = TypeVar("ResultT", bound=BaseModel)


class JsonCompletionClient(Protocol):
    async def _validated_json_completion(
        self,
        *,
        prompt: str,
        payload: dict[str, object],
        max_tokens: int,
        schema: type[ResultT],
        operation: str,
        business_validator: Callable[[ResultT], str | None] | None = None,
        final_fallback: Callable[[dict[str, object]], ResultT | None] | None = None,
    ) -> ResultT: ...


class DomainModelClient(Protocol):
    async def analyze_message_content(
        self,
        *,
        title: str | None,
        content: str,
        evidence_structure: dict[str, object],
        source_context: dict[str, object],
        knowledge_rules: list[str] | None = None,
    ) -> MessageContentAnalysisResult: ...

    async def classify_and_score_importance(
        self,
        *,
        content: str,
        extracted_facts: dict[str, object],
        products: list[str],
        content_form: str,
        source_context: dict[str, object],
        knowledge_rules: list[str] | None = None,
    ) -> MessageClassificationImportanceResult: ...

    async def aggregate_events(
        self,
        *,
        message: dict[str, object],
        possible_event_families: list[str],
        candidates: list[dict[str, object]],
    ) -> EventAggregationResult: ...
