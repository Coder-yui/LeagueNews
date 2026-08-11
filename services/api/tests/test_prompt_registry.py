import pytest

from app.prompts import prompt_registry
from app.prompts.registry import (
    CLASSIFICATION_OPERATION,
    PRODUCTION_LLM_OPERATIONS,
    TRANSLATION_OPERATION,
)


def test_production_prompt_registry_contract() -> None:
    importance = prompt_registry.resolve(
        operation="重要性评分",
        content="contract",
        schema_version="MessageClassificationImportanceResult:v1",
    )
    assert importance.name == "message-classification-importance"
    assert importance.version == "v13-community-promotion"
    assert importance.content == "contract"
    assert PRODUCTION_LLM_OPERATIONS <= prompt_registry.registered_operations
    classification = prompt_registry.resolve(
        operation=CLASSIFICATION_OPERATION,
        content="message taxonomy contract",
        schema_version="MessageContentAnalysisResult:v1",
    )
    assert classification.name == "message-content-analysis"
    assert classification.version == "v6-title-summarizability"
    translation = prompt_registry.resolve(
        operation=TRANSLATION_OPERATION,
        content="single request translation",
        schema_version="TranslationResult:v1",
    )
    assert translation.name == "translation"
    assert translation.version == "v3-single-request"


def test_unregistered_operations_require_explicit_experimental_opt_in() -> None:
    with pytest.raises(ValueError, match="unregistered LLM operation"):
        prompt_registry.resolve(
            operation="实验操作",
            content="experiment",
            schema_version="Experiment:v1",
        )
    prompt = prompt_registry.resolve(
        operation="实验操作",
        content="experiment",
        schema_version="Experiment:v1",
        allow_unregistered=True,
    )
    assert prompt.version == "unregistered-v1"
