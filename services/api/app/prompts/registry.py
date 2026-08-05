from dataclasses import dataclass

FACT_EXTRACTION_OPERATION = "事实抽取"
CLASSIFICATION_OPERATION = "分类"
IMPORTANCE_SCORING_OPERATION = "重要性评分"
RELEVANCE_OPERATION = "相关性判断"
TRANSLATION_OPERATION = "翻译"
EVENT_AGGREGATION_OPERATION = "事件聚合决策"
KNOWLEDGE_ORGANIZATION_OPERATION = "知识整理"
PRODUCTION_LLM_OPERATIONS = frozenset(
    {
        FACT_EXTRACTION_OPERATION,
        CLASSIFICATION_OPERATION,
        IMPORTANCE_SCORING_OPERATION,
        RELEVANCE_OPERATION,
        TRANSLATION_OPERATION,
        EVENT_AGGREGATION_OPERATION,
        KNOWLEDGE_ORGANIZATION_OPERATION,
    }
)


@dataclass(frozen=True, slots=True)
class PromptSpec:
    name: str
    version: str
    content: str
    schema_version: str


class PromptRegistry:
    _versions = {
        FACT_EXTRACTION_OPERATION: ("fact-extraction", "v1"),
        CLASSIFICATION_OPERATION: ("classification", "v1"),
        IMPORTANCE_SCORING_OPERATION: (
            "importance-scoring",
            "v3-five-dimensions",
        ),
        RELEVANCE_OPERATION: ("relevance", "v2-product-scope"),
        TRANSLATION_OPERATION: ("translation", "v2-contextual-chunks"),
        EVENT_AGGREGATION_OPERATION: ("event-decision", "v3-editorial-policy"),
        KNOWLEDGE_ORGANIZATION_OPERATION: (
            "knowledge-organization",
            "v2-evaluation-gated",
        ),
        "图片结构化": ("media-structure", "v1"),
    }

    def resolve(
        self,
        *,
        operation: str,
        content: str,
        schema_version: str,
        allow_unregistered: bool = False,
    ) -> PromptSpec:
        registered = self._versions.get(operation)
        if registered is None:
            if not allow_unregistered:
                raise ValueError(f"unregistered LLM operation: {operation}")
            registered = (operation, "unregistered-v1")
        name, version = registered
        return PromptSpec(
            name=name,
            version=version,
            content=content,
            schema_version=schema_version,
        )

    @property
    def registered_operations(self) -> frozenset[str]:
        return frozenset(self._versions)


prompt_registry = PromptRegistry()
