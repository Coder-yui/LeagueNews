import json
import hashlib
import os
import time
from collections.abc import Callable
from typing import Literal, TypeVar
from urllib.parse import urlsplit

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings
from app.prompts import prompt_registry
from app.schemas.event_workflow import EventDecisionDraft


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
    importance_evidence: list[str] = Field(min_length=1, max_length=4)


class FactExtractionResult(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=60)
    entities: list[ExtractedEntity] = Field(max_length=5)


class ImportanceDimension(BaseModel):
    score: int = Field(ge=0, le=4)
    evidence: str = Field(min_length=1)


class ImportanceResult(BaseModel):
    impact_scope: ImportanceDimension
    magnitude: ImportanceDimension
    duration: ImportanceDimension
    actionability: ImportanceDimension
    novelty: ImportanceDimension


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
    knowledge_type: Literal[
        "relevance", "analysis", "translation", "event_aggregation"
    ]
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
    translated_title: str = Field(min_length=1, max_length=500)
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

    def __init__(self) -> None:
        self.enabled = bool(settings.openai_api_key)
        self.client = (
            AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
                http_client=httpx.AsyncClient(trust_env=False),
            )
            if self.enabled
            else None
        )

    async def extract_facts(
        self,
        *,
        title: str | None,
        content: str,
        source_context: dict[str, object] | None = None,
        knowledge_rules: list[str] | None = None,
    ) -> FactExtractionResult:
        prompt = (
            "你是英雄联盟中文事实编辑。只根据当前输入提取事实，不判断重要性或可信度。"
            "输出严格 JSON：title、summary、category、entities。展示文本使用简体中文，"
            "不得遗漏明确的专有名词、数值、时间和事实状态，不得用规则中的旧案例补全事实。"
            "entities 每项只含 name、type、canonical_name，保留最关键 2-4 个，最多 5 个；"
            "附属皮肤、礼包、截图或奖励必须同时保留文本明确指向的英雄、模式、赛事、版本或"
            "活动父实体。批量版本图中的英雄和装备不能全部作为新闻核心实体。"
            "category 只描述内容类别。approved_rules 仅约束抽取方式，不是事实来源。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "title": title or "",
                "content": content,
                "source_context": source_context or {},
                "approved_rules": knowledge_rules or [],
            },
            max_tokens=900,
            schema=FactExtractionResult,
            operation="事实抽取",
        )

    async def score_importance(
        self,
        *,
        content: str,
        extracted_facts: dict[str, object],
    ) -> ImportanceResult:
        prompt = (
            "你只评估英雄联盟资讯的重要性，不修改事实、分类、实体或可信度。"
            "分别输出 impact_scope、magnitude、duration、actionability、novelty 五个对象；"
            "每个对象只含 0 至 4 的离散 score 和仅基于当前消息的 evidence。"
            "不要输出最终分数；最终分数、topic floor/cap 和权重由程序确定性计算。"
            "官方身份不得加分，重复提醒或重复证据不得加分。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "content": content,
                "extracted_facts": extracted_facts,
            },
            max_tokens=400,
            schema=ImportanceResult,
            operation="重要性评分",
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
            "importance_score、importance_evidence。"
            "title、summary、category 和实体的展示名称必须使用简体中文；"
            "entities 必须是对象数组，每个对象严格使用 name、type、canonical_name "
            "三个键，禁止使用“英雄”“物品”等动态键；type 使用稳定英文类型；"
            "实体只保留理解这条资讯最重要的 2 到 4 个，确有必要时最多 5 个；"
            "当消息核心是某个模式、英雄、赛事、版本或活动的礼包、皮肤、图标、封面、"
            "奖励、截图、测试服资源等附属内容时，entities 必须同时保留附属对象和文本"
            "明确指向的父级对象，不得只提取礼包或素材；父级对象使用 game_mode、champion、"
            "tournament、patch、activity 等稳定类型。不得凭空推测文本没有指向的父级对象。"
            "版本图片里批量出现的英雄或装备不要全部作为新闻实体；"
            "事实、专有名词原文和数值不得丢失。category 使用简短中文分类；"
            "importance_score 必须为 0 到 1，并按以下统一尺度判断："
            "正式版本改动、平衡调整为 0.88-1.00；新英雄或英雄重做为 0.92-1.00；"
            "新模式、新地图或核心玩法系统为 0.85-0.98；版本预览或开发者前瞻为 0.80-0.92；"
            "严重故障、账号安全、封禁或反作弊重大变化为 0.85-1.00。"
            "仅限全球总决赛、MSI、先锋赛等拳头全球赛事：重大节点为 0.85-0.98；"
            "普通场次无热门队伍为 0.58-0.66；中韩队伍默认属于热门队伍，"
            "有中韩队伍参赛为 0.73-0.78，两支中韩或顶级热门队伍交锋为 0.76-0.80；"
            "涉及晋级、淘汰、决赛或冠军时按重大节点评分。"
            "LPL 决赛、冠军、世界赛资格或重大赛制变化为 0.72-0.88，"
            "LPL 普通赛程和普通赛果如非决赛为 0.50-0.60，不得因为出现热门队伍"
            "而突破 0.60；单一操作集锦、赛后调侃、二创视频或缺少实质赛况的信息"
            "为 0.30-0.50。其他地方联赛应更低。"
            "国服普通游戏活动为 0.66-0.72；国服大型活动为 0.80-0.95；"
            "国服神话商城的常规每周或每日轮换属于低重要性小事件，为 0.30-0.45，"
            "不得套用普通国服活动区间；"
            "国际服普通游戏活动为 0.55-0.60；国际服大型活动为 0.70-0.85；"
            "国服真正可免费获得皮肤的活动为 0.85-0.92，抽奖概率获得、"
            "高额付费或条件苛刻的活动不算免费皮肤。国际服活动通常低于同类国服活动。"
            "独立的新皮肤资讯为 0.70-0.80，根据英雄热度和皮肤品质调整；"
            "新英雄伴生皮肤不得在新英雄高分上额外加分。明星选手转会最高 0.75；"
            "明星选手退役为 0.70-0.80。商业合作和周边通常为 0.25-0.50，"
            "社区招募或线下活动通常为 0.20-0.45，社交互动通常为 0.10-0.30。"
            "按消息核心事实评分，不把多个附属主题机械相加；重复提醒和缺少实质信息应降分。"
            "importance_evidence 用一到三条简短中文理由说明命中的内容类型、"
            "分数区间及主要加减分因素，不要在其中讨论信源或可信度。"
            "可信度与重要性相互独立，官方来源不代表消息一定重要。"
            "approved_rules 仅是判断约束，不是当前消息的事实来源；其中出现的标题、"
            "日期、实体或示例绝不能写入当前结果，除非它们也明确出现在当前 title 或 content 中。"
            "如果当前内容信息不足，只能概括可观察内容，不得用规则中的旧消息补全。"
            "不要判断消息可信度，可信度由系统根据信源配置确定。"
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

    async def propose_event(
        self,
        *,
        item: dict[str, object],
        candidates: list[dict[str, object]],
        stable_event_key: str | None,
        knowledge_rules: list[str],
    ) -> EventDecisionDraft:
        candidate_ids = {int(candidate["event_id"]) for candidate in candidates}
        candidates_by_id = {
            int(candidate["event_id"]): candidate for candidate in candidates
        }
        match_lifecycle_rank = {
            "scheduled": 0,
            "live": 1,
            "completed": 2,
        }

        def validate_candidate(result: EventDecisionDraft) -> str | None:
            if (
                result.decision == "update"
                and result.candidate_event_id not in candidate_ids
            ):
                return "update 只能引用输入候选中的 event_id"
            if result.decision == "create" and result.event_key not in {
                None,
                stable_event_key,
            }:
                return "create 不能编造稳定事件键"
            if result.decision == "create" and stable_event_key is not None:
                exact_candidate = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.get("event_key") == stable_event_key
                    ),
                    None,
                )
                if exact_candidate is not None:
                    return (
                        f"稳定事件键 {stable_event_key} 已对应候选事件 "
                        f"{exact_candidate['event_id']}，不能重复创建"
                    )
            if result.decision == "update":
                candidate = candidates_by_id.get(result.candidate_event_id or -1)
                if (
                    candidate is not None
                    and result.event_type == "match"
                    and candidate.get("event_type") == "match"
                    and result.lifecycle_status in match_lifecycle_rank
                    and candidate.get("lifecycle_status") in match_lifecycle_rank
                    and match_lifecycle_rank[result.lifecycle_status]
                    < match_lifecycle_rank[
                        str(candidate["lifecycle_status"])
                    ]
                ):
                    result.lifecycle_status = str(candidate["lifecycle_status"])
                    result.update_kind = "context"
                    result.evidence_stance = "context"
                    result.title = None
                    result.summary = None
                    result.category = None
                    result.new_facts = []
            policy = item.get("event_policy")
            if isinstance(policy, dict) and policy.get("policy_type") == (
                "mythic_shop_rotation"
            ):
                eligible = bool(policy.get("event_eligible"))
                if eligible:
                    result.event_type = str(
                        policy.get("required_event_type") or "activity"
                    )
                    importance_range = policy.get("importance_range")
                    if (
                        isinstance(importance_range, list)
                        and len(importance_range) == 2
                    ):
                        low = float(importance_range[0])
                        high = float(importance_range[1])
                        result.importance_score = (
                            (low + high) / 2
                            if result.importance_score is None
                            else min(high, max(low, result.importance_score))
                        )
                    if (
                        policy.get("cadence") == "daily"
                        and result.decision == "update"
                    ):
                        result.update_kind = "context"
                        result.evidence_stance = "context"
                if not eligible and result.decision != "not_event":
                    return "非国服神话商城轮换不进入事件层，decision 必须为 not_event"
                if eligible and result.decision == "not_event":
                    return "国服神话商城轮换必须形成或更新周轮换事件，不能是 not_event"
            if result.decision == "create" and candidate_ids:
                rejected_ids = {
                    rejection.event_id
                    for rejection in result.candidate_rejections
                }
                unknown_ids = rejected_ids - candidate_ids
                if unknown_ids:
                    return "candidate_rejections 只能引用输入候选"
                missing_ids = candidate_ids - rejected_ids
                if missing_ids:
                    return (
                        "存在候选时选择 create，必须在 candidate_rejections 中逐一说明"
                        f"为何不是同一事件；缺少 event_id={sorted(missing_ids)}"
                    )
            return None

        prompt = (
            "你是英雄联盟资讯事件聚合编辑。只根据当前已批准中文消息、程序给出的"
            "最多五个候选事件和审核知识，提出结构化草稿。decision 只能是 "
            "not_event、create、update。update 必须引用候选 event_id；create 只能使用"
            "程序提供的 stable_event_key 或 null。不得生成 SQL，不得假设候选之外的事件。"
            "title、summary、category、change_note 和 new_facts 使用简体中文。"
            "事件是明确的现实状态变化，不是相似消息的文件夹。普通 LPL 常规赛结果也必须"
            "形成 match 事件，但重要性通常保持 0.50 到 0.58；只有明确影响排名、晋级或淘汰"
            "才提高。具体到选手、动作和目标战队的单源转会爆料应立即形成 transfer 事件，"
            "lifecycle_status 使用 unconfirmed，标题必须带“传闻”或同等不确定性措辞。"
            "LPL 普通常规赛按比赛日聚合：同一天的赛程预告、进行中、赛果和赛后集锦属于"
            "同一个 match 事件。季后赛后程的半决赛、胜者组决赛、败者组决赛和总决赛才"
            "按单场系列赛独立建事件。scheduled、live、completed 是同一事件的生命周期，"
            "不是拆分事件的依据。晚采集到的较早赛程只能作为 context 加入已经 completed "
            "的事件，不得回退生命周期、标题或摘要。"
            "同一原始来源的转发不算多源。只有原始官方来源直接确认其权责范围内的核心事实时，"
            "official_confirmation 才能为 true；官方账号转发他人内容不算官方确认。"
            "update_kind=duplicate_evidence 或 context 时不得虚构新增事实；只有新增事实、确认、"
            "否认、修正才是显著更新。官方否认使用 evidence_stance=contradicts、"
            "update_kind=refutation、lifecycle_status=officially_refuted。"
            "item.supersedes_raw_item_id 非空表示同一来源文档的新采集版本，不是第二个独立"
            "消息或第二个来源。它应更新原版本所属事件；如果没有实质新事实，必须使用"
            "update_kind=duplicate_evidence，不得增加事件 revision。"
            "版本预览、完整预览、上线和热修复优先进入同一个 patch 主事件；新英雄、英雄重做"
            "和核心玩法系统属于 major_gameplay_change，但当前每条消息只选择一个最主要事件。"
            "候选包含 match_level=strong 的强身份候选和 match_level=broad 的宽召回候选。"
            "对宽召回候选必须结合候选标题、摘要和 core_entities 判断：当前消息中的别名、"
            "缩写、旧译名，以及礼包、皮肤、图标、封面、奖励、截图、测试服资源等附属对象，"
            "如果明确依附于候选事件的主体，应 update 该事件而不是 create。仅有同类词、"
            "相近日期或泛主题相似不能合并。附属素材没有改变事件核心状态时通常使用"
            "update_kind=context，不增加 revision；只有形成可独立追踪的发布、销售或活动"
            "事实时才考虑独立事件。只要存在候选却仍选择 create，必须通过"
            "candidate_rejections 逐一引用每个候选 event_id 并说明不是同一事件的具体原因。"
            "item.event_policy 是程序确定的领域约束，必须遵守。国服神话商城轮换按中国时区"
            "ISO 周聚合为一个低重要性 activity 事件，周内每日轮换作为 context 加入同一事件，"
            "不增加 revision，事件重要性保持 0.30-0.45。即使先处理每日消息，也应使用"
            "stable_event_key 创建本周聚合事件。X 来源的神话商城轮换按国际服处理，"
            "不进入本站国服轮换事件层，必须选择 not_event。"
            "importance_score 评价事件影响，不因消息数量或官方身份加分；可信度与重要性独立。"
            "latest_development 用一句话概括本次最新进展。"
            "not_event 只用于没有明确事实变化的互动、二创、泛宣传或纯观点。只输出 JSON。"
        )
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "item": item,
                "candidates": candidates,
                "stable_event_key": stable_event_key,
                "approved_rules": knowledge_rules,
            },
            max_tokens=1800,
            schema=EventDecisionDraft,
            operation="事件聚合决策",
            business_validator=validate_candidate,
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
            "document_context 仅用于保持全文术语、语气和标题一致，不是待翻译正文；"
            "只返回当前 text_blocks 中的 index。若 preferred_translated_title 非空，"
            "translated_title 必须沿用该标题。"
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
                "approved_glossary": glossary or [],
                "approved_rules": knowledge_rules or [],
                "document_context": document_context or {},
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
        prompt_spec = prompt_registry.resolve(
            operation=operation,
            content=prompt,
            schema_version=f"{schema.__name__}:v1",
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
        started = time.perf_counter()
        input_hash = hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
        ).hexdigest()
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()
        for attempt in range(1, 3):
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
            finish_reason = getattr(response.choices[0], "finish_reason", None)
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
                        "workflow_version": "reviewed-pipeline-v1",
                        "prompt_name": prompt_spec.name,
                        "prompt_version": prompt_spec.version,
                        "prompt_hash": f"sha256:{prompt_hash}",
                        "model": settings.model_name,
                        "provider": urlsplit(settings.openai_base_url).hostname,
                        "temperature": 0.1,
                        "max_tokens": max_tokens,
                        "input_hash": input_hash,
                        "json_schema_version": prompt_spec.schema_version,
                        "raw_response": raw_content[:16000],
                        "usage": usage_payload,
                        "latency_ms": round((time.perf_counter() - started) * 1000),
                        "retry_count": attempt - 1,
                        "finish_reason": finish_reason,
                        "error_type": None,
                        "commit_sha": (
                            os.getenv("GITHUB_SHA")
                            or os.getenv("CODE_COMMIT_SHA")
                            or None
                        ),
                    },
                )
                return result
            except (json.JSONDecodeError, ValidationError, ValueError) as exc:
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
