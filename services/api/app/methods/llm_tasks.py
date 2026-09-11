"""Baseline domain tasks: prompts, input construction and business interpretation.

LLMClient retains delegating methods for existing translation/workflow consumers.
"""

from typing import cast
from app.methods.model_client import JsonCompletionClient
from app.domain.message_taxonomy import (
    SourceKind,
    classification_catalog,
    classification_error,
    content_analysis_catalog,
)
from app.methods.contracts import (
    MessageContentAnalysisResult,
    MessageClassificationImportanceResult,
)
from app.schemas.event_aggregation import EventAggregationResult
from app.prompts.registry import (
    CLASSIFICATION_OPERATION,
    IMPORTANCE_SCORING_OPERATION,
    EVENT_AGGREGATION_OPERATION,
)


class BaselineLLMTasks:
    async def analyze_message_content(
        self: JsonCompletionClient,
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
        self: JsonCompletionClient,
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
                "source_context.classification_source_kind must be official, unofficial, or unknown"
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
        self: JsonCompletionClient,
        *,
        message: dict[str, object],
        possible_event_families: list[str],
        candidates: list[dict[str, object]],
    ) -> EventAggregationResult:
        candidate_by_id = {int(candidate["event_id"]): candidate for candidate in candidates}

        def validate_candidate_references(result: EventAggregationResult) -> str | None:
            if message.get("content_form") == "repost" and any(
                mention.action == "create" for mention in result.mentions
            ):
                return (
                    "content_form=repost 不能 create Event；转载只能 attach 到候选 Event 或 ignore"
                )
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
                candidate_products = {str(value) for value in candidate.get("products") or []}
                if mention.product is not None and candidate_products != {str(mention.product)}:
                    return f"mention[{mention.mention_index}] attach 的 product 必须与候选 Event 完全一致"
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
的 products 原样复制到每个 Event。不同产品且生命周期独立的外观、活动、玩法或资源应拆成各自的 mention；
产品不同本身不能拆开一个共享生命周期的批次，此时选择公告的主要产品，其他产品条目保留在 key_facts。
ignore 不需要 product。

事件粒度原则：
1. 先识别当前消息实际推动或发布的主要发展，再按“独立生命周期”归组；每组至多输出一个 create/attach
   mention。共享同一发布批次、版本/系列、上线窗口、状态变化和后续更新路径的内容属于同一组。
2. 每组列出的商品、奖励、组件、修复、平衡项、皮肤或其他子内容都进入该 Event 的 key_facts；不要仅因
   条目、实体、topic、event_family 或产品数量拆分。同一批次发布的多款皮肤尤其不能逐皮肤建 Event。
3. 只有拥有独立名称、独立发布状态和独立后续更新路径的现实发展才拆成另一组。新英雄、新模式等可与
   版本的修复/平衡批次分开，但“影响对象不同”或“改动规模较大”本身不构成独立生命周期。
4. create 用于当前消息正在带来的新发展，不用于回填正文提及的所有历史事件。历史回顾、前序批次、引用
   材料和背景说明即使描述了过去真实发生的变化，只要本消息没有更新其状态，就不得据此 create Event，
   也不得按其中的日期、产品或条目拆分；可作为主要 Event 的 key_facts 或上下文。例如“本次发布 + 前次
   发布回顾”只处理本次发布；若输出前次发布片段，action 必须是 ignore，不能 create/attach。
5. 一条综合消息可以 attach 多个候选、create 其他 Event，并 ignore 非事件片段。
6. 候选召回追求高 recall，可能包含无关 Event；不要因为候选存在或分数较高就强行 attach。
7. preview、正式公告、后续更新、确认、否认和更正是否共享生命周期，由当前消息证据和候选上下文
   判断，不要依赖固定字段形状。
8. repost 消息只能 attach 到已有事件或 ignore；程序会拒绝 repost 的 create 结果。转载本身不是新事件。
9. 判断依据是现实生命周期，不是消息形态。补丁说明、更新公告、测试服爆料或综合报道都使用同一套归组规则；
   不得根据 message_type、固定数量上限或标题关键词机械决定 Event 数量。
10. attach 必须有当前消息与候选属于同一现实发展的正向证据，例如明确共享同一命名发布、版本/批次、
    具体故障、核心主体或连续状态。仅仅 event_family、产品、笼统的“更新/修复”语义相同，或候选列表中只有
    一个同 family Event，都不是 attach 依据。候选的核心主体、发布批次、日期或环境（如正式服与测试服）
    不同时应 create/ignore；不得用 projection 把候选改名成另一件事来代替 create。

典型边界（用于解释统一规则，不是关键词匹配）：
- 当前不停机更新公告末尾附带“昨天/前次更新”的修复记录：只为当前更新 create/attach；前次记录即使跨产品
  也只是回顾上下文，必须 ignore，不能回填 Event，更不能逐 BUG 建 Event。
- 大版本公告同时包含一批英雄/装备平衡、新英雄、新模式和同批系列皮肤：平衡条目归入一个版本调整 Event；
  新英雄和新模式若各有独立发布生命周期可分别成为 Event；同批系列皮肤只形成一个 cosmetic_release Event，
  每款皮肤进入 key_facts，不能逐皮肤建 Event。

event_family 语义边界：
- gameplay_balance 是既有玩法/数值的调整；gameplay_release 是新英雄、模式、机制或玩法内容上线。
- cosmetic_release 是外观资产本身的发布；player_activity 是需要参与、兑换、领取或完成任务的活动，
  即使奖励是外观也不因此改成 cosmetic_release。
- commercial_offer 是商店、付费商品或限时销售变化；service_incident 是具体故障、热修或服务异常；
  platform_service 是平台能力或服务产品本身的发布/变化。
- esports_match 是一场具体比赛的完整生命周期，包括赛前确定的对阵与时间、进行中更新和赛果；
  esports_schedule 只用于赛事日历/赛程体系本身，或延期、改期、场地、对阵、赛制等实质安排变化，
  不能因为消息是赛前预告就把具体比赛改成 esports_schedule。
- esports_match 表示一场具体比赛的实际过程和结果。同一场比赛的开始、比分推进、比赛过程、
  最终赛果和胜负通常发生在同一天或相隔很短的时间内，应优先视为同一 Event 的连续发展：
  例如 BLG 1:0 TES → BLG 1:1 TES → BLG 2:1 TES → BLG 2:1 TES 比赛结束，应 attach 到同一个
  比赛 Event，不能因为比分变化不断 create。但如果消息与候选比赛已经相隔明显较长时间，
  特别是已经跨天较久，结合当前上下文更像同两支队伍进行的另一场比赛，则应考虑这是新的比赛
  occurrence 并 create 新 Event。时间只是语义判断的重要信号：同两支队伍可以多次交手，
  participants 相同不代表永远是同一个 Event，比分变化本身也不代表新 Event；请结合候选标题、
  摘要、时间、双方和赛事上下文做正常语义判断。
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
- relation、source_role、materiality 描述当前 mention。只有 materiality=material_update 的 attach 才能
  提交 projection；materiality=corroboration_only、duplicate 或 context_only 时 projection 必须为 null
  或省略，不能同时输出任何标题、摘要、最新进展或 key facts 更新。
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
