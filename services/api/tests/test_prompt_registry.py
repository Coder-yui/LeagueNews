from app.prompts import prompt_registry


def test_prompt_registry_versions_known_tasks() -> None:
    prompt = prompt_registry.resolve(
        operation="单条分析",
        content="contract",
        schema_version="AnalysisResult:v1",
    )
    assert prompt.name == "item-analysis"
    assert prompt.version == "v5-importance-rubric"
    assert prompt.content == "contract"
