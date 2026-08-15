from dataclasses import dataclass
from typing import Final, Literal, get_args


CLASSIFICATION_VERSION: Final = "message-taxonomy-v4"

Product = Literal[
    "lol_pc",
    "tft",
    "lol_esports",
    "lol_universe",
    "other_lol_product",
    "riot_ecosystem",
    "unknown",
]
ContentForm = Literal["original", "repost", "quote", "media_only", "link_only"]
SourceKind = Literal["official", "unofficial", "unknown"]
MessageType = Literal[
    "game_patch_notes",
    "game_official_preview",
    "game_announcement",
    "game_notice",
    "game_promotion_interaction",
    "game_community_notice",
    "game_community_promotion_interaction",
    "game_leak",
    "game_community_discussion",
    "esports_announcement",
    "esports_promotion_interaction",
    "esports_rumor_speculation",
    "esports_community_discussion",
    "esports_community_promotion_interaction",
    "lol_universe_announcement",
    "lol_universe_promotion_interaction",
    "lol_universe_leak",
    "lol_universe_community_discussion",
    "other_lol_product_announcement",
    "other_lol_product_promotion_interaction",
    "other_lol_product_leak",
    "other_lol_product_community_discussion",
    "riot_ecosystem_announcement",
    "riot_ecosystem_promotion_interaction",
    "riot_ecosystem_leak",
    "riot_ecosystem_community_discussion",
    "unknown",
]
Topic = Literal[
    "balance_gameplay",
    "champions",
    "items_runes_systems",
    "game_modes",
    "gameplay",
    "service_technical",
    "cosmetics",
    "shop_monetization",
    "activities_rewards",
    "security_fair_play",
    "community",
    "guides_education",
    "tft_gameplay",
    "esports_competition",
    "esports_schedule",
    "esports_matches",
    "esports_rosters",
    "esports_analysis",
    "esports_broadcast",
    "esports_fandom_live",
    "lore_universe",
    "media_entertainment",
    "merchandise_collectibles",
    "corporate_partnerships",
    "platform_services",
    "unknown",
]

PRODUCTS: Final = tuple(get_args(Product))
CONTENT_FORMS: Final = frozenset(get_args(ContentForm))
MESSAGE_TYPE_ORDER: Final = tuple(get_args(MessageType))
MESSAGE_TYPES: Final = frozenset(MESSAGE_TYPE_ORDER)
TOPICS: Final = frozenset(get_args(Topic))


@dataclass(frozen=True, slots=True)
class ProductRule:
    code: Product
    name: str
    definition: str


@dataclass(frozen=True, slots=True)
class ContentFormRule:
    code: ContentForm
    name: str
    definition: str


@dataclass(frozen=True, slots=True)
class MessageTypeRule:
    code: MessageType
    name: str
    products: frozenset[Product]
    source: Literal["official", "unofficial", "any"]
    definition: str


@dataclass(frozen=True, slots=True)
class TopicRule:
    code: Topic
    name: str
    products: frozenset[Product]
    definition: str


_GAME_PRODUCTS = frozenset({"lol_pc", "tft"})

PRODUCT_RULES: Final = (
    ProductRule(
        "lol_pc",
        "英雄联盟 PC",
        "英雄联盟 PC 端游戏本体、客户端、版本改动、英雄、装备、符文、玩法、模式、皮肤、商城和游戏内活动；也包括针对端游未来改动的开发者报告、开发日志和路线图。",
    ),
    ProductRule(
        "tft",
        "云顶之弈",
        "云顶之弈的赛季、套装、棋子、羁绊、强化符文、玩法、模式、外观、商城和活动。",
    ),
    ProductRule(
        "lol_esports",
        "英雄联盟电竞",
        "英雄联盟职业赛事及其生态，包括联赛、赛程、比赛、战队、选手、转会、赛事规则、转播和现场活动。",
    ),
    ProductRule(
        "lol_universe",
        "英雄联盟宇宙",
        "符文之地世界观、角色故事、背景设定、叙事内容，以及以该宇宙为核心的动画、影视、音乐或其他创意作品。",
    ),
    ProductRule(
        "other_lol_product",
        "其他英雄联盟相关产品",
        "与英雄联盟 IP 直接相关但不属于前述产品的内容，例如英雄联盟手游、符文之地传说、2XKO、Riftbound（裂界征伐）等。文本明确出现具体产品名时，即使内容复用英雄、皮肤或世界观元素，也按该具体产品判断。",
    ),
    ProductRule(
        "riot_ecosystem",
        "Riot 生态与关联业务",
        "Riot 公司事务、招聘合作、开放平台与 API、账户和跨产品基础设施，以及周边、收藏品、媒体和其他平台业务。",
    ),
    ProductRule(
        "unknown",
        "产品未知",
        "可读证据不足以判断产品；不表示无关消息。",
    ),
)

CONTENT_FORM_RULES: Final = (
    ContentFormRule("original", "原创发布", "发布者直接发布，正文或标题包含可处理的语义内容。"),
    ContentFormRule("repost", "转发", "转发或重新发布他人消息，自身没有实质性新增内容。"),
    ContentFormRule("quote", "引用发布", "引用另一条消息，并附加可独立理解的新文字、判断或补充。"),
    ContentFormRule(
        "media_only",
        "仅媒体",
        "标题与正文都没有足够可读语义，只有图片、视频或其他媒体。标题是当前消息证据；若仅根据标题就能忠实概括消息的对象与事项、生成非空摘要，则已有可处理语义，不属于仅媒体。",
    ),
    ContentFormRule("link_only", "仅链接", "只有链接或嵌入地址，没有成功提取到可读正文。"),
)

MESSAGE_TYPE_RULES: Final = (
    MessageTypeRule(
        "game_patch_notes",
        "版本说明",
        _GAME_PRODUCTS,
        "official",
        "版本更新的正式说明，通常包含大量当前或下个版本的具体改动内容。",
    ),
    MessageTypeRule(
        "game_official_preview",
        "官方预览",
        _GAME_PRODUCTS,
        "official",
        "以实质信息披露为主要目的，对未来内容提供多项具体机制、数值、规则、玩法或开发说明。仅介绍单个英雄、棋子、皮肤或活动，只有上线日期、宣传文案、视频，或附带‘测试服、以正式服为准’等免责声明时，不属于官方预览。",
    ),
    MessageTypeRule(
        "game_announcement",
        "游戏公告",
        _GAME_PRODUCTS,
        "official",
        "官方对已确定游戏事项的正式告知，以明确的开放时间、适用范围、参与或获取方式、规则和安排等可独立核验的信息为主体。仅在宣传短帖或视频中提到内容上线、回归或到来，不属于游戏公告。",
    ),
    MessageTypeRule(
        "game_notice",
        "游戏通知",
        _GAME_PRODUCTS,
        "official",
        "不停机更新、Bug 热修复、服务状态、运营调整和其他面向玩家的运行通知。",
    ),
    MessageTypeRule(
        "game_promotion_interaction",
        "游戏推广与互动",
        _GAME_PRODUCTS,
        "official",
        "以展示、预热、吸引关注、唤起回忆或参与引导为主要目的的短帖、视频和互动内容，包括英雄、棋子、皮肤、模式、活动和游戏创作宣传。可以包含确定的上线时间、现已上线、回归、少量新内容或测试服免责声明；这些事实服务于宣传表达、没有形成独立完整的正式告知时，仍属于推广互动。",
    ),
    MessageTypeRule(
        "game_community_notice",
        "游戏社区通知",
        _GAME_PRODUCTS,
        "unofficial",
        "社区对神话商店轮换、活动时间、可领取活动奖励、活动口令码、游戏 Bug 和其他限时内容的提醒与整理。",
    ),
    MessageTypeRule(
        "game_leak",
        "游戏爆料",
        _GAME_PRODUCTS,
        "unofficial",
        "非官方渠道披露的测试服（PBE）内容或其他未由官方确认的游戏信息，包括测试服改动、PBE 改动、平衡性调整、活动、模式、新英雄和游戏资产等。",
    ),
    MessageTypeRule(
        "game_community_discussion",
        "游戏讨论",
        _GAME_PRODUCTS,
        "unofficial",
        "社区对活动、皮肤、英雄、版本改动、模式、玩法和其他游戏内容的讨论、分析、评价与观点表达。",
    ),
    MessageTypeRule(
        "game_community_promotion_interaction",
        "游戏社区推广与互动",
        _GAME_PRODUCTS,
        "unofficial",
        "非官方账号以展示、引流、带货、宣传或参与引导为主要目的，传播游戏活动、皮肤、模式、视频、直播和社区创作等内容。可以包含上线日期或少量新内容；没有形成实用提醒或观点分析时，仍属于社区推广互动。",
    ),
    MessageTypeRule(
        "esports_announcement",
        "赛事公告",
        frozenset({"lol_esports"}),
        "official",
        "官方发布的赛程安排、赛果、选手转会结果和其他赛事正式消息。",
    ),
    MessageTypeRule(
        "esports_promotion_interaction",
        "赛事推广与互动",
        frozenset({"lol_esports"}),
        "official",
        "赛事集锦、采访、赛事宣传、观赛引导和官方互动内容。",
    ),
    MessageTypeRule(
        "esports_rumor_speculation",
        "赛事传闻与推测",
        frozenset({"lol_esports"}),
        "unofficial",
        "对选手转会、队伍变化、赛事安排或其他电竞事项的传言、推测和未确认说法。",
    ),
    MessageTypeRule(
        "esports_community_discussion",
        "赛事社区讨论",
        frozenset({"lol_esports"}),
        "unofficial",
        "社区对选手的评价、赛事过程和结果的讨论、分析与观点表达。",
    ),
    MessageTypeRule(
        "esports_community_promotion_interaction",
        "赛事社区推广与互动",
        frozenset({"lol_esports"}),
        "unofficial",
        "非官方账号围绕电竞赛事、直播、观赛、战队选手或赛事活动，以展示、宣传、引流、抽奖、福利、带话题参与或其他参与引导为主要目的的内容；主体不是未确认消息披露、赛事分析或观点讨论。",
    ),
    MessageTypeRule(
        "lol_universe_announcement",
        "英雄联盟宇宙公告",
        frozenset({"lol_universe"}),
        "official",
        "官方发布的世界观设定、角色故事、动画影视、音乐项目、合作或上线安排等正式消息。",
    ),
    MessageTypeRule(
        "lol_universe_promotion_interaction",
        "英雄联盟宇宙推广与互动",
        frozenset({"lol_universe"}),
        "official",
        "对世界观内容、动画影视、音乐、艺术作品或相关创作的官方宣传、展示和互动引导。",
    ),
    MessageTypeRule(
        "lol_universe_leak",
        "英雄联盟宇宙爆料",
        frozenset({"lol_universe"}),
        "unofficial",
        "非官方渠道披露的未确认角色故事、世界观设定、动画影视、音乐或其他叙事项目内容。",
    ),
    MessageTypeRule(
        "lol_universe_community_discussion",
        "英雄联盟宇宙社区讨论",
        frozenset({"lol_universe"}),
        "unofficial",
        "社区对世界观、角色关系、剧情、动画影视、音乐和其他叙事内容的解读、分析与评价。",
    ),
    MessageTypeRule(
        "other_lol_product_announcement",
        "其他英雄联盟产品公告",
        frozenset({"other_lol_product"}),
        "official",
        "官方发布的英雄联盟手游、符文之地传说、2XKO、Riftbound（裂界征伐）等产品上线、更新、活动或运营消息。",
    ),
    MessageTypeRule(
        "other_lol_product_promotion_interaction",
        "其他英雄联盟产品推广与互动",
        frozenset({"other_lol_product"}),
        "official",
        "对其他英雄联盟相关产品、内容、活动或创作的官方宣传、展示和互动引导。",
    ),
    MessageTypeRule(
        "other_lol_product_leak",
        "其他英雄联盟产品爆料",
        frozenset({"other_lol_product"}),
        "unofficial",
        "非官方渠道披露的未确认产品更新、新内容、活动安排、发布日期或游戏资产。",
    ),
    MessageTypeRule(
        "other_lol_product_community_discussion",
        "其他英雄联盟产品社区讨论",
        frozenset({"other_lol_product"}),
        "unofficial",
        "社区对其他英雄联盟相关产品的玩法、内容、运营和产品表现进行讨论、分析与评价。",
    ),
    MessageTypeRule(
        "riot_ecosystem_announcement",
        "Riot 生态与关联业务公告",
        frozenset({"riot_ecosystem"}),
        "official",
        "官方发布的公司事务、招聘合作、开放平台、账户或跨产品基础设施、周边媒体及其他平台业务消息。",
    ),
    MessageTypeRule(
        "riot_ecosystem_promotion_interaction",
        "Riot 生态与关联业务推广与互动",
        frozenset({"riot_ecosystem"}),
        "official",
        "Riot 品牌活动、合作项目、周边媒体、开发者平台或其他业务的官方宣传、展示和互动引导。",
    ),
    MessageTypeRule(
        "riot_ecosystem_leak",
        "Riot 生态与关联业务爆料",
        frozenset({"riot_ecosystem"}),
        "unofficial",
        "非官方渠道披露的未确认 Riot 公司动向、合作、招聘、平台服务、周边媒体或其他业务信息。",
    ),
    MessageTypeRule(
        "riot_ecosystem_community_discussion",
        "Riot 生态与关联业务社区讨论",
        frozenset({"riot_ecosystem"}),
        "unofficial",
        "社区对 Riot 公司政策、平台服务、品牌合作、周边媒体和其他业务的讨论、分析与评价。",
    ),
    MessageTypeRule(
        "unknown",
        "类型未知",
        frozenset(PRODUCTS),
        "any",
        "证据不足，或产品与信源确定后仍没有合适的消息类型。",
    ),
)

TOPIC_RULES: Final = (
    TopicRule(
        "balance_gameplay",
        "游戏平衡",
        frozenset({"lol_pc", "tft", "other_lol_product"}),
        "游戏中玩法对象的强度调整、数值改动和平衡目标。",
    ),
    TopicRule(
        "champions",
        "英雄",
        frozenset({"lol_pc"}),
        "英雄发布、技能、设计、重做、背景在游戏中的呈现及相关游戏内容。",
    ),
    TopicRule(
        "items_runes_systems",
        "装备、符文与战斗系统",
        frozenset({"lol_pc"}),
        "装备、符文、召唤师技能、地图资源、目标机制和构筑系统。",
    ),
    TopicRule(
        "game_modes",
        "游戏模式",
        frozenset({"lol_pc", "tft", "other_lol_product"}),
        "产品中的模式、规则变体、轮换模式和其他特殊玩法。",
    ),
    TopicRule(
        "gameplay",
        "玩法与策略",
        frozenset({"lol_pc", "other_lol_product"}),
        "操作与玩法机制、打法、战术、Meta、对局体验及非数值型玩法讨论。",
    ),
    TopicRule(
        "service_technical",
        "服务与技术",
        frozenset({"lol_pc", "tft", "other_lol_product", "riot_ecosystem"}),
        "Bug、修复、客户端、服务器、平台维护、兼容性、性能、基础设施和服务可用性。",
    ),
    TopicRule(
        "cosmetics",
        "外观内容",
        frozenset({"lol_pc", "tft", "other_lol_product"}),
        "皮肤、炫彩、小小英雄、棋盘、图标、表情、特效、卡面和其他数字个性化资产。",
    ),
    TopicRule(
        "shop_monetization",
        "商店与商业化",
        frozenset({"lol_pc", "tft", "other_lol_product"}),
        "商店轮换、价格、礼包、通行证、货币、抽取、购买方式和商业化机制。",
    ),
    TopicRule(
        "activities_rewards",
        "活动与奖励",
        frozenset({"lol_pc", "tft", "other_lol_product"}),
        "游戏活动、任务、登录奖励、可领取奖励、兑换码、通行证或活动进度。",
    ),
    TopicRule(
        "security_fair_play",
        "安全与公平竞技",
        frozenset({"lol_pc", "tft", "other_lol_product"}),
        "反作弊、封禁、处罚、账号安全、消极行为治理和公平竞技政策。",
    ),
    TopicRule(
        "community",
        "社区创作与文化",
        frozenset(PRODUCTS[:-1]),
        "玩家或粉丝创作、同人、Cosplay、创作者合作及围绕产品、赛事、IP 或品牌形成的社区文化。",
    ),
    TopicRule(
        "guides_education",
        "攻略与教学",
        frozenset({"lol_pc", "tft", "other_lol_product"}),
        "面向玩家的教程、入门说明、机制教学、打法或阵容指南和知识整理。",
    ),
    TopicRule(
        "tft_gameplay",
        "赛季与核心玩法",
        frozenset({"tft"}),
        "云顶之弈的赛季或套装、棋子、羁绊、强化符文、装备、阶段机制和阵容构筑。",
    ),
    TopicRule(
        "esports_competition",
        "赛事与赛制",
        frozenset({"lol_esports"}),
        "联赛、杯赛、参赛资格、分组、赛制、规则、排名和赛事组织。",
    ),
    TopicRule(
        "esports_schedule",
        "赛程",
        frozenset({"lol_esports"}),
        "比赛日期、比赛预告、首发阵容安排、开赛时间、对阵安排、抽签和赛程变更。",
    ),
    TopicRule(
        "esports_matches",
        "比赛与赛果",
        frozenset({"lol_esports"}),
        "具体比赛的过程、比分、胜负、晋级和淘汰结果。",
    ),
    TopicRule(
        "esports_rosters",
        "战队与人员",
        frozenset({"lol_esports"}),
        "选手、教练、战队阵容、转会、签约、离队和人员变动。",
    ),
    TopicRule(
        "esports_analysis",
        "赛事表现与分析",
        frozenset({"lol_esports"}),
        "选手或战队表现、比赛复盘、战术、版本环境和赛事观点。",
    ),
    TopicRule(
        "esports_broadcast",
        "赛事内容与转播",
        frozenset({"lol_esports"}),
        "采访、集锦、节目、直播、解说内容和赛事媒体制作。",
    ),
    TopicRule(
        "esports_fandom_live",
        "观赛与粉丝活动",
        frozenset({"lol_esports"}),
        "门票、现场活动、观赛派对、粉丝互动和赛事周边体验。",
    ),
    TopicRule(
        "lore_universe",
        "世界观与叙事",
        frozenset({"lol_universe"}),
        "符文之地设定、角色故事、剧情、时间线、地区和叙事关系。",
    ),
    TopicRule(
        "media_entertainment",
        "影视、音乐与创意媒体",
        frozenset({"lol_universe", "other_lol_product", "riot_ecosystem"}),
        "与英雄联盟相关 IP、其他相关产品或 Riot 直接相关的动画、影视、音乐、视频、配音、艺术和其他创意媒介。",
    ),
    TopicRule(
        "merchandise_collectibles",
        "周边与收藏品",
        frozenset({"other_lol_product", "riot_ecosystem"}),
        "Riot 及其 IP 的实体卡牌、收藏盒、模型、服饰、授权商品和其他实体周边。",
    ),
    TopicRule(
        "corporate_partnerships",
        "公司与合作",
        frozenset({"riot_ecosystem"}),
        "Riot 公司事务、组织与招聘、商业合作、品牌合作和企业活动。",
    ),
    TopicRule(
        "platform_services",
        "平台与跨产品服务",
        frozenset({"riot_ecosystem"}),
        "Riot 账户、开发者平台、API、跨产品基础设施、数据服务和平台政策。",
    ),
    TopicRule(
        "unknown", "主题未知", frozenset(PRODUCTS), "原始内容不足以判断主题，或没有适合的稳定主题。"
    ),
)

MESSAGE_TYPE_RULES_BY_CODE: Final = {
    code: tuple(rule for rule in MESSAGE_TYPE_RULES if rule.code == code)
    for code in MESSAGE_TYPE_ORDER
}
TOPIC_RULE_BY_CODE: Final = {rule.code: rule for rule in TOPIC_RULES}
TOPIC_ORDER: Final = tuple(rule.code for rule in TOPIC_RULES)


def content_analysis_error(*, products: list[str], content_form: str) -> str | None:
    if not products or len(products) > 3 or len(products) != len(set(products)):
        return "products 必须包含 1-3 个不重复值"
    if any(product not in PRODUCTS for product in products):
        return "products 包含不受支持的值"
    if products != [product for product in PRODUCTS if product in products]:
        return "products 必须按受控目录顺序输出"
    if "unknown" in products and products != ["unknown"]:
        return "unknown product 不能与其他产品并列"
    if content_form not in CONTENT_FORMS:
        return "content_form 包含不受支持的值"
    if content_form in {"media_only", "link_only"} and products != ["unknown"]:
        return "纯媒体或纯链接消息的 products 必须为 unknown"
    return None


def message_content_error(
    *,
    products: list[str],
    content_form: str,
    title: str,
    summary: str,
    entities: list[object],
) -> str | None:
    if error := content_analysis_error(products=products, content_form=content_form):
        return error
    if content_form in {"media_only", "link_only"}:
        if summary.strip() or entities:
            return "纯媒体或纯链接消息的摘要和实体必须为空"
        return None
    if not title.strip():
        return "可处理消息必须生成标题"
    if not summary.strip():
        return "可处理消息必须生成摘要"
    return None


def classification_error(
    *,
    products: list[str],
    content_form: str,
    message_type: str,
    topics: list[str],
    source_kind: str,
) -> str | None:
    if source_kind not in {"official", "unofficial", "unknown"}:
        return "source_kind 包含不受支持的值"
    if error := content_analysis_error(products=products, content_form=content_form):
        return error
    if message_type not in MESSAGE_TYPES:
        return "message_type 包含不受支持的值"
    if not topics or len(topics) != len(set(topics)):
        return "topics 必须包含至少一个不重复值"
    if any(topic not in TOPICS for topic in topics):
        return "topics 包含不受支持的值"
    if topics != [topic for topic in TOPIC_ORDER if topic in topics]:
        return "topics 必须按受控目录顺序输出"
    if "unknown" in topics and topics != ["unknown"]:
        return "unknown topic 不能与其他主题并列"
    if content_form in {"media_only", "link_only"}:
        if message_type != "unknown" or topics != ["unknown"]:
            return "纯媒体或纯链接消息的 message_type 和 topics 必须为 unknown"
        return None
    selected_products = set(products)
    message_rules = MESSAGE_TYPE_RULES_BY_CODE[message_type]
    expected_source = source_kind if source_kind != "unknown" else None
    if message_type != "unknown":
        product_rules = [
            rule for rule in message_rules if selected_products.intersection(rule.products)
        ]
        if not product_rules:
            return "message_type 不适用于所选 products"
        if expected_source is not None and not any(
            rule.source in {"any", expected_source} for rule in product_rules
        ):
            return "message_type 不适用于当前信源性质"
    for topic in topics:
        if topic != "unknown" and not selected_products.intersection(
            TOPIC_RULE_BY_CODE[topic].products
        ):
            return f"topic={topic} 不适用于所选 products"
    return None


def content_analysis_catalog() -> dict[str, object]:
    return {
        "classification_version": CLASSIFICATION_VERSION,
        "products": [
            {"code": rule.code, "name": rule.name, "definition": rule.definition}
            for rule in PRODUCT_RULES
        ],
        "content_forms": [
            {"code": rule.code, "name": rule.name, "definition": rule.definition}
            for rule in CONTENT_FORM_RULES
        ],
    }


def classification_catalog(
    *, products: list[str], source_kind: SourceKind
) -> dict[str, object]:
    selected_products = set(products)
    disclosed_sources = (
        {"official", "unofficial", "any"}
        if source_kind == "unknown"
        else {source_kind, "any"}
    )
    return {
        "classification_version": CLASSIFICATION_VERSION,
        "selected_products": products,
        "source_kind": source_kind,
        "message_types": [
            {
                "code": rule.code,
                "name": rule.name,
                "products": [product for product in PRODUCTS if product in rule.products],
                "source": rule.source,
                "definition": rule.definition,
            }
            for rule in MESSAGE_TYPE_RULES
            if rule.code == "unknown"
            or (
                bool(selected_products.intersection(rule.products))
                and rule.source in disclosed_sources
            )
        ],
        "topics": [
            {
                "code": rule.code,
                "name": rule.name,
                "products": [product for product in PRODUCTS if product in rule.products],
                "definition": rule.definition,
            }
            for rule in TOPIC_RULES
            if rule.code == "unknown" or bool(selected_products.intersection(rule.products))
        ],
    }
