from dataclasses import dataclass

CLASSIFICATION_OPERATION = "分类"
IMPORTANCE_SCORING_OPERATION = "重要性评分"
RELEVANCE_OPERATION = "相关性判断"
TRANSLATION_OPERATION = "翻译"
KNOWLEDGE_ORGANIZATION_OPERATION = "知识整理"
PRODUCTION_LLM_OPERATIONS = frozenset(
    {
        CLASSIFICATION_OPERATION,
        IMPORTANCE_SCORING_OPERATION,
        RELEVANCE_OPERATION,
        TRANSLATION_OPERATION,
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
        CLASSIFICATION_OPERATION: (
            "message-content-analysis",
            "v6-title-summarizability",
        ),
        IMPORTANCE_SCORING_OPERATION: (
            "message-classification-importance",
            "v13-community-promotion",
        ),
        RELEVANCE_OPERATION: ("relevance", "v3-lol-scope"),
        TRANSLATION_OPERATION: ("translation", "v3-single-request"),
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
