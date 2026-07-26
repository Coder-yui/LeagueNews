import json
from collections.abc import Callable
from typing import Literal, TypeVar

from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings


class LLMConfigurationError(RuntimeError):
    """Raised when the analysis workflow has no usable LLM configuration."""


class LLMAnalysisError(RuntimeError):
    """Raised when a provider response cannot be used as a news analysis."""


class ExtractedEntity(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    canonical_name: str | None = None


class AnalysisResult(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=60)
    entities: list[ExtractedEntity] = Field(max_length=5)
    importance_score: float = Field(ge=0, le=1)
    credibility: Literal["official", "corroborated", "unverified", "rumor"]
    credibility_score: float = Field(ge=0, le=1)
    credibility_evidence: list[str] = Field(default_factory=list)


class RelevanceResult(BaseModel):
    product_scope: Literal[
        "lol_pc",
        "lol_esports",
        "tft",
        "lol_universe",
        "riot_corporate",
        "lol_merch_music",
        "wild_rift",
        "2xko",
        "unrelated",
        "uncertain",
    ]
    is_lol_relevant: bool
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_scope_decision(self) -> "RelevanceResult":
        retained = {
            "lol_pc",
            "lol_esports",
            "tft",
            "lol_universe",
            "riot_corporate",
            "lol_merch_music",
        }
        if self.product_scope in retained and not self.is_lol_relevant:
            raise ValueError("retained product_scope must set is_lol_relevant=true")
        if self.product_scope in {"wild_rift", "2xko", "unrelated"} and self.is_lol_relevant:
            raise ValueError("excluded product_scope must set is_lol_relevant=false")
        if self.product_scope == "uncertain" and self.is_lol_relevant:
            raise ValueError("uncertain scope cannot enter downstream processing")
        return self


class OrganizedKnowledgeRule(BaseModel):
    knowledge_type: Literal["relevance", "analysis", "translation"]
    scope: str = Field(min_length=1, max_length=160)
    rule_text: str = Field(min_length=1, max_length=1000)
    source_rule_ids: list[int] = Field(min_length=1)


class KnowledgeOrganizationResult(BaseModel):
    rules: list[OrganizedKnowledgeRule] = Field(min_length=1)


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class TranslatedTextBlock(BaseModel):
    index: int = Field(ge=0)
    text: str = Field(min_length=1)


class TranslatedMediaExtraction(BaseModel):
    extraction_id: int = Field(ge=1)
    translated_data: dict[str, object]


class TranslationResult(BaseModel):
    translated_title: str = Field(min_length=1, max_length=500)
    translated_blocks: list[TranslatedTextBlock] = Field(default_factory=list)
    translated_summary: str = Field(min_length=1)
    translated_entities: list[ExtractedEntity] = Field(max_length=5)
    translated_media_extractions: list[TranslatedMediaExtraction] = Field(default_factory=list)


class PatchEntry(BaseModel):
    target: str = Field(min_length=1)
    target_type: Literal["champion", "item", "rune", "system", "other"]
    changes: list[str] = Field(default_factory=list)


class PatchSection(BaseModel):
    section_type: Literal[
        "champion_buff",
        "champion_nerf",
        "champion_adjustment",
        "system_buff",
        "system_nerf",
        "system_adjustment",
        "item_buff",
        "item_nerf",
        "item_adjustment",
        "rune_buff",
        "rune_nerf",
        "rune_adjustment",
        "adjustment",
        "other",
    ]
    label: str = Field(min_length=1)
    entries: list[PatchEntry]


class PatchPreviewExtraction(BaseModel):
    document_type: Literal["patch_preview"]
    preview_kind: Literal["preview", "full_preview"]
    patch: str | None = None
    title: str = Field(min_length=1)
    sections: list[PatchSection] = Field(min_length=1)
    warnings: list[str]


class LLMClient:
    """Thin OpenAI-compatible boundary; workflow code does not depend on a provider."""

    def __init__(self) -> None:
        self.enabled = bool(settings.openai_api_key)
        self.client = (
            AsyncOpenAI(api_key=settings.openai_api_key, base_url=settings.openai_base_url)
            if self.enabled
            else None
        )

    async def analyze(
        self,
        *,
        title: str | None,
        content: str,
        source_context: dict[str, object] | None = None,
        knowledge_rules: list[str] | None = None,
    ) -> AnalysisResult:
        prompt = (
            "你是英雄联盟中文新闻编辑。请分析输入资讯，只输出一个完整的 JSON 对象，"
            "不要输出 Markdown。必须包含 title、summary、category、entities、"
            "importance_score、credibility、credibility_score、credibility_evidence。"
            "title、summary、category 和实体的展示名称必须使用简体中文；"
            "entities 必须是对象数组，每个对象严格使用 name、type、canonical_name "
            "三个键，禁止使用“英雄”“物品”等动态键；type 使用稳定英文类型；"
            "实体只保留理解这条资讯最重要的 2 到 4 个，确有必要时最多 5 个；"
            "版本图片里批量出现的英雄或装备不要全部作为新闻实体；"
            "事实、专有名词原文和数值不得丢失。category 使用简短中文分类；"
            "importance_score 必须为 0 到 1；credibility 只能是 official、"
            "corroborated、unverified、rumor；credibility_score 必须为 0 到 1；"
            "credibility_evidence 列出支撑可信度判断的简短依据。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "title": title or "",
                "content": content,
                "source_context": source_context or {},
                "approved_rules": knowledge_rules or [],
            },
            max_tokens=1200,
            schema=AnalysisResult,
            operation="单条分析",
        )

    async def translate(
        self,
        *,
        title: str | None,
        text_blocks: list[dict[str, object]],
        source_language: str,
        target_language: str = "zh-CN",
        glossary: list[dict[str, object]] | None = None,
        knowledge_rules: list[str] | None = None,
        summary: str,
        entities: list[dict[str, str]],
        media_extractions: list[dict[str, object]] | None = None,
    ) -> TranslationResult:
        expected_indexes = {int(block["index"]) for block in text_blocks}
        expected_extraction_ids = {
            int(extraction["extraction_id"]) for extraction in media_extractions or []
        }
        expected_entity_count = len(entities)
        source_extractions = {
            int(extraction["extraction_id"]): extraction.get("structured_data") or {}
            for extraction in media_extractions or []
        }

        def collect_targets(
            value: object,
            path: tuple[str | int, ...] = (),
        ) -> dict[tuple[str | int, ...], tuple[str, str]]:
            targets: dict[tuple[str | int, ...], tuple[str, str]] = {}
            if isinstance(value, dict):
                target = value.get("target")
                target_type = value.get("target_type")
                if isinstance(target, str) and isinstance(target_type, str):
                    targets[path] = (target, target_type)
                for key, child in value.items():
                    targets.update(collect_targets(child, (*path, str(key))))
            elif isinstance(value, list):
                for index, child in enumerate(value):
                    targets.update(collect_targets(child, (*path, index)))
            return targets

        def compare_structure(
            source: object,
            translated: object,
            path: tuple[str | int, ...] = (),
        ) -> str | None:
            path_text = ".".join(str(part) for part in path) or "root"
            if isinstance(source, dict):
                if not isinstance(translated, dict):
                    return f"{path_text} 应保持为对象"
                if source.keys() != translated.keys():
                    return f"{path_text} 的字段集合发生变化"
                for key, value in source.items():
                    error = compare_structure(value, translated[key], (*path, key))
                    if error:
                        return error
                return None
            if isinstance(source, list):
                if not isinstance(translated, list):
                    return f"{path_text} 应保持为数组"
                if path and path[-1] == "changes":
                    if not all(isinstance(item, str) for item in translated):
                        return f"{path_text} 应为文本数组"
                    return None
                if len(source) != len(translated):
                    return (
                        f"{path_text} 数组长度发生变化："
                        f"expected={len(source)}, actual={len(translated)}"
                    )
                for index, value in enumerate(source):
                    error = compare_structure(
                        value,
                        translated[index],
                        (*path, index),
                    )
                    if error:
                        return error
                return None
            if isinstance(source, str):
                return None if isinstance(translated, str) else f"{path_text} 应保持为字符串"
            if source != translated or type(source) is not type(translated):
                return f"{path_text} 的非文本值被改动"
            return None

        def validate_indexes(result: TranslationResult) -> str | None:
            actual_indexes = {block.index for block in result.translated_blocks}
            if actual_indexes != expected_indexes:
                return (
                    "译文块索引不完整："
                    f"expected={sorted(expected_indexes)}, actual={sorted(actual_indexes)}"
                )
            actual_extraction_ids = {
                extraction.extraction_id
                for extraction in result.translated_media_extractions
            }
            if actual_extraction_ids != expected_extraction_ids:
                return (
                    "结构化版本译文 ID 不完整："
                    f"expected={sorted(expected_extraction_ids)}, "
                    f"actual={sorted(actual_extraction_ids)}"
                )
            if len(result.translated_entities) != expected_entity_count:
                return (
                    "翻译前后实体数量不一致："
                    f"expected={expected_entity_count}, "
                    f"actual={len(result.translated_entities)}"
                )
            translated_by_id = {
                extraction.extraction_id: extraction.translated_data
                for extraction in result.translated_media_extractions
            }
            for extraction_id, source_data in source_extractions.items():
                structure_error = compare_structure(
                    source_data,
                    translated_by_id[extraction_id],
                )
                if structure_error:
                    return (
                        "结构化版本译文必须与原数据逐项对应："
                        f"extraction_id={extraction_id}, {structure_error}"
                    )
                source_targets = collect_targets(source_data)
                translated_targets = collect_targets(translated_by_id[extraction_id])
                if source_targets.keys() != translated_targets.keys():
                    return f"结构化版本 target 结构发生变化：extraction_id={extraction_id}"
                for path, (source_target, target_type) in source_targets.items():
                    translated_target = translated_targets[path][0]
                    requires_localization = (
                        target_type in {"champion", "item", "rune", "system"}
                        and any(char.isascii() and char.isalpha() for char in source_target)
                    )
                    if (
                        requires_localization
                        and translated_target.casefold() == source_target.casefold()
                    ):
                        return (
                            "结构化版本 target 未翻译为官方简体中文名称："
                            f"extraction_id={extraction_id}, target={source_target}"
                        )
            return None

        prompt = (
            "你是英雄联盟专业本地化编辑。将输入内容准确翻译为简体中文，"
            "保留英雄、装备、赛事、技能、数值和版本术语，不能删减事实。"
            "必须遵守 approved_rules 中经过人工审核的翻译规则，并优先采用 "
            "approved_glossary 中的标准术语。"
            "结构化版本数据必须保持原 JSON 结构、section/entry 对应关系和 extraction_id，"
            "只翻译其中需要展示的自然语言字符串，不得改动数字、运算符和字段名。"
            "changes 是同一条改动的文本分行，译文可以按中文表达需要合并或拆分行数，"
            "但不得增删改动事实。结构化版本 entries 中的 "
            "target 是前端展示名称而不是不可变标识符：target_type 为 champion、item、"
            "rune 或 system 时，必须把英文 target 翻译成英雄联盟官方简体中文名称。"
            "必须忠实翻译 target 当前使用的称谓层级：名称翻译为对应名称，称号翻译为"
            "对应称号，技能名翻译为对应技能名，不得擅自替换成同一对象的名称、称号、"
            "昵称或其他相关称谓。例如 Aphelios 必须译为“厄斐琉斯”，不能替换为其称号"
            "“残月之肃”；若原文 target 本身是该称号，则应译为“残月之肃”。"
            "只输出完整 JSON，不要输出"
            "Markdown。translated_blocks 必须逐一返回输入中的每个 index；"
            "translated_entities 必须与输入实体一一对应，不能增加或删除。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "source_language": source_language,
                "target_language": target_language,
                "title": title or "",
                "text_blocks": text_blocks,
                "summary": summary,
                "entities": entities,
                "media_extractions": media_extractions or [],
                "approved_glossary": glossary or [],
                "approved_rules": knowledge_rules or [],
            },
            max_tokens=8000,
            schema=TranslationResult,
            operation="翻译",
            business_validator=validate_indexes,
        )

    async def judge_relevance(
        self,
        *,
        title: str | None,
        content: str,
        source_context: dict[str, object],
        knowledge_rules: list[str],
    ) -> RelevanceResult:
        prompt = (
            "你是英雄联盟资讯范围审核员。判断输入是否属于本项目保留范围。"
            "保留英雄联盟端游、英雄联盟电竞、云顶之弈、英雄联盟世界观/影视、"
            "Riot 公司新闻，以及英雄联盟周边、音乐和商业合作。"
            "明确排除英雄联盟手游 Wild Rift 和 2XKO；普通私人内容也排除。"
            "不能仅凭账号身份判断，必须依据本条内容。只输出 JSON。\n"
            "字段：product_scope、is_lol_relevant、confidence、reason。"
        )
        payload = {
            "title": title or "",
            "content": content,
            "source_context": source_context,
            "approved_rules": knowledge_rules,
        }
        return await self._validated_json_completion(
            prompt=prompt,
            payload=payload,
            max_tokens=800,
            schema=RelevanceResult,
            operation="相关性判断",
        )

    async def organize_knowledge(
        self,
        *,
        rules: list[dict[str, object]],
    ) -> KnowledgeOrganizationResult:
        source_by_id = {int(rule["id"]): rule for rule in rules}

        def validate_coverage(result: KnowledgeOrganizationResult) -> str | None:
            output_ids = [
                source_id
                for rule in result.rules
                for source_id in rule.source_rule_ids
            ]
            expected_ids = sorted(source_by_id)
            if sorted(output_ids) != expected_ids:
                return (
                    "source_rule_ids 必须完整且仅使用一次："
                    f"expected={expected_ids}, actual={sorted(output_ids)}"
                )
            for organized in result.rules:
                sources = [source_by_id[source_id] for source_id in organized.source_rule_ids]
                if any(
                    source["knowledge_type"] != organized.knowledge_type
                    or source["scope"] != organized.scope
                    for source in sources
                ):
                    return "只能合并 knowledge_type 和 scope 完全相同的规则"
            return None

        prompt = (
            "你是知识库编辑。整理所有输入规则：去除口语、背景叙述和重复表达，"
            "合并语义重复或可组成同一判断原则的规则，但不得丢失有效约束、例外条件"
            "或纠正结论。每条输出应是简洁、明确、可直接提供给模型执行的中文规则，"
            "通常一到三句话。只能合并 knowledge_type 与 scope 完全相同的规则。"
            "每个输入规则 ID 必须在 source_rule_ids 中出现且只出现一次。"
            "只输出 JSON，不要输出 Markdown。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={"rules": rules},
            max_tokens=4000,
            schema=KnowledgeOrganizationResult,
            operation="知识整理",
            business_validator=validate_coverage,
        )

    async def _validated_json_completion(
        self,
        *,
        prompt: str,
        payload: dict[str, object],
        max_tokens: int,
        schema: type[SchemaT],
        operation: str,
        business_validator: Callable[[SchemaT], str | None] | None = None,
    ) -> SchemaT:
        if not self.client:
            raise LLMConfigurationError(
                "未配置 OPENAI_API_KEY，无法执行 AI 工作流。请配置 Key 后重试。"
            )
        provider_options: dict[str, object] = {}
        if "api.deepseek.com" in settings.openai_base_url:
            provider_options["extra_body"] = {"thinking": {"type": "disabled"}}
        output_schema = schema.model_json_schema()
        schema_instruction = (
            "\n\n输出必须严格符合下面的 JSON Schema。所有 required 字段都必须出现，"
            "常量和枚举值必须原样使用，不要增加替代字段：\n"
            f"{json.dumps(output_schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        messages = [
            {"role": "system", "content": prompt + schema_instruction},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]
        last_error = "模型返回空内容"
        for _ in range(2):
            try:
                response = await self.client.chat.completions.create(
                    model=settings.model_name,
                    response_format={"type": "json_object"},
                    max_tokens=max_tokens,
                    temperature=0.1,
                    messages=messages,
                    **provider_options,
                )
            except (APIConnectionError, APITimeoutError) as exc:
                last_error = f"模型连接中断：{exc}"
                continue
            raw_content = response.choices[0].message.content
            if not raw_content or not raw_content.strip():
                continue
            try:
                decoded = json.loads(raw_content)
                if not isinstance(decoded, dict):
                    raise ValueError("JSON 顶层必须是对象")
                result = schema.model_validate(decoded)
                business_error = business_validator(result) if business_validator else None
                if business_error:
                    raise ValueError(business_error)
                return result
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                last_error = _compact_validation_error(exc)
                messages.extend(
                    [
                        {"role": "assistant", "content": raw_content[:8000]},
                        {
                            "role": "user",
                            "content": (
                                f"上一次输出未通过校验：{last_error}。"
                                "请逐项对照系统消息中的 JSON Schema，补齐所有 required 字段，"
                                "修正常量和枚举值，然后重新输出完整 JSON。"
                                "不要输出解释或 Markdown。"
                            ),
                        },
                    ]
                )
        raise LLMAnalysisError(
            f"{operation}失败：模型连续两次未通过结构或业务校验：{last_error}。"
            "原始资讯和既有正式数据均未改变，可修正后重试。"
        )


def _compact_validation_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        errors = exc.errors(include_url=False, include_input=False)
        return "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'root'}: {error['msg']}"
            for error in errors[:8]
        )
    return str(exc)[:1000]
