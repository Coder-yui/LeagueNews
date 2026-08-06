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
from app.domain.importance import (
    AudienceRegion,
    CompetitionRegion,
    ImportanceScale,
    ImportanceSubtype,
    Prominence,
    SkinTier,
)
from app.prompts import prompt_registry
from app.prompts.registry import (
    CLAIM_GENERATION_OPERATION,
    CLASSIFICATION_OPERATION,
    EVENT_AGGREGATION_OPERATION,
    FACT_EXTRACTION_OPERATION,
    IMPORTANCE_SCORING_OPERATION,
    KNOWLEDGE_ORGANIZATION_OPERATION,
    RELEVANCE_OPERATION,
    TRANSLATION_OPERATION,
)
from app.schemas.event_workflow import EventDecisionDraft
from app.services.event_decision import validate_event_decision_business


class LLMConfigurationError(RuntimeError):
    """Raised when the analysis workflow has no usable LLM configuration."""


class LLMAnalysisError(RuntimeError):
    """Raised when a provider response cannot be used as a news analysis."""


class ExtractedEntity(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    canonical_name: str | None = None


class FactExtractionResult(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    category: str = Field(min_length=1, max_length=60)
    entities: list[ExtractedEntity] = Field(max_length=5)


class ClassifiedEntityRole(BaseModel):
    name: str = Field(min_length=1)
    type: str = Field(min_length=1)
    role: Literal["core", "context", "affected"]


class ClassificationTemporal(BaseModel):
    is_recurring: bool
    recurrence_window: Literal["daily", "weekly", "patch_cycle"] | None
    certainty: Literal["confirmed", "likely", "speculative"]


class ClassificationResult(BaseModel):
    content_type: Literal[
        "official_fact",
        "official_notice",
        "match_result",
        "insider_rumor",
        "insider_confirmed",
        "data_mine",
        "aggregation",
        "community_noise",
    ]
    topic: Literal[
        "patch",
        "champion",
        "game_mode",
        "esports",
        "roster",
        "skin",
        "activity",
        "service",
        "community",
        "business",
        "other",
    ]
    secondary_topics: list[
        Literal[
            "patch",
            "champion",
            "game_mode",
            "esports",
            "roster",
            "skin",
            "activity",
            "service",
            "community",
            "business",
            "other",
        ]
    ] = Field(default_factory=list, max_length=2)
    entity_roles: list[ClassifiedEntityRole] = Field(default_factory=list)
    temporal: ClassificationTemporal


class FactClaimDraft(BaseModel):
    subject: dict[str, object]
    predicate: Literal[
        "transfers_to",
        "considered_for",
        "leaves",
        "stays",
        "retires",
        "releases",
        "goes_live",
        "previews",
        "delays",
        "patches",
        "buffs",
        "nerfs",
        "reworks",
        "adds_mode",
        "wins",
        "loses",
        "advances",
        "eliminated",
        "rotates",
        "discounts",
        "gifts",
    ]
    object: dict[str, object]
    temporal_role: Literal["state", "event", "prediction"]
    supersedes_hint: str | None = None


class ClaimAttributionDraft(BaseModel):
    claimed_by: str = Field(min_length=1)
    stance: Literal["asserts", "confirms", "refutes", "contextualizes"]
    certainty: Literal["confirmed", "likely", "speculative"]


class ClaimGenerationResult(BaseModel):
    fact_claims: list[FactClaimDraft] = Field(default_factory=list, max_length=20)
    attribution: ClaimAttributionDraft


class ImportanceResult(BaseModel):
    editorial_subtype: ImportanceSubtype
    scale: ImportanceScale
    audience_region: AudienceRegion
    competition_region: CompetitionRegion
    prominence: Prominence
    skin_tier: SkinTier
    is_bulk_update: bool
    is_first_concrete_disclosure: bool
    is_duplicate_or_reminder: bool
    evidence: list[str] = Field(min_length=1, max_length=6)


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
            raise ValueError("uncertain scope must preserve is_lol_relevant=false")
        return self


class OrganizedKnowledgeRule(BaseModel):
    knowledge_type: Literal[
        "analysis", "translation", "event_aggregation"
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
            operation=FACT_EXTRACTION_OPERATION,
        )

    async def classify(
        self,
        *,
        content: str,
        extracted_facts: dict[str, object],
        source_context: dict[str, object] | None = None,
    ) -> ClassificationResult:
        prompt = """你是英雄联盟中文资讯的分类器。只依据当前消息的文本与图片OCR结果分类，
不判断重要性、不判断可信度、不改写事实。

输出严格 JSON：
{
  "content_type": <见下方枚举>,
  "topic": <见下方枚举>,
  "secondary_topics": [最多2个次要topic],
  "entity_roles": [
    {"name": 实体名, "type": 实体类型, "role": "core|context|affected"}
  ],
  "temporal": {
    "is_recurring": bool,        // 是否属于周期性内容(如每日商城/每周活动)
    "recurrence_window": "daily|weekly|patch_cycle|null",
    "certainty": "confirmed|likely|speculative"  // 措辞确定性,不等于可信度
  }
}

content_type 枚举与判定规则：
- official_fact: 官方账号发布的既成事实（皮肤已上线、更新已生效）
- official_notice: 官方预告、活动规则、赛程安排（尚未发生但官方确定）
- match_result: 已结束比赛的比分结果
- insider_rumor: 非官方账号的爆料，且措辞含试探性（"考虑中""据说""官宣为准"）
- insider_confirmed: 非官方账号但措辞确定（"确认""已敲定"），仍非官方
- data_mine: 测试服/客户端文件挖掘、开发者技术披露
- aggregation: 明显转载/搬运其他来源的内容（含转发、引用官方原文）
- community_noise: 抽奖、应援、纯个人感想，无新增事实

判定要点：
1. content_type 看"谁说的+怎么说的"，topic 看"说的是什么"，二者独立。
2. certainty 只反映消息本身措辞的确定程度，不要因为是官方就填 confirmed——
   官方预告未来的事仍是 confirmed（官方有权决定），爆料人的"确认"最多 likely。
3. entity_roles 的 core 是新闻主角（转会的选手、更新的英雄、上线的皮肤、
   对阵的战队），context 是背景实体（赛事名、版本号、俱乐部），
   affected 是被波及实体（被修复bug涉及的皮肤）。批量版本图里的英雄列表
   不要全标 core。
4. is_recurring=true 用于每日神话商城、每周活动这类会周期重复的内容，
   供下游按时间窗口聚合。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "content": content,
                "extracted_facts": extracted_facts,
                "source_context": source_context or {},
            },
            max_tokens=900,
            schema=ClassificationResult,
            operation=CLASSIFICATION_OPERATION,
        )

    async def score_importance(
        self,
        *,
        content: str,
        extracted_facts: dict[str, object],
        classification: dict[str, object],
        source_context: dict[str, object],
    ) -> ImportanceResult:
        prompt = """你是英雄联盟资讯编辑，只提取重要性规则需要的结构化特征，
不输出最终分数，不判断可信度，也不评估行动紧迫性。最终分由程序按编辑政策确定性计算。

editorial_subtype 必须从以下语义中选择最精确的一项：
- shop_daily_standard：国服神话商城普通每日/每周轮换
- shop_cosmetic_rotation：神话商城皮肤或炫彩轮换
- shop_rare_cosmetic：明确涉及稀有、限定或神话炫彩轮换
- shop_bulk_refresh：随版本批量上新大量商城内容
- patch_preview：设计师或官方版本改动预览，但不是完整内容
- patch_full_preview：设计师发布完整版本预览或完整改动表
- patch_official_notes：正式官方完整版本更新公告
- patch_hotfix：单英雄、小范围热修复或微补丁
- new_champion / new_game_mode / major_gameplay_change：新英雄、新模式、重大玩法系统
- activity_paid：主要是付费或氪金活动
- activity_standard：普通游戏活动或一般免费奖励
- activity_free_skin：明确可免费获得皮肤的活动；抽奖但不保证获得时不要选此项
- riot_corporate / lol_universe：拳头公司事务 / 英雄联盟世界观
- esports_regular / esports_playoffs / esports_final：赛区常规赛 / 季后赛 / 决赛
- worlds_regular / worlds_key：世界赛普通比赛 / 焦点、晋级、淘汰或关键场次
- roster_transfer：选手、教练转会或阵容变动
- skin_release / service_incident / community / other：皮肤发布、服务故障、社区内容、其他

其他字段：
- scale：minor / standard / major，仅表示同一子类型内的影响规模。
- audience_region：国服内容用 cn；国服与其他服务器都会生效用 global；
  明确只影响外服、不影响国服用 international_only；信息不足用 unknown。
  Riot/X 来源不等于只影响外服，必须依据消息实际适用范围判断。
- competition_region：仅赛事使用 lpl/lck/international/other，非赛事用 none。
- prominence：涉及普通对象用 normal，知名队伍/选手用 notable，明星选手或顶级焦点队伍用 star。
- skin_tier：仅新皮肤发布使用 standard / legendary / prestige_or_mythic / ultimate，
  非新皮肤内容使用 none；必须依据消息明确写出的档次，不得凭空推断。
- is_bulk_update：是否包含大量对象或批量新增。
- is_first_concrete_disclosure：是否首次给出具体数值、名单、赛果或确定内容。
- is_duplicate_or_reminder：是否只是重复提醒、无新增事实的转载或重复发布。
- evidence：1-6条消息中的具体文本依据，不得编造。

注意：
- “官方”本身不决定重要性；完整版本内容、新英雄、新模式等内容属性才决定高分。
- 完整预览与普通预览必须区分；单英雄热修复不能误判为完整版本更新。
- 常规赛须区分赛区；转会须识别明星对象，但不要因消息未官宣而降低重要性，
  未官宣只影响可信度。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "content": content,
                "extracted_facts": extracted_facts,
                "classification": classification,
                "source_context": source_context,
            },
            max_tokens=700,
            schema=ImportanceResult,
            operation=IMPORTANCE_SCORING_OPERATION,
        )

    async def generate_claims(
        self,
        *,
        content: str,
        extracted_facts: dict[str, object],
        classification: dict[str, object],
        source_context: dict[str, object],
    ) -> ClaimGenerationResult:
        prompt = """你是英雄联盟事实断言抽取器。把消息拆成"可独立验证的原子事实断言"，
每条断言是一个可能随时间被确认或推翻的陈述。

输出严格 JSON：
{
  "fact_claims": [
    {
      "subject": {"name": 事实主体, "type": 实体类型},
      "predicate": 谓词(见下),
      "object": {结构化宾语},
      "temporal_role": "state|event|prediction",  // 现状/已发生/预测
      "supersedes_hint": 若本条明显更新了某个更早断言,给出关键词
    }
  ],
  "attribution": {
    "claimed_by": 发布者,
    "stance": "asserts|confirms|refutes|contextualizes",
    "certainty": "confirmed|likely|speculative"
  }
}

谓词规范（转会类必须能串成时间线）：
- transfers_to / considered_for / leaves / stays / retires  (转会)
- releases / goes_live / previews / delays                   (上线/预告)
- patches / buffs / nerfs / reworks / adds_mode              (版本)
- wins / loses / advances / eliminated                       (赛事)
- rotates / discounts / gifts                                (商城/活动)

要点：
1. subject 是事实主体本身（选手、英雄、战队、商城），不是发布者。
   发布者只进 attribution。
2. 一条消息可产生多个 fact_claim（如官方更新公告同时含"修复经典模式皮肤"
   和"修复客户端崩溃"两个断言）。
3. considered_for 用于"考虑中"的候选（如 WBG 打野候选 Beichuan/蔻蔻），
   transfers_to 用于已确定。爆料人说"考虑"就用 considered_for，
   不要升格成 transfers_to。
4. temporal_role: prediction 用于未发生的预告/传闻，event 用于已发生，
   state 用于持续状态。这决定时间线上的节点样式。
5. supersedes_hint 帮助下游把"传闻 A 候选 → 官宣 B 入队"串成同一时间线。
6. 版本预览图片如果只有增强/削弱名单、没有每个对象的具体数值，不要为每个英雄各写一条。
   同一分组只生成一条断言，把完整名单放在 object.targets；有具体数值或机制变化时才拆开。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "content": content,
                "extracted_facts": extracted_facts,
                "classification": classification,
                "source_context": source_context,
            },
            max_tokens=1800,
            schema=ClaimGenerationResult,
            operation=CLAIM_GENERATION_OPERATION,
        )

    async def propose_event(
        self,
        *,
        item: dict[str, object],
        candidates: list[dict[str, object]],
        stable_event_key: str | None,
        knowledge_rules: list[str],
        route_aggregation_keys: list[str] | None = None,
    ) -> EventDecisionDraft:
        candidate_ids = {int(candidate["event_id"]) for candidate in candidates}
        candidates_by_id = {
            int(candidate["event_id"]): candidate for candidate in candidates
        }
        allowed_new_keys = set(route_aggregation_keys or [])
        if stable_event_key:
            allowed_new_keys.add(stable_event_key)
        match_lifecycle_rank = {
            "scheduled": 0,
            "live": 1,
            "completed": 2,
        }

        def validate_candidate(result: EventDecisionDraft) -> str | None:
            shared_error = validate_event_decision_business(
                result,
                item=item,
                candidates=candidates,
                allowed_new_keys=allowed_new_keys,
            )
            if shared_error:
                return shared_error
            for membership in result.memberships:
                existing_event_id = membership.existing_event_id
                if (
                    existing_event_id is not None
                    and existing_event_id not in candidate_ids
                ):
                    return "existing target 只能引用输入候选中的 event_id"
                if (
                    membership.target == "new"
                    and allowed_new_keys
                    and membership.aggregation_key not in allowed_new_keys
                ):
                    return "new membership 不能编造程序路由之外的 aggregation_key"
                exact_candidate = next(
                    (
                        candidate
                        for candidate in candidates
                        if candidate.get("aggregation_key")
                        == membership.aggregation_key
                    ),
                    None,
                )
                if membership.target == "new" and exact_candidate is not None:
                    return (
                        f"聚合键 {membership.aggregation_key} 已对应候选事件 "
                        f"{exact_candidate['event_id']}，不能重复创建"
                    )
                candidate = candidates_by_id.get(existing_event_id or -1)
                if (
                    candidate is not None
                    and membership.event_type
                    in {"daily_matches", "major_match"}
                    and candidate.get("event_type")
                    in {"daily_matches", "major_match"}
                    and membership.lifecycle_status in match_lifecycle_rank
                    and candidate.get("lifecycle_status") in match_lifecycle_rank
                    and match_lifecycle_rank[membership.lifecycle_status]
                    < match_lifecycle_rank[
                        str(candidate["lifecycle_status"])
                    ]
                ):
                    membership.lifecycle_status = str(
                        candidate["lifecycle_status"]
                    )
                    membership.update_kind = "context"
                    membership.evidence_stance = "context"
            policy = item.get("event_policy")
            if isinstance(policy, dict) and policy.get("policy_type") == (
                "mythic_shop_rotation"
            ):
                eligible = bool(policy.get("event_eligible"))
                if eligible:
                    if not result.memberships:
                        return "国服神话商城轮换必须形成或更新 shop_rotation"
                    for membership in result.memberships:
                        membership.event_type = "shop_rotation"
                        if policy.get("cadence") == "daily":
                            membership.update_kind = "context"
                            membership.evidence_stance = "context"
                elif result.memberships:
                    return "非国服神话商城轮换不进入事件层"
            if any(
                membership.target == "new"
                for membership in result.memberships
            ) and candidate_ids:
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
                        "存在候选时选择 new，必须在 candidate_rejections 中逐一说明"
                        f"为何不是同一事件；缺少 event_id={sorted(missing_ids)}"
                    )
            return None

        prompt = """你是英雄联盟事件编辑。给定一条已分析消息（含 fact_claims、content_type、
topic、entities[].role）和若干候选事件，决定这条消息如何归属。

输入：
- 当前消息：{title, summary, fact_claims, content_type, topic, entities, published_at}
- 候选事件列表：每个含 {event_id, event_type, title, aggregation_key,
  timeline节点摘要, lifecycle_status, 成员消息数}

输出严格 JSON（一条消息可对多个事件产生 decision）：
{
  "memberships": [
    {
      "target": "existing:{event_id}" | "new",
      "event_type": <见事件类型枚举>,
      "aggregation_key": 该事件的聚合键,
      "membership_role": "primary|component|cross_ref",
      "evidence_stance": "supports|contradicts|context",
      "update_kind": "new_fact|confirmation|refutation|correction|context|duplicate_evidence",
      "lifecycle_status": <生命周期状态或null>,
      "timeline_note": 这条消息在时间线上代表的节点（如"WBG考虑Beichuan等候选")
    }
  ],
  "candidate_rejections": [{event_id, reason}]  // 当选择new但存在候选时必填
}

归属规则：
1. 一条消息可同时归属多个事件。典型：官方大版本更新公告 → 以 primary 进入
   patch_cycle 事件，其"经典模式"子事实以 component 进入 major_gameplay_change 事件。
   仅当消息确实包含该事件的核心事实时才归属，不要因沾边就挂靠。

2. 时间线型事件（transfer_saga/patch_cycle/release_saga）：
   - 同一聚合键的新消息用 update，推进 lifecycle
   - 转会：传闻阶段 lifecycle=unconfirmed 且标题含"传闻"；官方确认→confirmed，
     update_kind=confirmation；官方否认→officially_refuted，update_kind=refutation
   - 不要把同一转会的不同传闻拆成多个事件——只要聚合键(选手/位置)相同就是同一时间线

3. 周期窗口型事件（shop_rotation/daily_matches）：
   - 按 aggregation_key 的时间窗口归属：同一周的商城变动进同一事件，
     同一天同一赛区的比赛进同一事件
   - 跨窗口一律新建，不要跨周合并

4. 单点型（major_match）：LPL/LCK总决赛、世界赛关键场单独成事件，
   即使同一天也不并入 daily_matches

5. certainty=speculative 的爆料只能 supports 一个 unconfirmed 事件，
   不能直接把事件推进到 confirmed。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "item": item,
                "candidates": candidates,
                "stable_event_key": stable_event_key,
                "route_aggregation_keys": route_aggregation_keys or [],
                "approved_rules": knowledge_rules,
            },
            max_tokens=1800,
            schema=EventDecisionDraft,
            operation=EVENT_AGGREGATION_OPERATION,
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
            "你是英雄联盟资讯范围审核员。判断输入是否属于本项目保留范围。"
            "保留英雄联盟端游、英雄联盟电竞、云顶之弈、英雄联盟世界观/影视、"
            "Riot 公司新闻，以及英雄联盟周边、音乐和商业合作。"
            "明确排除英雄联盟手游 Wild Rift 和 2XKO；普通私人内容也排除。"
            "不能仅凭账号身份判断，必须依据本条内容。只输出 JSON。\n"
            "信息不足、无法可靠归类时使用 product_scope=uncertain，"
            "并保持 is_lol_relevant=false；系统会将 uncertain 送入后续流程。\n"
            "字段：product_scope、is_lol_relevant、confidence、reason。"
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
