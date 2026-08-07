import json
import hashlib
import os
import time
from collections.abc import Callable
from typing import Literal, TypeVar
from urllib.parse import urlsplit

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.core.config import settings
from app.domain.importance import (
    AudienceRegion,
    CompetitionRegion,
    ImportanceScale,
    Prominence,
    SkinTier,
)
from app.domain.ontology import (
    ContentForm,
    EntityType,
    EventAssertion,
    InformationStage,
    PRIMARY_TOPICS,
    PrimaryTopic,
    SourceKind,
    Subtopic,
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
from app.schemas.workflow import (
    EventMentionDraft,
    EventMentionTemporalDraft,
)
from app.services.event_decision import (
    stabilize_event_decision,
    validate_event_decision_business,
)


class LLMConfigurationError(RuntimeError):
    """Raised when the analysis workflow has no usable LLM configuration."""


class LLMAnalysisError(RuntimeError):
    """Raised when a provider response cannot be used as a news analysis."""


class ExtractedEntity(BaseModel):
    name: str = Field(min_length=1)
    type: EntityType
    canonical_name: str | None = None


class FactExtractionResult(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    summary: str = Field(min_length=1)
    entities: list[ExtractedEntity] = Field(max_length=8)


class ClassifiedEntityRole(BaseModel):
    name: str = Field(min_length=1)
    type: EntityType
    role: Literal["core", "context", "affected"]


class ClassificationResult(BaseModel):
    source_kind: SourceKind
    information_stage: InformationStage
    content_form: ContentForm
    topic: PrimaryTopic
    subtopic: Subtopic
    secondary_topics: list[PrimaryTopic] = Field(default_factory=list, max_length=2)
    entity_roles: list[ClassifiedEntityRole] = Field(default_factory=list)
    event_mentions: list[EventMentionDraft] = Field(
        default_factory=list,
        max_length=12,
    )
    event_assertion: EventAssertion = "asserted"
    temporal: EventMentionTemporalDraft

    @field_validator("secondary_topics", mode="before")
    @classmethod
    def discard_unknown_secondary_topics(cls, value: object) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for entry in value:
            topic = str(entry).strip().casefold()
            if topic in PRIMARY_TOPICS and topic not in normalized:
                normalized.append(topic)
        return normalized[:2]


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
        "adjusts",
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

    @field_validator("predicate", mode="before")
    @classmethod
    def normalize_predicate(cls, value: object) -> object:
        return {
            "changes": "adjusts",
            "modifies": "adjusts",
            "updates": "adjusts",
        }.get(value, value)


class ClaimAttributionDraft(BaseModel):
    claimed_by: str = Field(min_length=1)
    stance: Literal["asserts", "confirms", "refutes", "contextualizes"]
    certainty: Literal["confirmed", "likely", "speculative"]


class ClaimGenerationResult(BaseModel):
    fact_claims: list[FactClaimDraft] = Field(default_factory=list, max_length=8)
    attribution: ClaimAttributionDraft


class ImportanceResult(BaseModel):
    scale: ImportanceScale
    audience_region: AudienceRegion
    competition_region: CompetitionRegion
    prominence: Prominence
    skin_tier: SkinTier
    is_bulk_update: bool
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
    knowledge_type: Literal["analysis", "translation", "event_aggregation"]
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
                timeout=settings.llm_timeout_seconds,
                max_retries=settings.llm_max_retries,
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
            "输出严格 JSON：title、summary、entities。展示文本使用简体中文，"
            "不得遗漏明确的专有名词、数值、时间和事实状态，不得用规则中的旧案例补全事实。"
            "entities 每项只含 name、type、canonical_name，type 必须是 champion、skin、"
            "item、rune、patch、game_mode、team、player、coach、person、tournament、"
            "league、activity、region、organization、product、system、other、unknown 之一。"
            "保留最关键 2-6 个，最多 8 个。canonical_name 必须是跨消息稳定的身份名："
            "职业选手优先常用比赛ID，战队优先官方简称，联赛去掉年份、赛段和轮次后缀；"
            "不确定别名时保留原名，不得猜造。"
            "附属皮肤、礼包、截图或奖励必须同时保留文本明确指向的英雄、模式、赛事、版本或"
            "活动父实体。同一公告批量发布同系列皮肤时，必须保留系列/套系父实体，单款皮肤"
            "作为其组成对象。批量版本图中的英雄和装备不能全部作为新闻核心实体。"
            "approved_rules 仅约束抽取方式，不是事实来源。"
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
        prompt = """你是英雄联盟中文资讯的多轴分类器。只依据输入中明确列出的可用证据分类，
不判断重要性、不判断可信度、不改写事实。

判定要点：
1. source_kind 只表示事实由谁直接声称：first_party 是权利主体直接发布；
   attributed_report 是第三方报道；data_mined 是数据挖掘；community 是社区表达。
   官方账号转发别人时不能因账号身份写 first_party。
2. information_stage 表示事实所处阶段：announcement 是已确认但尚未开始，preview 是版本/测试服预览，
   active 是已上线或正在发生，result 是已结束结果，update 是同一事件的新进展，correction 是更正，
   rumor/speculation 是未确认消息，reminder 是无新事实提醒，commentary 是观点。
   content_form 只表示证据组织方式：original、repost、roundup；推广或讨论不是 content_form。
3. topic 是宽领域，subtopic 是可执行的细分类。云顶版本、云顶饰品、赛事票务、周免、
   纪律安全、实体周边必须使用对应受控 subtopic，不得用 other 代替。新皮肤、单独报道的
   新炫彩，以及国服新增的付费臻彩都使用 topic=skin、subtopic=skin_release；商城轮换、
   返场和折扣仍使用 commerce 下对应的商城 subtopic。
4. certainty 只反映消息措辞的确定程度，不等于可信度。
   官方预告未来的事仍是 confirmed（官方有权决定），爆料人的"确认"最多 likely。
5. entity_roles 的 core 是新闻主角（转会的选手、更新的英雄、上线的皮肤、炫彩或臻彩、
   对阵的战队），context 是背景实体（赛事名、版本号、俱乐部），
   affected 是被波及实体（被修复bug涉及的皮肤）。批量版本图里的英雄列表
   不要全标 core。
6. event_mentions 列出消息中每个可独立聚合的事件提及，不是关键词列表。每项都使用受控
   topic/subtopic，并给出最小 identity_entities、独立 assertion、事件日期和成员角色：
   - 转会按核心选手拆分；同一消息分别谈到多名选手时可有多项；
   - 单场比赛保留两支队伍及联赛。同一赛程列出多场比赛时，每场各一项；
   - 同一公告、同一上线批次的系列皮肤/炫彩只列一项，以系列/套系为 core 身份，单款对象
     不是独立事件；该批外观的售价、签名礼包、销售期和未来商城去向都是发布属性，不另列
     commerce 事件；赛事冠军、战队和选手等设计背景也不是当前公告主张的新赛事事件。
     只有 roundup 中彼此独立的发布批次才拆分；
   - 其他发布保留发布对象；版本保留版本号；活动保留活动或通行证本身；
   - primary 是消息主事件，component 是同一消息明确发布的附属事件，cross_ref 只用于
     消息确实提供上下文但不主张该事件的新事实。
   canonical_name 使用跨语言、简称和全称都稳定的名称。可依据明确队伍归属和稳定领域知识
   规范化联赛，但不得补充无法判断的参与者。没有可聚合事件时输出空数组。
7. event_assertion 表示消息主叙事是否主张事件发生；每个 event_mention.assertion 单独表示
   该事件提及的状态：asserted 是已发生或明确安排，
   speculative 是爆料、推测或候选方案，negated 是明确表示不会发生/没有上线/已否认，
   context_only 是只提到对象或发表讨论，没有新的事件主张。传闻仍用 speculative，
   不要因为未官宣就写 context_only。
8. temporal.event_date 填事件日期；相对日期可结合 published_at 解析。
   只有完全没有日期线索时才留空，赛程不得用发布日期代替比赛日期。
9. 主分类按消息主张的动作而不是附属奖励。只有明确出现通行证、宝典、战令、pass、购买等级、
   付费解锁等级、等级奖励或里程碑进度机制时，才使用 topic=activity、subtopic=event_pass，皮肤等奖励
   只作 affected。普通命名活动、口令参与活动、抽奖或概率奖池活动使用 in_game_activity；
   抽奖活动无论免费还是付费氪金、是否另有累计抽数奖励，都不属于 event_pass，奖池中的皮肤、
   炫彩或臻彩只作 affected。
   商城礼包使用 commerce 下对应子主题。只有消息主要宣布新皮肤、新炫彩或国服新臻彩本身发布
   时才是 skin_release；炫彩即使作为新皮肤的配套内容被单独报道，也属于同一发布档。
   event_mentions 可同时保留其中明确的附属发布。
   消息若只在推测或说明既有皮肤的获取方式，并明确指向通行证/宝典/战令，则主分类仍是
   activity/event_pass，不能因列出皮肤名称改成 skin_release。
10. 已确定可免费领取、兑换或开箱的皮肤等奖励使用 topic=activity、subtopic=free_reward。
   第三方通知玩家“现已可领取”且没有新增活动规则时，information_stage=reminder；不能因
   “开箱”“比惨”等社区化表达改成 community_post。若奖励属于命名活动，event_mentions
   必须把该活动作为 core 身份实体，以便领取提醒加入原活动，而不是另建奖励事件。付费通行证、
   商城购买、抽奖和概率奖励不适用本条。
11. attributed_report 包括第三方转述可识别主体的官宣或报道；只有当前发布源本身就是
   权利主体时才是 first_party。community 仅用于发布者自己的观点、玩笑或讨论。
12. 可用证据只包括原文标题、正文和已确认的 designer_patch_changes。输入未提供的媒体内容不得猜测。
13. “测试服/PBE”只决定证据来源和阶段，不决定内容主题：测试服英雄、装备或模式数值变动使用
    patch/pbe_change；测试服礼包、商城目录、价格、获取方式、封面或商业物料使用
    commerce/shop_offer。只有明确宣布玩法可用、上线或发布时才使用 game_mode_release。
14. 非官方来源发布英雄、装备、模式、皮肤或其他内容更新时，无论措辞是否声称已经确认或上线，
    都只能作为爆料处理：information_stage=preview、
    event_assertion=speculative，各对应 event_mention 也必须是 speculative；不能把爆料人的确定语气
    当作官方确认。只有设计师本人或游戏官方信源直接发布的更新才可作为正式更新。
15. “不停机更新/无需停机更新/热修复/hotfix”统一使用 topic=patch、subtopic=hotfix，
    受影响英雄、模式和系统只是 affected 实体，不得把它们改成主分类。
16. 以提问、投票、征集回忆或观点为主要动作，且没有日期、版本、数值、机制或改动明细的互动帖，
    即使顺带写“即将上线”，也使用 community/community_post、information_stage=commentary、
    event_assertion=context_only，并输出空 event_mentions。混合多个联赛的赛程中，每场比赛应在
    identity_entities 中补充可由稳定队伍归属确定的 league；不要因正文称某场为“焦点战”而改变分类。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "content": content,
                "extracted_facts": extracted_facts,
                "source_context": source_context or {},
            },
            max_tokens=1800,
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

字段：
- scale：minor / standard / major，仅表示同一子类型内的影响规模。
- audience_region：国服内容用 cn；国服与其他服务器都会生效用 global；
  明确只影响外服、不影响国服用 international_only；信息不足用 unknown。
  Riot/X 来源不等于只影响外服，必须依据消息实际适用范围判断。
- competition_region：仅赛事使用 lpl/lck/international/other，非赛事用 none。
- prominence：涉及普通对象用 normal，知名队伍/选手用 notable，明星选手或顶级焦点队伍用 star。
- skin_tier：仅新皮肤、新炫彩和国服新臻彩发布使用 standard / legendary /
  prestige_or_mythic / ultimate，非新外观发布使用 none；必须依据消息明确写出的档次，
  不得凭空推断。"臻彩"不是"至臻皮肤"，仅因臻彩名称不得标为 prestige_or_mythic。
- is_bulk_update：是否包含大量对象或批量新增。
- evidence：1-6条消息中的具体文本依据，不得编造。

注意：
- “官方”本身不决定重要性；完整版本内容、新英雄、新模式等内容属性才决定高分。
- 常规赛须区分赛区；转会须识别明星对象，但不要因消息未官宣而降低重要性，
  未官宣只影响可信度。
- 当前输入没有历史对照，不得猜测“首次披露”或“重复消息”。"""
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
- patches / buffs / nerfs / reworks / adjusts / adds_mode    (版本)
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
6. 最多输出 8 条。版本和批量活动按变化方向或事实阶段分组，不要为对象列表逐项生成断言。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "content": content,
                "extracted_facts": extracted_facts,
                "classification": classification,
                "source_context": source_context,
            },
            max_tokens=900,
            schema=ClaimGenerationResult,
            operation=CLAIM_GENERATION_OPERATION,
        )

    async def propose_event(
        self,
        *,
        item: dict[str, object],
        candidates: list[dict[str, object]],
        knowledge_rules: list[str],
    ) -> EventDecisionDraft:
        routes = [
            route
            for route in item.get("event_routes", [])
            if isinstance(route, dict) and route.get("aggregation_key")
        ]
        allowed_new_keys = {
            str(route["aggregation_key"])
            for route in routes
            if route.get("creation_policy") != "existing_only"
        }

        def validate_candidate(result: EventDecisionDraft) -> str | None:
            if routes:
                stabilize_event_decision(
                    result,
                    item=item,
                    candidates=candidates,
                )
            return validate_event_decision_business(
                result,
                item=item,
                candidates=candidates,
                allowed_new_keys=allowed_new_keys,
            )

        prompt = """你是英雄联盟事件编辑。给定一条已分析消息（含受控分类轴、
        fact_claims 和 entities[].role）以及若干候选事件，决定这条消息如何归属。

输入：
- 当前消息：{title, summary, fact_claims, source_kind, information_stage,
  content_form, topic, subtopic, product_scope, entities, event_mentions,
  event_routes, published_at}
- 候选事件列表：每个含 {event_id, event_kind, aggregation_strategy,
  product_scope, title, aggregation_key,
  timeline节点摘要, lifecycle_status, 成员消息数}

输出严格 JSON（一条消息可对多个事件产生 decision）：
{
  "memberships": [
    {
      "target": "existing:{event_id}" | "new",
      "event_kind": 事件的事实类型,
      "aggregation_strategy": 时间线|版本周期|日历日|周期窗口|发布链|单点,
      "product_scope": 产品范围,
      "aggregation_key": 该事件的聚合键,
      "identity_resolution": "exact_key|semantic_candidate|new_event",
      "identity_rationale": 语义同一事件的简短依据或null,
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
1. 一条消息可同时归属多个事件。典型：官方大版本更新公告以 primary 进入
   gameplay_update + patch_cycle，其新模式子事实以 component 进入
   gameplay_release + release。
   仅当消息确实包含该事件的核心事实时才归属，不要因沾边就挂靠。

2. event_routes 是程序完成主题簇归并后生成的允许路由。事件数量和路由身份已经确定，模型
   不能根据原始提及数量重新拆分，也不能修改其中的键或三个分类轴：
   - 同键候选必须选择 existing，并标记 exact_key；不同 product_scope 必须分开
   - 版本按 product+版本号，转会按年份+选手，活动/发布按命名主体或共同批次，
     普通比赛日和重大单场分别使用程序给定的身份
   - 没有同键候选时，若候选与当前路由的 event_kind、aggregation_strategy、product_scope
     全部相同，且从标题、摘要、核心实体和时间线能判断只是同一对象的跨语言、简称、全称
     或稳定同义名称差异，可以选择该 existing 候选，标记 semantic_candidate，并在
     identity_rationale 说明依据。仅有主题相似、共享一个背景实体或时间接近都不够。
   - 语义上不是同一事件时选择 new_event。不得为了减少事件数量强行语义合并。
3. aggregation_strategy=timeline/patch_cycle/release 的事件：
   - 同一聚合键的新消息用 update，推进 lifecycle
   - 转会：传闻阶段 lifecycle=unconfirmed 且标题含"传闻"；官方确认→confirmed，
     update_kind=confirmation；官方否认→officially_refuted，update_kind=refutation
   - 不要把同一选手转会的离队、加盟传闻和官宣拆成多个事件

4. recurring_window/calendar_day 的事件：
   - 按 aggregation_key 的时间窗口归属：同一周的商城变动进同一事件，
     同一天同一赛区的比赛进同一事件
   - 跨窗口一律新建，不要跨周合并

5. esports_match 可以是 calendar_day 比赛日或 timeline 重大单场。预告、分场赛果、整日
   总结和赛后评论使用程序给定的同一键；commentary 只作为 context，不推进已完成事件的
   生命周期。
6. certainty=speculative 的爆料只能 supports 一个 unconfirmed 事件，
   不能直接把事件推进到 confirmed。
7. 每条 creation_policy=allow 的路由都必须有归属；existing_only 路由只能加入语义同一的
   已有候选，没有同一候选时省略。新建事件只能选择 event_routes 给出的稳定键和三个分类轴；
   没有路由时返回空 memberships。
   不得创建含 unknown 的聚合键。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "item": item,
                "candidates": candidates,
                "approved_rules": knowledge_rules,
            },
            max_tokens=2000,
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
                            os.getenv("GITHUB_SHA") or os.getenv("CODE_COMMIT_SHA") or None
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
