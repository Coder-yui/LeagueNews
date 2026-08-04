from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PromptSpec:
    name: str
    version: str
    content: str
    schema_version: str


class PromptRegistry:
    _versions = {
        "单条分析": ("item-analysis", "v5-importance-rubric"),
        "事实抽取": ("fact-extraction", "v1"),
        "重要性评分": ("importance-scoring", "v2-five-dimensions"),
        "相关性判断": ("relevance", "v2-product-scope"),
        "翻译": ("translation", "v2-contextual-chunks"),
        "事件判断": ("event-decision", "v3-editorial-policy"),
        "知识整理": ("knowledge-organization", "v2-evaluation-gated"),
        "图片结构化": ("media-structure", "v1"),
    }

    def resolve(
        self,
        *,
        operation: str,
        content: str,
        schema_version: str,
    ) -> PromptSpec:
        name, version = self._versions.get(
            operation, (operation, "unregistered-v1")
        )
        return PromptSpec(
            name=name,
            version=version,
            content=content,
            schema_version=schema_version,
        )


prompt_registry = PromptRegistry()
