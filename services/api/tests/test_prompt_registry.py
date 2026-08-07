import pytest

from app.prompts import prompt_registry
from app.prompts.registry import (
    CLASSIFICATION_OPERATION,
    CLAIM_GENERATION_OPERATION,
    EVENT_AGGREGATION_OPERATION,
    PRODUCTION_LLM_OPERATIONS,
)


def test_prompt_registry_versions_known_tasks() -> None:
    prompt = prompt_registry.resolve(
        operation="重要性评分",
        content="contract",
        schema_version="ImportanceResult:v1",
    )
    assert prompt.name == "importance-scoring"
    assert prompt.version == "v5-cosmetic-releases"
    assert prompt.content == "contract"


def test_all_production_llm_operations_are_registered() -> None:
    assert PRODUCTION_LLM_OPERATIONS <= prompt_registry.registered_operations
    prompt = prompt_registry.resolve(
        operation=EVENT_AGGREGATION_OPERATION,
        content="unchanged event prompt",
        schema_version="EventDecisionDraft:v1",
    )
    assert prompt.name == "event-decision"
    assert prompt.version == "v7-semantic-candidate-binding"
    classification = prompt_registry.resolve(
        operation=CLASSIFICATION_OPERATION,
        content="dual-axis contract",
        schema_version="ClassificationResult:v1",
    )
    assert classification.name == "classification"
    assert classification.version == "v6-cosmetic-releases"
    claim_generation = prompt_registry.resolve(
        operation=CLAIM_GENERATION_OPERATION,
        content="timeline claim contract",
        schema_version="ClaimGenerationResult:v1",
    )
    assert claim_generation.name == "claim-generation"
    assert claim_generation.version == "v2-timeline"


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
