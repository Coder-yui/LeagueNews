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
        summary: str,
        entities: list[dict[str, str]],
        media_extractions: list[dict[str, object]] | None = None,
    ) -> TranslationResult:
        expected_indexes = {int(block["index"]) for block in text_blocks}
        expected_extraction_ids = {
            int(extraction["extraction_id"]) for extraction in media_extractions or []
        }
        expected_entity_count = len(entities)

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
            return None

        prompt = (
            "你是英雄联盟专业本地化编辑。将输入内容准确翻译为简体中文，"
            "保留英雄、装备、赛事、技能、数值和版本术语，不能删减事实。"
            "结构化版本数据必须保持原 JSON 结构和 extraction_id，只翻译其中需要展示的"
            "自然语言字符串，不得改动数字、运算符和字段名。只输出完整 JSON，不要输出"
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
