from copy import copy
import asyncio
from uuid import uuid4
from app.services.call_metering import CallAttempt, digest

import json
import hashlib
import os
import time
from collections.abc import Callable
from typing import Literal, TypeVar
from urllib.parse import urlsplit

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError

from app.core.config import settings

from app.methods.contracts import (
    MessageClassificationImportanceResult,
    MessageContentAnalysisResult,
)
from app.prompts import prompt_registry
from app.prompts.registry import (
    KNOWLEDGE_ORGANIZATION_OPERATION,
    RELEVANCE_OPERATION,
    TRANSLATION_OPERATION,
)
from app.schemas.event_aggregation import EventAggregationResult


class LLMConfigurationError(RuntimeError):
    """Raised when the analysis workflow has no usable LLM configuration."""


class LLMAnalysisError(RuntimeError):
    """Raised when a provider response cannot be used as a news analysis."""


class RelevanceResult(BaseModel):
    decision: Literal["relevant", "irrelevant", "uncertain"]
    confidence: float = Field(ge=0, le=1)
    reason: str = Field(min_length=1)


class OrganizedKnowledgeRule(BaseModel):
    knowledge_type: Literal["analysis", "translation"]
    scope: str = Field(min_length=1, max_length=160)
    rule_text: str = Field(min_length=1, max_length=1000)
    source_rule_ids: list[int] = Field(min_length=1)


class KnowledgeOrganizationResult(BaseModel):
    rules: list[OrganizedKnowledgeRule] = Field(min_length=1)


SchemaT = TypeVar("SchemaT", bound=BaseModel)


def execution_metadata(result: BaseModel) -> dict[str, object]:
    value = getattr(result, "_llm_execution_metadata", {})
    return dict(value) if isinstance(value, dict) else {}


class TranslatedTextBlock(BaseModel):
    index: int = Field(ge=0)
    text: str = Field(min_length=1)


class TranslatedMediaExtraction(BaseModel):
    extraction_id: int = Field(ge=1)
    translated_data: dict[str, object]


class TranslationResult(BaseModel):
    translated_title: str = Field(default="", max_length=500)
    translated_blocks: list[TranslatedTextBlock] = Field(default_factory=list)
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

    def __init__(self, *, before_request=None) -> None:
        self.before_request = before_request
        self.enabled = bool(settings.openai_api_key)
        self.client = (
            AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                timeout=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
                http_client=httpx.AsyncClient(
                    trust_env=False,
                    proxy=settings.outbound_proxy_url or None,
                ),
            )
            if self.enabled
            else None
        )

    def configured(self, *, prompt_ref=None, prompt_contents=None, model_parameters=None):
        """Create a per-call view; never change shared clients or global settings."""
        parameters = dict(model_parameters or {})
        unsupported = set(parameters) - {"model", "temperature", "max_tokens"}
        if unsupported:
            raise ValueError(f"unsupported model parameters: {sorted(unsupported)}")
        if "model" in parameters and (not isinstance(parameters["model"], str) or not parameters["model"].strip()):
            raise ValueError("model must be a nonempty string")
        if "temperature" in parameters and (not isinstance(parameters["temperature"], (int, float)) or not 0 <= parameters["temperature"] <= 2):
            raise ValueError("temperature must be between 0 and 2")
        if "max_tokens" in parameters and (type(parameters["max_tokens"]) is not int or parameters["max_tokens"] < 1):
            raise ValueError("max_tokens must be a positive integer")
        if prompt_ref and not (prompt_contents or {}).get(prompt_ref):
            raise ValueError(f"prompt content is missing for ref: {prompt_ref}")
        configured = copy(self)
        configured._request_parameters = parameters
        configured._prompt_ref = prompt_ref
        configured._prompt_content = (prompt_contents or {}).get(prompt_ref)
        return configured

    async def analyze_message_content(
        self,
        *,
        title: str | None,
        content: str,
        evidence_structure: dict[str, object],
        source_context: dict[str, object],
        knowledge_rules: list[str] | None = None,
    ) -> MessageContentAnalysisResult:
        from app.methods.llm_tasks import BaselineLLMTasks

        return await BaselineLLMTasks.analyze_message_content(self, title=title, content=content, evidence_structure=evidence_structure, source_context=source_context, knowledge_rules=knowledge_rules)

    async def classify_and_score_importance(
        self,
        *,
        content: str,
        extracted_facts: dict[str, object],
        products: list[str],
        content_form: str,
        source_context: dict[str, object],
        knowledge_rules: list[str] | None = None,
    ) -> MessageClassificationImportanceResult:
        from app.methods.llm_tasks import BaselineLLMTasks

        return await BaselineLLMTasks.classify_and_score_importance(self, content=content, extracted_facts=extracted_facts, products=products, content_form=content_form, source_context=source_context, knowledge_rules=knowledge_rules)

    async def aggregate_events(
        self,
        *,
        message: dict[str, object],
        possible_event_families: list[str],
        candidates: list[dict[str, object]],
    ) -> EventAggregationResult:
        from app.methods.llm_tasks import BaselineLLMTasks

        return await BaselineLLMTasks.aggregate_events(self, message=message, possible_event_families=possible_event_families, candidates=candidates)

    async def translate(
        self,
        *,
        title: str | None,
        text_blocks: list[dict[str, object]],
        source_language: str,
        target_language: str = "zh-CN",
        glossary: list[dict[str, object]] | None = None,
        knowledge_rules: list[str] | None = None,
        media_extractions: list[dict[str, object]] | None = None,
        document_context: dict[str, object] | None = None,
    ) -> TranslationResult:
        expected_indexes = {int(block["index"]) for block in text_blocks}
        expected_extraction_ids = {
            int(extraction["extraction_id"]) for extraction in media_extractions or []
        }
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
            result.translated_title = result.translated_title.strip()
            if (title or "").strip() and not result.translated_title:
                return "输入标题非空时 translated_title 不能为空"
            actual_indexes = {block.index for block in result.translated_blocks}
            if actual_indexes != expected_indexes:
                return (
                    "译文块索引不完整："
                    f"expected={sorted(expected_indexes)}, actual={sorted(actual_indexes)}"
                )
            actual_extraction_ids = {
                extraction.extraction_id for extraction in result.translated_media_extractions
            }
            if actual_extraction_ids != expected_extraction_ids:
                return (
                    "结构化版本译文 ID 不完整："
                    f"expected={sorted(expected_extraction_ids)}, "
                    f"actual={sorted(actual_extraction_ids)}"
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
                    requires_localization = target_type in {
                        "champion",
                        "item",
                        "rune",
                        "system",
                    } and any(char.isascii() and char.isalpha() for char in source_target)
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
            "document_context 仅用于保持全文术语、语气和标题一致，不是待翻译正文；"
            "只返回当前 text_blocks 中的 index。若 preferred_translated_title 非空，"
            "translated_title 必须沿用该标题。"
            "输入 title 非空时必须忠实翻译且 translated_title 不能为空；输入 title 为空时"
            "translated_title 可以为空，禁止编造标题。"
            "本阶段只翻译原始标题、正文块和图片结构化内容，不生成摘要、实体、分类或评分。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "source_language": source_language,
                "target_language": target_language,
                "title": title or "",
                "text_blocks": text_blocks,
                "media_extractions": media_extractions or [],
                "document_context": document_context or {},
                "approved_glossary": glossary or [],
                "approved_rules": knowledge_rules or [],
            },
            max_tokens=8000,
            schema=TranslationResult,
            operation=TRANSLATION_OPERATION,
            business_validator=validate_indexes,
        )

    async def judge_relevance(
        self,
        *,
        title: str | None,
        content: str,
        source_context: dict[str, object],
    ) -> RelevanceResult:
        prompt = (
            "你是英雄联盟资讯范围审核员。只判断当前消息是否与英雄联盟相关范围有关，"
            "不在此阶段判断具体产品、内容形式、消息类型或主题。保留范围包括英雄联盟 PC、"
            "云顶之弈、英雄联盟电竞、英雄联盟宇宙、英雄联盟手游、符文之地传说、2XKO、"
            "Riftbound 等英雄联盟相关产品，以及 Riot 公司、平台、周边和媒体业务。"
            "与上述范围明确无关的消息输出 irrelevant；证据不足、纯媒体、纯链接或无法可靠判断时"
            "输出 uncertain 并继续后续消息处理。不能仅凭发布账号判断。只输出 JSON。"
            "字段：decision（relevant/irrelevant/uncertain）、confidence、reason。"
        )
        payload = {
            "title": title or "",
            "content": content,
            "source_context": source_context,
        }
        return await self._validated_json_completion(
            prompt=prompt,
            payload=payload,
            max_tokens=800,
            schema=RelevanceResult,
            operation=RELEVANCE_OPERATION,
        )

    async def organize_knowledge(
        self,
        *,
        rules: list[dict[str, object]],
    ) -> KnowledgeOrganizationResult:
        source_by_id = {int(rule["id"]): rule for rule in rules}

        def validate_coverage(result: KnowledgeOrganizationResult) -> str | None:
            output_ids = [source_id for rule in result.rules for source_id in rule.source_rule_ids]
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
            "必须删除文章标题、具体日期、消息编号、链接以及“这篇文章/这条消息”等"
            "只对单条内容成立的上下文，将退回理由改写成可跨文章复用的判断原则。"
            "不得把文章中的偶然事实、实体或结论泛化成新规则，也不得凭空增加约束。"
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
            operation=KNOWLEDGE_ORGANIZATION_OPERATION,
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
        final_fallback: Callable[[dict[str, object]], SchemaT | None] | None = None,
    ) -> SchemaT:
        if not self.client:
            raise LLMConfigurationError(
                "未配置 OPENAI_API_KEY，无法执行 AI 工作流。请配置 Key 后重试。"
            )
        parameters = getattr(self, "_request_parameters", {})
        model = parameters.get("model", settings.model_name)
        prompt = getattr(self, "_prompt_content", None) or prompt
        provider_options: dict[str, object] = {}
        if "api.deepseek.com" in settings.openai_base_url:
            provider_options["extra_body"] = {"thinking": {"type": "disabled"}}
        output_schema = schema.model_json_schema()
        schema_versions = {
            "MessageContentAnalysisResult": "v2",
            "TranslationResult": "v2",
        }
        prompt_spec = prompt_registry.resolve(
            operation=operation,
            content=prompt,
            schema_version=f"{schema.__name__}:{schema_versions.get(schema.__name__, 'v1')}",
        )
        schema_instruction = (
            "\n\n输出必须严格符合下面的 JSON Schema。所有 required 字段都必须出现，"
            "常量和枚举值必须原样使用，不要增加替代字段：\n"
            f"{json.dumps(output_schema, ensure_ascii=False, separators=(',', ':'))}"
        )
        messages = [
            {"role": "system", "content": prompt_spec.content + schema_instruction},
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]
        last_error = "模型返回空内容"
        last_decoded: dict[str, object] | None = None
        last_raw_content = ""
        last_finish_reason: str | None = None
        started = time.perf_counter()
        input_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        logical_call_id = str(uuid4())
        for attempt in range(1, 3):
            if getattr(self, "before_request", None) is not None:
                await self.before_request()
            meter = CallAttempt(
                logical_call_id=logical_call_id, attempt=attempt,
                provider=urlsplit(settings.openai_base_url).hostname, model=model,
                operation=operation,
                parameters={"temperature": parameters.get("temperature", 0.1),
                            "max_tokens": parameters.get("max_tokens", max_tokens),
                            **provider_options},
                prompt_hash=digest(messages[0]), schema_hash=digest(output_schema),
                input_hash=digest(messages),
            )
            try:
                response = await self.client.chat.completions.create(
                    model=model,
                    response_format={"type": "json_object"},
                    max_tokens=parameters.get("max_tokens", max_tokens),
                    temperature=parameters.get("temperature", 0.1),
                    messages=messages,
                    **provider_options,
                )
            except (APIConnectionError, APITimeoutError) as exc:
                meter.emit("timeout" if isinstance(exc, APITimeoutError) else "failed", error_type=type(exc).__name__)
                last_error = f"模型连接中断：{exc}"
                continue
            except BaseException as exc:
                meter.emit("cancelled" if isinstance(exc, asyncio.CancelledError) else "timeout" if isinstance(exc, TimeoutError) else "failed", error_type=type(exc).__name__)
                raise
            meter.response(response)
            if not response.choices:
                meter.emit("invalid", error_type="EmptyChoices")
                continue
            raw_content = response.choices[0].message.content
            finish_reason = getattr(response.choices[0], "finish_reason", None)
            if not raw_content or not raw_content.strip():
                meter.emit("invalid", error_type="EmptyContent")
                continue
            try:
                decoded = json.loads(raw_content)
                if not isinstance(decoded, dict):
                    raise ValueError("JSON 顶层必须是对象")
                last_decoded = decoded
                last_raw_content = raw_content
                last_finish_reason = finish_reason
                result = schema.model_validate(decoded)
                business_error = business_validator(result) if business_validator else None
                if business_error:
                    raise ValueError(business_error)
                usage = getattr(response, "usage", None)
                usage_payload = (
                    usage.model_dump(mode="json")
                    if usage is not None and hasattr(usage, "model_dump")
                    else {}
                )
                object.__setattr__(
                    result,
                    "_llm_execution_metadata",
                    {
                        "workflow_version": "method-client-v3",
                        "prompt_name": getattr(self, "_prompt_ref", None) or prompt_spec.name,
                        "prompt_version": f"sha256:{prompt_hash}" if getattr(self, "_prompt_ref", None) else prompt_spec.version,
                        "prompt_hash": f"sha256:{prompt_hash}",
                        "model": model,
                        "provider": urlsplit(settings.openai_base_url).hostname,
                        "temperature": parameters.get("temperature", 0.1),
                        "max_tokens": parameters.get("max_tokens", max_tokens),
                        "input_hash": input_hash,
                        "json_schema_version": prompt_spec.schema_version,
                        "raw_response": raw_content[:16000],
                        "usage": usage_payload,
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "retry_count": attempt - 1,
                        "finish_reason": finish_reason,
                        "error_type": None,
                        "commit_sha": (
                            os.getenv("GITHUB_SHA") or os.getenv("CODE_COMMIT_SHA") or None
                        ),
                    },
                )
                meter.emit("succeeded")
                return result
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
                meter.emit("invalid", error_type=type(exc).__name__)
                validation_error = _compact_validation_error(exc)
                last_error = (
                    f"模型输出达到长度上限并被截断；{validation_error}"
                    if finish_reason == "length"
                    else validation_error
                )
                messages.extend(
                    [
                        {"role": "assistant", "content": raw_content[:8000]},
                        {
                            "role": "user",
                            "content": (
                                f"上一次输出未通过结构或业务校验：{last_error}。"
                                "保留未被错误点名的合法字段和内容，只修正对应片段。"
                                "如果是业务校验失败，只修正该错误涉及的字段，不改变其他合法内容。"
                                "同时逐项对照 JSON Schema，修复 JSON 结构、补齐 required 字段并修正常量和枚举值，"
                                "然后重新输出完整 JSON。"
                                "不要输出解释或 Markdown。"
                            ),
                        },
                    ]
                )
        fallback_result = final_fallback(last_decoded) if final_fallback and last_decoded else None
        if fallback_result is not None:
            object.__setattr__(
                fallback_result,
                "_llm_execution_metadata",
                {
                    "workflow_version": "method-client-v3",
                    "prompt_name": getattr(self, "_prompt_ref", None) or prompt_spec.name,
                    "prompt_version": f"sha256:{prompt_hash}" if getattr(self, "_prompt_ref", None) else prompt_spec.version,
                    "prompt_hash": f"sha256:{prompt_hash}",
                    "model": model,
                    "provider": urlsplit(settings.openai_base_url).hostname,
                    "temperature": parameters.get("temperature", 0.1),
                    "max_tokens": parameters.get("max_tokens", max_tokens),
                    "input_hash": input_hash,
                    "json_schema_version": prompt_spec.schema_version,
                    "raw_response": last_raw_content[:16000],
                    "usage": {},
                    "latency_ms": round((time.perf_counter() - started) * 1000),
                    "retry_count": 1,
                    "finish_reason": last_finish_reason,
                    "error_type": "partial_acceptance",
                    "commit_sha": (
                        os.getenv("GITHUB_SHA") or os.getenv("CODE_COMMIT_SHA") or None
                    ),
                },
            )
            return fallback_result
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
