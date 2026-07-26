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


class AnalysisResult(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=60)
    entities: list[dict[str, str]]
    importance_score: float = Field(ge=0, le=1)
    credibility: Literal["official", "corroborated", "unverified", "rumor"]


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


class EventProfile(BaseModel):
    is_event: bool
    event_type: Literal[
        "roster_change",
        "esports_match",
        "tournament",
        "patch_preview",
        "game_update",
        "skin_leak",
        "skin_release",
        "hotfix",
        "incident",
        "announcement",
        "other",
    ]
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    key_facts: list[str]
    occurred_at: str | None = None
    reason: str = Field(min_length=1)


class EventResolution(BaseModel):
    action: Literal["create", "update"]
    event_id: int | None = None
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=60)
    event_type: str = Field(min_length=1, max_length=60)
    entities: list[dict[str, str]]
    importance_score: float = Field(ge=0, le=1)
    credibility: Literal["official", "corroborated", "unverified", "rumor"]
    relation_type: Literal[
        "initial_report",
        "rumor",
        "community_discussion",
        "repost",
        "official_preview",
        "official_confirmation",
        "correction",
        "follow_up",
        "contradiction",
    ]
    adds_new_information: bool
    conflicts: list[dict[str, str]]
    reason: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_action_target(self) -> "EventResolution":
        if self.action == "create" and self.event_id is not None:
            raise ValueError("create action must not include event_id")
        if self.action == "update" and self.event_id is None:
            raise ValueError("update action requires event_id")
        return self


class ReportDraft(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)


SchemaT = TypeVar("SchemaT", bound=BaseModel)


class TranslatedTextBlock(BaseModel):
    index: int = Field(ge=0)
    text: str = Field(min_length=1)


class TranslationResult(BaseModel):
    translated_title: str = Field(min_length=1, max_length=500)
    translated_blocks: list[TranslatedTextBlock] = Field(min_length=1)


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
            "你是英雄联盟新闻编辑。请分析输入资讯，只输出一个完整的 JSON 对象，"
            "不要输出 Markdown。必须包含 title、summary、category、entities、"
            "importance_score、credibility。category 使用简短中文分类；"
            "importance_score 必须为 0 到 1；credibility 只能是 official、"
            "corroborated、unverified、rumor。"
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
    ) -> TranslationResult:
        expected_indexes = {int(block["index"]) for block in text_blocks}

        def validate_indexes(result: TranslationResult) -> str | None:
            actual_indexes = {block.index for block in result.translated_blocks}
            if actual_indexes != expected_indexes:
                return (
                    "译文块索引不完整："
                    f"expected={sorted(expected_indexes)}, actual={sorted(actual_indexes)}"
                )
            return None

        prompt = (
            "你是英雄联盟专业本地化编辑。将输入内容准确翻译为简体中文，"
            "保留英雄、装备、赛事和版本术语，不能删减事实。只输出完整 JSON，"
            "不要输出 Markdown。translated_blocks 必须逐一返回输入中的每个 index。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "source_language": source_language,
                "target_language": target_language,
                "title": title or "",
                "text_blocks": text_blocks,
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

    async def classify_event(
        self,
        *,
        item: dict[str, object],
        knowledge_rules: list[str],
    ) -> EventProfile:
        prompt = (
            "你是英雄联盟新闻事件编辑。判断单条信息是否描述一个可在时间线上追踪的事件。"
            "转会、赛事、版本预览、皮肤爆料、游戏更新、热修复和正式公告通常是事件；"
            "纯观点、攻略、闲聊和无具体发生事项的内容通常不是事件。只输出 JSON，"
            "必须包含 is_event、event_type、title、summary、key_facts、occurred_at、reason。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={"item": item, "approved_rules": knowledge_rules},
            max_tokens=1200,
            schema=EventProfile,
            operation="事件识别",
        )

    async def resolve_event(
        self,
        *,
        item: dict[str, object],
        profile: dict[str, object],
        candidates: list[dict[str, object]],
        source_context: dict[str, object],
        knowledge_rules: list[str],
    ) -> EventResolution:
        prompt = (
            "你是英雄联盟事件聚合编辑。候选事件已由应用程序检索，不得选择候选列表外的 ID。"
            "判断本条信息是否属于某个候选事件；属于则 action=update，否则 action=create。"
            "事件以直接官方信息为主，其他来源作为早期报告、补充、冲突或观点。"
            "相同实体不等于同一事件，必须是同一次、同一时间范围内发生的事情。"
            "输出更新后的事件标题、摘要、分类、类型、实体、重要性、可信度、来源关系、"
            "是否增加新信息、冲突和理由。只输出 JSON。"
        )
        candidate_ids = {int(candidate["id"]) for candidate in candidates}

        def validate_candidate(result: EventResolution) -> str | None:
            if result.action == "update" and result.event_id not in candidate_ids:
                return "update 的 event_id 必须来自 candidate_events"
            return None

        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "item": item,
                "event_profile": profile,
                "candidate_events": candidates,
                "source_context": source_context,
                "approved_rules": knowledge_rules,
            },
            max_tokens=2200,
            schema=EventResolution,
            operation="事件聚合",
            business_validator=validate_candidate,
        )

    async def generate_report(
        self,
        *,
        report_type: str,
        period_start: str,
        period_end: str,
        timezone: str,
        events: list[dict[str, object]],
    ) -> ReportDraft:
        prompt = (
            "你是英雄联盟中文资讯主编。根据给定事件生成日报、周报或月报草稿。"
            "区分本期新事件和本期后续更新；以官方信息为主，未证实内容明确标注；"
            "不要添加输入中不存在的事实。输出 JSON，字段为 title 和 content。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "report_type": report_type,
                "period_start": period_start,
                "period_end": period_end,
                "timezone": timezone,
                "events": events,
            },
            max_tokens=5000,
            schema=ReportDraft,
            operation="报告生成",
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
