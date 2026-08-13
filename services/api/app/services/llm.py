import json
import hashlib
import os
import time
from collections.abc import Callable
from typing import Literal, TypeVar, cast
from urllib.parse import urlsplit

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.core.config import settings
from app.domain.importance import (
    AudienceRegion,
    CompetitionRegion,
    ImportanceScale,
    Prominence,
    SkinTier,
)
from app.domain.message_taxonomy import (
    CLASSIFICATION_VERSION,
    ContentForm as MessageContentForm,
    MessageType,
    Product,
    SourceKind,
    Topic,
    TOPIC_ORDER,
    classification_catalog,
    classification_error,
    content_analysis_catalog,
    message_content_error,
)
from app.domain.message_entities import EntityType
from app.prompts import prompt_registry
from app.prompts.registry import (
    CLASSIFICATION_OPERATION,
    EVENT_AGGREGATION_OPERATION,
    IMPORTANCE_SCORING_OPERATION,
    KNOWLEDGE_ORGANIZATION_OPERATION,
    RELEVANCE_OPERATION,
    TRANSLATION_OPERATION,
)
from app.schemas.event_aggregation import EventAggregationResult


class LLMConfigurationError(RuntimeError):
    """Raised when the analysis workflow has no usable LLM configuration."""


class LLMAnalysisError(RuntimeError):
    """Raised when a provider response cannot be used as a news analysis."""


class ExtractedEntity(BaseModel):
    name: str = Field(min_length=1)
    type: EntityType
    canonical_name: str | None = None


class MessageContentAnalysisResult(BaseModel):
    title: str = Field(default="", max_length=500)
    summary: str
    entities: list[ExtractedEntity] = Field(default_factory=list, max_length=8)
    products: list[Product] = Field(min_length=1, max_length=3)
    content_form: MessageContentForm
    classification_version: Literal[CLASSIFICATION_VERSION] = CLASSIFICATION_VERSION

    @model_validator(mode="after")
    def validate_controlled_classification(self) -> "MessageContentAnalysisResult":
        self.title = self.title.strip()
        if self.content_form in {"media_only", "link_only"}:
            self.summary = ""
            self.entities = []
        error = message_content_error(
            products=list(self.products),
            content_form=self.content_form,
            title=self.title,
            summary=self.summary,
            entities=list(self.entities),
        )
        if error:
            raise ValueError(error)
        return self


class MessageClassificationImportanceResult(BaseModel):
    message_type: MessageType
    topics: list[Topic] = Field(min_length=1)
    scale: ImportanceScale
    audience_region: AudienceRegion
    competition_region: CompetitionRegion
    prominence: Prominence
    skin_tier: SkinTier
    is_bulk_update: bool
    evidence: list[str] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def normalize_topic_order(self) -> "MessageClassificationImportanceResult":
        selected = set(self.topics)
        self.topics = [topic for topic in TOPIC_ORDER if topic in selected]
        return self


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

    async def analyze_message_content(
        self,
        *,
        title: str | None,
        content: str,
        evidence_structure: dict[str, object],
        source_context: dict[str, object],
        knowledge_rules: list[str] | None = None,
    ) -> MessageContentAnalysisResult:
        prompt = """你是英雄联盟资讯的消息内容分析器。只根据当前消息证据判断产品和内容形式，
同时提取标题、摘要和实体。不判断消息类型、主题、重要性、事件或可信度，也不补充输入中不存在的事实。
不生成事件候选。输出严格 JSON。

执行规则：
1. products 允许多选但尽量单选，只有多个产品都是消息实质主体时才增加，最多 3 个；顺带提及不选。
2. content_form 单选：original 是发布者直接发布；repost 是无实质新增的转发；quote 是引用后附加了
   可独立理解的新文字；media_only 仅限标题和正文都没有足够可读语义、只有媒体；link_only 仅限标题
   和正文都没有足够可读语义、只有未成功提取正文的链接。标题也是当前消息证据；不能因为正文只有
   图片就忽略标题。选择 media_only 前先判断仅根据标题能否忠实概括消息的对象与事项、生成非空
   摘要；如果可以，说明已有可处理语义，按 original 并输出摘要。
3. media_only 或 link_only 时固定 products=[unknown]、summary 为空、entities 为空；输入没有明确标题时
   title 可以为空，禁止为了满足字段而编造标题；不能根据媒体、链接地址、账号或常识猜测内容，
   但必须使用输入标题中明确写出的语义。
4. unknown 与其他 product 互斥，products 按目录顺序输出。
5. title 与 summary 使用简体中文。original、repost、quote 都必须输出非空 title 和 summary；
   repost 的摘要忠实概括被转发消息的可读内容。summary 概括消息的主要事实、观点或提醒，
   不写重要性和可信度。
6. entities 最多 8 个，只提取文本明确出现且有检索价值的实体。每项包含 name、type、canonical_name；
   type 必须是既有受控实体类型。canonical_name 不确定时保留原名，不能猜造。
7. 开发者报告、开发日志和路线图中针对英雄联盟端游的未来改动属于 lol_pc，不属于公司业务。
8. 文本明确出现具体产品名时，以该产品作为讨论对象；该产品复用的英雄、皮肤或世界观元素不能
   单独把 products 改判为 lol_pc 或 lol_universe。
approved_rules 只约束处理方式，不是当前消息的事实来源。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "title": title or "",
                "content": content,
                "evidence_structure": evidence_structure,
                "source_context": source_context,
                "controlled_catalog": content_analysis_catalog(),
                "approved_rules": knowledge_rules or [],
            },
            max_tokens=2200,
            schema=MessageContentAnalysisResult,
            operation=CLASSIFICATION_OPERATION,
        )

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
        source_kind_value = source_context.get("classification_source_kind")
        if source_kind_value not in {"official", "unofficial", "unknown"}:
            raise ValueError(
                "source_context.classification_source_kind must be official, "
                "unofficial, or unknown"
            )
        source_kind = cast(SourceKind, source_kind_value)

        def validate_classification(
            result: MessageClassificationImportanceResult,
        ) -> str | None:
            return classification_error(
                products=products,
                content_form=content_form,
                message_type=result.message_type,
                topics=list(result.topics),
                source_kind=source_kind,
            )

        prompt = """你是英雄联盟资讯编辑。已知产品、内容形式和本轮确定的分类信源性质；只从本次提供的
候选目录中选择一个 message_type 和一个或多个 topics，同时提取重要性规则需要的结构化特征。
不重新判断 products/content_form，不输出最终分数，不判断可信度，也不评估行动紧迫性。
最终分由程序按编辑政策确定性计算。输出严格 JSON。

分类规则：
- message_type 必须且只能从 controlled_catalog.message_types 中单选。
- topics 只能从 controlled_catalog.topics 中选择，只标消息实质讨论的领域，按候选目录顺序输出。
- unknown 与同字段其他值互斥。不能输出未披露的 code。
- classification_source 由程序根据当前发布者或可验证的上游来源确定。不得自行改判其官方性质；
  source_kind=unknown 只表示同时披露两侧候选，不构成任何官方证据结论。
- 非官方渠道的测试服（PBE）、测试服改动或 PBE 改动等未确认游戏信息优先考虑 game_leak。

游戏官方公告、预览与推广互动的判断顺序：
1. 先判断主要传播目的，不因内容涉及未来版本、上线或回归就直接选择公告或预览。
2. 以确定事项的正式告知为主体，提供可独立获取和核验的明确开放时间、适用范围、参与或获取
   方式、规则或安排时，考虑 game_announcement；宣传措辞应只是辅助。
3. 多项具体机制、数值、规则、玩法或开发说明是主体时，考虑 game_official_preview。
4. 短帖、视频、口号式展示、预热、唤起回忆或参与引导是主体时，考虑
   game_promotion_interaction。
5. “上线”“现已上线”“即将上线”“回归”“来自测试服”“以正式服为准”只说明内容状态或
   信息阶段，不能单独作为 game_announcement 或 game_official_preview 的依据。多类内容混合时，
   判断正式事实是可独立成立的消息主体，还是服务于宣传表达；事实没有形成独立完整的正式告知，
   或实质说明不足时，优先考虑 game_promotion_interaction。

非官方信源以展示、引流、带货、宣传或参与引导为主要目的，且没有形成实用提醒或观点分析时，
考虑 game_community_promotion_interaction。

字段：
- scale：minor / standard / major，仅表示同一重要性档案内的影响规模。
- audience_region：国服内容用 cn；国服与其他服务器都会生效用 global；
  明确只影响外服、不影响国服用 international_only；信息不足用 unknown。
  Riot/X 来源不等于只影响外服，必须依据消息实际适用范围判断。
- competition_region：仅赛事使用 lpl/lck/international/other，非赛事用 none。
- prominence：涉及普通对象用 normal，知名队伍/选手用 notable，明星选手或顶级焦点队伍用 star。
  赛事知名度校准示例：Faker 属于 star；Chovy、Knight、Bin、TheShy、Caps，以及 T1、Gen.G、
  HLE、BLG、TES、JDG、AL、IG、G2 等知名选手或队伍，通常至少为 notable；当消息的核心就是
  明星选手、顶级焦点队伍或它们之间的焦点对局时，可为 star。示例不是封闭名单，也不能仅因正文
  顺带提到该对象就升档，必须是消息实质涉及的对象。
- skin_tier：仅新皮肤、新炫彩和国服新臻彩发布使用 standard / legendary /
  prestige_or_mythic / ultimate，非新外观发布使用 none；必须依据消息明确写出的档次，
  不得凭空推断。"臻彩"不是"至臻皮肤"，仅因臻彩名称不得标为 prestige_or_mythic。
- is_bulk_update：是否包含大量对象或批量新增。
- evidence：1-6条消息中的具体文本依据，不得编造。

注意：
- message_type 决定消息的信息价值层级，topics 决定内容影响领域；信源可靠性本身不调整特征。
- 常规赛须区分赛区；转会须识别明星对象。未确认、传闻、推广或讨论的性质已经体现在
  message_type 中，不得再通过 scale、prominence 等字段重复升降档。
- 当前输入没有历史对照，不得猜测“首次披露”或“重复消息”。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "content": content,
                "extracted_facts": extracted_facts,
                "known_classification": {
                    "products": products,
                    "content_form": content_form,
                },
                "source_context": source_context,
                "controlled_catalog": classification_catalog(
                    products=products,
                    source_kind=source_kind,
                ),
                "approved_rules": knowledge_rules or [],
            },
            max_tokens=900,
            schema=MessageClassificationImportanceResult,
            operation=IMPORTANCE_SCORING_OPERATION,
            business_validator=validate_classification,
        )

    async def aggregate_events(
        self,
        *,
        message: dict[str, object],
        possible_event_families: list[str],
        candidates: list[dict[str, object]],
    ) -> EventAggregationResult:
        candidate_by_id = {
            int(candidate["event_id"]): candidate for candidate in candidates
        }

        def validate_candidate_references(result: EventAggregationResult) -> str | None:
            if message.get("content_form") == "repost" and any(
                mention.action == "create" for mention in result.mentions
            ):
                return "repost 消息不能 create Event，只能 attach 或 ignore"
            for mention in result.mentions:
                if mention.action != "attach":
                    continue
                candidate = candidate_by_id.get(int(mention.event_id or 0))
                if candidate is None:
                    return (
                        f"mention[{mention.mention_index}] attach 只能引用本次输入的 "
                        "candidate event_id"
                    )
                if candidate.get("event_family") != mention.event_family:
                    return (
                        f"mention[{mention.mention_index}] attach 的 event_family 必须与"
                        "候选 Event 完全一致"
                    )
                candidate_products = {
                    str(value) for value in candidate.get("products") or []
                }
                if mention.product is not None and candidate_products != {str(mention.product)}:
                    return (
                        f"mention[{mention.mention_index}] attach 的 product 必须与候选 Event 完全一致"
                    )
            return None

        prompt = """你是 LeagueNews 的事件聚合编辑器。输入是一条已经完成翻译、摘要、分类和实体
提取的消息，以及由程序宽松召回的一组近期候选 Event。你的唯一核心任务是识别消息中的 0 到 N 个
有意义事件 mention，并为每个 mention 选择 attach、create 或 ignore。一次响应处理整条消息；不要
重做 OCR、翻译、消息分类、消息重要性或候选召回。

Event 是用户认知中的同一件现实世界事情，Event 的最终身份是 event_id。候选的 family、实体、
时间、标题和 recall_score 都只是帮助判断的上下文，不是确定性身份规则。你需要进行语义判断：
- attach：该 mention 是某个候选 Event 的继续、确认、否认、修正、佐证或复述。
- create：该 mention 是有意义的新现实状态变化，且没有合适候选。
- ignore：没有独立可跟踪的现实变化，或证据不足以形成/连接 Event。

possible_event_families 是由上游 products + topics 推导的 taxonomy 路由范围。create/attach 的
event_family 必须从这个范围中选择；不要重新猜测主题或消息类型。每个 create/attach mention
必须输出 product：单产品消息填该产品；跨产品消息必须逐个 mention 选择所属产品，不能把整条消息
的 products 原样复制到每个 Event。只有同一现实发展确实跨产品共享生命周期时，才把它判断为跨产品事件；
不同产品的外观、活动、玩法或资源应拆成各自的 mention。ignore 不需要 product。

事件粒度原则：
1. 持续更新同一核心主体、同一现实发展且共享生命周期的消息属于同一 Event。
2. 商品、奖励、组件、子内容和附件默认属于主 Event；不要仅因实体或 topic 数量拆分。
3. 只有拥有明显独立生命周期和后续更新路径的现实发展才拆成多个 Event。
4. 一条综合消息可以 attach 多个候选、create 其他 Event，并 ignore 非事件片段。
5. 候选召回追求高 recall，可能包含无关 Event；不要因为候选存在或分数较高就强行 attach。
6. preview、正式公告、后续更新、确认、否认和更正是否共享生命周期，由当前消息证据和候选上下文
   判断，不要依赖固定字段形状。
7. repost 消息不能 create Event；只能 attach 到已有事件或 ignore。转载本身不是新事件。
8. 对 game_leak/测试服版本物料消息，同一产品、同一 cosmetic_release 批次中的多个外观默认合并为一个
   create/attach mention，把各个外观放进 key_facts；不要因为列出多个皮肤就创建多个 Event。只有证据显示
   它们拥有独立生命周期、不同版本或不同发布活动时才拆分。

event_family 语义边界：
- gameplay_balance 是既有玩法/数值的调整；gameplay_release 是新英雄、模式、机制或玩法内容上线。
- cosmetic_release 是外观资产本身的发布；player_activity 是需要参与、兑换、领取或完成任务的活动，
  即使奖励是外观也不因此改成 cosmetic_release。
- commercial_offer 是商店、付费商品或限时销售变化；service_incident 是具体故障、热修或服务异常；
  platform_service 是平台能力或服务产品本身的发布/变化。
- esports_match 是一场具体比赛的完整生命周期，包括赛前确定的对阵与时间、进行中更新和赛果；
  esports_schedule 只用于赛事日历/赛程体系本身，或延期、改期、场地、对阵、赛制等实质安排变化，
  不能因为消息是赛前预告就把具体比赛改成 esports_schedule。
- roster_change 是选手/教练/阵容变动；esports_rules 是赛事规则和竞赛制度变化。
- universe_release、media_release、corporate_change、security_enforcement 按其字面现实变化使用；没有更
  合适 family 的命名发展才用 other_named_development。
- 编辑政策明确不跟踪例行免费英雄轮换/周免名单；这类消息 ignore。该例只用于模型语义边界，程序
  不通过文本规则拦截它。

输出规则：
- mention_index 从 0 连续递增。
- attach 必须引用 candidate_events 中的 event_id，且 event_family 与候选一致；候选 Event 的产品必须
  与 mention.product 完全一致。
- create 不引用 event_id，必须提供最小 new_event.title 和 new_event.summary。
  canonical_anchors 仅是可选描述/召回特征，不需要完整，也不得虚构。
- ignore 不引用 Event。
- create/attach 的 evidence_excerpt 必须来自当前消息。
- relation、source_role、materiality 描述当前 mention；非 material attach 不得提交 projection。
- attach 的 projection 可选，只用于当前 Event 的展示标题、摘要、最新进展或 key facts；它不能改变
  membership 决定。create 的初始展示字段放在 new_event。
- 展示字段使用简体中文。
只输出符合 schema 的 JSON。"""
        return await self._validated_json_completion(
            prompt=prompt,
            payload={
                "possible_event_families": possible_event_families,
                "message": message,
                "candidate_events": candidates,
            },
            max_tokens=3200,
            schema=EventAggregationResult,
            operation=EVENT_AGGREGATION_OPERATION,
            business_validator=validate_candidate_references,
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
                                f"上一次输出未通过结构或业务校验：{last_error}。"
                                "保留未被错误点名的合法 mentions，只修正对应片段。"
                                "相同 identity 的 create 必须合并；create 必须是 material_update；"
                                "缺少当前消息证据或完整 identity 的附属片段必须 ignore。"
                                "同时逐项对照 JSON Schema，补齐 required 字段并修正常量和枚举值，"
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
