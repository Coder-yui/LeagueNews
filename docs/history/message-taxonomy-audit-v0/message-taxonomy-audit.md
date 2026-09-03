# Message Taxonomy Audit v1

## 结论

本次对本地开发环境的只读全量 RawItem 导出进行了逐条审计：`737` 条，其中 `715` 条正常分类、`22` 条 `review_needed` （2.99%）。推荐 `8` 个 Product 枚举、`18` 个单选 MessageType，以及 `22` 个稳定 Topic 加 `other`/`unknown` 回退值。

该文档和同目录 JSON/JSONL 都是分析建议，不被生产代码读取。本轮未修改数据库、迁移、消息处理、Prompt 或事件聚合逻辑。

## 范围与方法

- 扫描对象：所有 `737` 条历史 `RawItem`，包含 `706` 条修订头和 `31` 条历史修订；修订没有从统计或审计文件中删除。
- 数据字段：`approved_classification`, `approved_translation`, `author_name`, `canonical_url`, `content_blocks`, `content_hash`, `content_kind`, `external_id`, `id`, `ingested_at`, `language`, `native_title`, `normalized`, `published_at`, `revision`, `source`, `supersedes_raw_item_id`。分类实际读取 `native_title` 与 `content_blocks`；批准译文仅用于中文阅读和歧义消解；现有 normalized/classification 仅作背景核对，不作为本次分类答案。
- 内容类型：`post` 594、`thread` 101、`article` 42；语言：`zh-CN` 403、`en` 318、`zxx` 6、`tl` 2、`cy` 2、`de` 2、`da` 1、`es` 1、`qme` 1、`in` 1。
- 方法：先使用完整 737 条消息的开放归纳缓存保留逐条语义判断，再合并同义词、清除跨轴污染、限制长文档的 incidental tags，并对全部记录用收敛规则回测。没有再次向外部模型发送语料。
- 标准：不根据发布账号直接决定产品或类型；可读标题足以支持高层分类时不因正文短而过度复核；只有链接、不可读媒体或确实缺乏语义时保留 `unknown` 和 `review_needed`。

## 数据质量

| 连接器 | RawItem 数 |
| --- | --- |
| x_twitter | 314 |
| weibo | 280 |
| baidu_tieba | 101 |
| tencent_lol | 22 |
| riot_official | 20 |

| 来源 | RawItem 数 |
| --- | --- |
| Spideraxe (@Spideraxe30) | 133 |
| SkinSpotlights (@SkinSpotlights) | 70 |
| lol半价吧 · 小老鼠小伟 | 65 |
| 英雄联盟 | 64 |
| _尧阿尧y_ | 64 |
| 英雄联盟赛事 | 57 |
| 召唤师Park | 54 |
| League of Legends (@LeagueofLegends) | 46 |
| LoL Esports (@lolesports) | 44 |
| 恋恋红茶_244 | 41 |
| lol半价吧 · 凤舞天_惊鸿恋 | 36 |
| 腾讯英雄联盟官方网站 | 22 |
| Matt Leung-Harrison (@RiotPhroxzon) | 21 |
| Riot Games Official | 20 |

- 无可读正文：`280`, `695`, `696`。
- 明显截断/仅提示查看全文：`31`, `41`, `44`, `47`, `49`, `368`, `371`, `523`, `684`。这些记录仍保留，标题足以提供高层主题时照常分类。
- 明确仅链接：`411`、`741`、`748`；仅媒体：`280`、`695`、`696`。
- 修订关系共有 30 条链，唯一三节点链为 `65 -> 122 -> 148`。URL 相同组和文本相同组主要反映修订或重复抓取；另发现跨来源完全相同文本组 `123/242`、`394/396`、`504/513`、`726/727`，以及疑似镜像组 `370/375`、`369/374`、`373/389`、`419/429`。所有这些记录均保留在 737 条审计中。

## 开放归纳与收敛

第一轮共出现 `45` 个临时 Product 标签、`160` 个临时 MessageType 标签和 `539` 个临时 Topic 标签。它们包含同义词、版本/赛区/实体、渠道名、事件状态及转发形式，不能直接成为运行时枚举。

收敛后的关键决定：

- `products` 只保留游戏、产品或 Riot 业务域；LPL/LCS/Classic/PBE/平台名不再污染产品轴。
- `message_type` 描述整条发布的文档性质或主要意图；皮肤、英雄、活动、热修复结果不作为类型。
- `topics` 是平铺多选内容领域；长版本说明最多保留 5 个实质主题，避免因为文末顺带提及而堆标签。
- `repost`/`quote` 的 115 个可见转发语法不再占用 `message_type`，而是进入下述分析专用 `content_form`。

## 分类契约

- `products`：多选，允许空数组。空数组表示明确的外部内容或不承载产品语义的个人表达；不是不确定性标记。
- `message_type`：必须且只取一个值。
- `topics`：多选且不允许空数组。可理解但不属于稳定主题时用 `other`；证据不足时用 `unknown` 并同时设 `review_needed=true`。
- `unknown` 与 `other`：前者是证据缺失，后者是语义已理解但体系外；`unknown` 不能成为常规吸收桶。
- 多选数组按词表固定顺序输出，顺序没有优先级含义。新增 code 必须在后续版本化全量审计中证明有稳定定义、边界、代表记录及不可替代用途。

## Products 统计

空 Product 数组：`60`（8.14%）；单产品：`565`；多产品：`112`（15.2%）。

| 高频跨产品组合 | 记录数 |
| --- | --- |
| lol_pc + tft | 24 |
| lol_pc + lol_esports | 24 |
| lol_pc + other_riot_product | 15 |
| lol_esports + riot_corporate | 8 |
| lol_pc + riot_merchandise_media | 7 |
| lol_pc + lol_universe | 6 |
| tft + lol_esports | 5 |
| tft + riot_corporate | 5 |
| lol_pc + tft + lol_esports | 3 |
| lol_pc + riot_corporate | 3 |

## Products 推荐表
### `lol_pc` - 英雄联盟端游
- 定义：《英雄联盟》PC 端游戏本体及其客户端、玩法、英雄、模式和游戏内内容。
- 命中条件：正文明确讨论端游玩法、版本、英雄、模式、皮肤、商城或客户端内容。
- 排除条件：只讨论职业赛事时使用 lol_esports；只讨论云顶时使用 tft。
- 全量命中：336 条（45.59%）；代表记录：`7`, `8`, `10`, `12`, `14`
- 易混淆：`lol_esports`, `tft`, `lol_universe`
- 边界规则：赛事报道不因使用英雄联盟比赛项目自动附加 lol_pc；明确讨论游戏改动时才多选。

### `tft` - 云顶之弈
- 定义：《云顶之弈》游戏、赛季、棋子、羁绊、强化符文、模式与外观内容。
- 命中条件：正文明确出现云顶之弈、TFT、赛季/套装、棋子或云顶玩法。
- 排除条件：仅在英雄联盟端游消息中顺带提到云顶奖励时，除非奖励或玩法本身是消息实质。
- 全量命中：72 条（9.77%）；代表记录：`28`, `29`, `40`, `45`, `48`
- 易混淆：`lol_pc`, `other_riot_product`
- 边界规则：同一活动明确同时发布端游和云顶内容时同时标注 tft 与 lol_pc。

### `lol_esports` - 英雄联盟电竞
- 定义：英雄联盟职业赛事、联赛、战队、选手、赛程、转会、转播和现场活动。
- 命中条件：消息的实质是比赛、赛事组织、职业队伍或职业人员。
- 排除条件：一般端游玩法、皮肤或版本说明不因提到 MSI、职业选手而自动属于电竞。
- 全量命中：301 条（40.84%）；代表记录：`9`, `11`, `20`, `21`, `22`
- 易混淆：`lol_pc`, `riot_corporate`
- 边界规则：以消息主张的对象判断：比赛/职业生态为 lol_esports，游戏更新为 lol_pc。

### `lol_universe` - 英雄联盟宇宙
- 定义：符文之地世界观、角色故事、叙事设定及以该宇宙为核心的影视内容。
- 命中条件：正文主要讨论世界观、角色叙事、设定、动画或衍生故事。
- 排除条件：仅展示可购买皮肤或游戏资产时，使用 lol_pc/cosmetics。
- 全量命中：7 条（0.95%）；代表记录：`140`, `305`, `306`, `307`, `404`
- 易混淆：`lol_pc`, `media_entertainment`
- 边界规则：叙事对象是主语才标 lol_universe；宣传素材中的背景文案不足以触发。

### `riot_corporate` - Riot 公司与平台业务
- 定义：Riot 的公司、开发者平台、招聘、合作、账户或跨产品服务事项。
- 命中条件：正文实质是公司政策、合作关系、开发者/账户平台能力或企业活动。
- 排除条件：只是官方账号发布具体游戏内容时，不因发布者是 Riot 而标注。
- 全量命中：23 条（3.12%）；代表记录：`17`, `84`, `114`, `169`, `184`
- 易混淆：`lol_pc`, `other_riot_product`
- 边界规则：公司级事实可与受影响游戏产品并存，账号身份本身不能触发此标签。

### `riot_merchandise_media` - Riot 周边与媒体业务
- 定义：与英雄联盟/Riot IP 相关的实体周边、收藏卡、音乐、视频和授权媒体商品。
- 命中条件：正文主要讨论实体商品、收藏品、音乐或媒体商业发行。
- 排除条件：游戏内皮肤、礼包和商城售卖归 lol_pc/tft 与相应 topic。
- 全量命中：11 条（1.49%）；代表记录：`46`, `59`, `162`, `331`, `332`
- 易混淆：`lol_pc`, `lol_universe`, `other_riot_product`
- 边界规则：以交付物是否为游戏外商品或媒体作品区分。

### `other_riot_product` - 其他 Riot 产品
- 定义：非本项目核心范围、但明确可识别为 Riot 产品的游戏或 IP，例如 2XKO、VALORANT、Riftbound、LoR 和 Wild Rift。
- 命中条件：正文明确讨论上述或其他已识别 Riot 产品，且不宜为每个低频产品创建独立运行时枚举。
- 排除条件：没有足够证据确认属于 Riot 产品时使用 other 或 unknown。
- 全量命中：32 条（4.34%）；代表记录：`14`, `15`, `16`, `17`, `80`
- 易混淆：`other`, `riot_corporate`
- 边界规则：跨产品消息可同时标注其他 Riot 产品与 lol_pc/tft；具体产品名建议作为后续辅助实体，不进入本轮产品枚举。

### `unknown` - 产品未明
- 定义：现有标题、正文、图注和已批准译文不足以稳定判断产品。
- 命中条件：消息看起来涉及产品，但只有链接、不可读图片或极短上下文，无法识别是哪一个。
- 排除条件：明确属于外部内容或纯个人表达时 products 使用空数组。
- 全量命中：17 条（2.31%）；代表记录：`280`, `411`, `477`, `479`, `483`
- 易混淆：`空数组`
- 边界规则：unknown 必须同时 review_needed=true；空数组表示该轴不适用而不是证据不足。

## Message Types 统计

| message_type | 记录数 |
| --- | --- |
| `announcement` | 165 |
| `commentary` | 120 |
| `match_result` | 86 |
| `promotion` | 63 |
| `patch_notes` | 60 |
| `showcase` | 55 |
| `preview` | 46 |
| `explainer` | 27 |
| `rumor` | 25 |
| `schedule` | 24 |
| `report` | 13 |
| `personal_post` | 13 |
| `interview` | 10 |
| `notice` | 9 |
| `unknown` | 8 |
| `roundup` | 5 |
| `leak` | 5 |
| `correction` | 3 |

不同来源的 MessageType 分布：

| 来源 | message_type=数量 |
| --- | --- |
| League of Legends (@LeagueofLegends) | `promotion`=16、`announcement`=12、`showcase`=8、`explainer`=4、`commentary`=2、`patch_notes`=1、`report`=1、`unknown`=1、`roundup`=1 |
| LoL Esports (@lolesports) | `match_result`=13、`announcement`=7、`promotion`=6、`showcase`=6、`schedule`=3、`unknown`=3、`personal_post`=2、`commentary`=2、`interview`=1、`preview`=1 |
| Matt Leung-Harrison (@RiotPhroxzon) | `preview`=8、`patch_notes`=7、`commentary`=2、`promotion`=2、`match_result`=1、`explainer`=1 |
| Riot Games Official | `explainer`=8、`patch_notes`=7、`announcement`=4、`promotion`=1 |
| SkinSpotlights (@SkinSpotlights) | `announcement`=28、`preview`=14、`commentary`=10、`showcase`=5、`report`=2、`explainer`=2、`personal_post`=2、`promotion`=2、`match_result`=1、`schedule`=1、`unknown`=1、`notice`=1、`correction`=1 |
| Spideraxe (@Spideraxe30) | `commentary`=34、`announcement`=24、`patch_notes`=23、`showcase`=12、`explainer`=9、`notice`=6、`preview`=6、`promotion`=5、`personal_post`=4、`unknown`=3、`leak`=3、`correction`=2、`roundup`=1、`report`=1 |
| _尧阿尧y_ | `commentary`=27、`match_result`=14、`rumor`=9、`personal_post`=5、`preview`=3、`promotion`=2、`announcement`=2、`showcase`=1、`report`=1 |
| lol半价吧 · 凤舞天_惊鸿恋 | `announcement`=20、`patch_notes`=6、`match_result`=3、`preview`=2、`promotion`=2、`commentary`=1、`notice`=1、`rumor`=1 |
| lol半价吧 · 小老鼠小伟 | `announcement`=32、`match_result`=14、`patch_notes`=6、`showcase`=4、`preview`=3、`commentary`=2、`leak`=2、`explainer`=1、`roundup`=1 |
| 召唤师Park | `commentary`=23、`rumor`=8、`promotion`=5、`match_result`=5、`report`=5、`preview`=3、`schedule`=2、`interview`=1、`announcement`=1、`notice`=1 |
| 恋恋红茶_244 | `interview`=7、`rumor`=7、`schedule`=6、`match_result`=6、`commentary`=5、`report`=3、`promotion`=3、`roundup`=2、`showcase`=1、`preview`=1 |
| 腾讯英雄联盟官方网站 | `announcement`=13、`patch_notes`=9 |
| 英雄联盟 | `announcement`=19、`showcase`=12、`promotion`=12、`commentary`=10、`preview`=5、`schedule`=3、`explainer`=2、`patch_notes`=1 |
| 英雄联盟赛事 | `match_result`=29、`schedule`=9、`promotion`=7、`showcase`=6、`announcement`=3、`commentary`=2、`interview`=1 |

## Message Types 推荐表
### `announcement` - 公告
- 定义：发布者正式宣布新的可验证安排、可用性、规则、上线或政策。
- 命中条件：主动作是宣布、上线、开放、发布、任命或确认。
- 排除条件：完整版本变更文档、赛果、赛程、采访、爆料和纯营销分别使用专门类型。
- 全量命中：165 条（22.39%）；代表记录：`12`, `13`, `14`, `17`, `23`
- 易混淆：`preview`, `promotion`, `report`
- 边界规则：有新的正式事实且不是其他专门文档形式时，使用 announcement。

### `patch_notes` - 版本说明
- 定义：结构化列举版本、热修复或测试服改动的说明文档。
- 命中条件：标题或正文以版本/补丁为主并列出具体改动、修复或条目。
- 排除条件：只预告未来改动但没有成体系条目时使用 preview。
- 全量命中：60 条（8.14%）；代表记录：`43`, `47`, `49`, `55`, `58`
- 易混淆：`preview`, `announcement`
- 边界规则：以文档结构和改动清单优先，不以官方身份或已上线状态判断。

### `notice` - 运行通知
- 定义：面向用户说明服务状态、部署进度、故障、维护、延期或已知问题的通知。
- 命中条件：主信息是可用性、部署、维护、故障处置、延期或临时规避措施。
- 排除条件：结构化版本改动使用 patch_notes；普通新内容发布使用 announcement。
- 全量命中：9 条（1.22%）；代表记录：`303`, `405`, `410`, `414`, `459`
- 易混淆：`patch_notes`, `announcement`, `correction`
- 边界规则：以运行状态或处置进展为读者主要行动依据时使用 notice。

### `preview` - 预览
- 定义：发布者展示未来内容、开发方向、测试内容或即将到来的变化。
- 命中条件：主旨是预告、抢先看、开发预览、PBE 预览或 teaser。
- 排除条件：非官方数据挖掘用 leak；纯素材展示用 showcase。
- 全量命中：46 条（6.24%）；代表记录：`7`, `8`, `10`, `11`, `73`
- 易混淆：`leak`, `showcase`, `announcement`
- 边界规则：由内容是否以未来可用内容为中心区分；已确定上线公告仍是 announcement。

### `leak` - 爆料/数据挖掘
- 定义：以非官方发现、数据挖掘或未公开资产为中心的具体预发布信息。
- 命中条件：正文将信息表述为 datamine、泄露、测试客户端发现或未公开素材。
- 排除条件：只有传闻、推测或听闻且没有具体发现依据时使用 rumor。
- 全量命中：5 条（0.68%）；代表记录：`524`, `525`, `597`, `647`, `651`
- 易混淆：`rumor`, `preview`
- 边界规则：具体发现来源为 leak，来源不明的可能性判断为 rumor。

### `rumor` - 传闻/推测
- 定义：传播未证实的消息、候选方案或主观概率判断。
- 命中条件：正文明确使用传闻、据说、可能、应当、尚未确认等表达。
- 排除条件：对已知事实的评论使用 commentary；可复核的数据挖掘使用 leak。
- 全量命中：25 条（3.39%）；代表记录：`234`, `254`, `261`, `266`, `267`
- 易混淆：`leak`, `commentary`
- 边界规则：主张外部事实但未证实为 rumor；仅表达看法为 commentary。

### `report` - 新闻报道
- 定义：报道、转述或整理已发生事实的新闻性内容。
- 命中条件：主旨是向读者说明事实进展，且不属于赛果、赛程或采访。
- 排除条件：官方直接宣布用 announcement；主观评价用 commentary。
- 全量命中：13 条（1.76%）；代表记录：`181`, `210`, `214`, `217`, `257`
- 易混淆：`announcement`, `commentary`, `roundup`
- 边界规则：第三方事实叙述优先用 report，即使引用官方原话。

### `match_result` - 赛果报道
- 定义：报告已经结束的比赛、对局、排名或竞赛结果。
- 命中条件：给出胜负、比分、MVP、淘汰、晋级、名次或赛后战报。
- 排除条件：赛前对阵和开播安排使用 schedule；分析比赛使用 commentary。
- 全量命中：86 条（11.67%）；代表记录：`9`, `66`, `93`, `116`, `140`
- 易混淆：`schedule`, `commentary`, `roundup`
- 边界规则：比赛结果是整条消息主事实时优先于一般 report。

### `schedule` - 赛程与安排
- 定义：公布未来比赛、节目、售票、转播、现场或时间安排。
- 命中条件：主信息是未来时间、对阵、开售、场地或播出安排。
- 排除条件：已经结束的赛事报道使用 match_result；无具体安排的宣传使用 promotion。
- 全量命中：24 条（3.26%）；代表记录：`22`, `24`, `75`, `79`, `85`
- 易混淆：`promotion`, `announcement`, `match_result`
- 边界规则：存在可执行的未来日程或票务信息时优先使用 schedule。

### `interview` - 采访/引语
- 定义：以问答、采访、直播转述或人物直接发言为组织方式。
- 命中条件：正文以问答、受访者观点或长引语为核心。
- 排除条件：新闻仅短暂引用一句话时仍可为 report。
- 全量命中：10 条（1.36%）；代表记录：`26`, `74`, `84`, `87`, `204`
- 易混淆：`report`, `commentary`
- 边界规则：内容结构由受访者发言驱动时使用 interview。

### `commentary` - 评论与分析
- 定义：以解释、评价、讨论、预测、吐槽或个人观点为主要表达。
- 命中条件：主张是作者/嘉宾的分析或观点而非新增事实公告。
- 排除条件：未证实外部消息用 rumor；教学内容用 explainer。
- 全量命中：120 条（16.28%）；代表记录：`27`, `29`, `30`, `64`, `77`
- 易混淆：`rumor`, `report`, `explainer`
- 边界规则：先问是否在主张新事实；不是时，评价性表达归 commentary。

### `explainer` - 说明与指南
- 定义：系统解释玩法、机制、设计思路或提供操作指引的内容。
- 命中条件：正文组织为说明、开发解读、教程、攻略、FAQ 或操作建议。
- 排除条件：只宣布内容上线时使用 announcement；主观赛事评价用 commentary。
- 全量命中：27 条（3.66%）；代表记录：`56`, `57`, `62`, `63`, `125`
- 易混淆：`commentary`, `preview`, `patch_notes`
- 边界规则：以帮助读者理解或执行为中心，而非发表观点或列改动清单。

### `promotion` - 推广与互动
- 定义：以召集、营销、提醒、投票、征集、福利或行动号召为主。
- 命中条件：主动作是观看、参与、购买、报名、投票、领取或互动。
- 排除条件：有完整具体赛程时使用 schedule；有实质发布事实时使用 announcement。
- 全量命中：63 条（8.55%）；代表记录：`20`, `21`, `25`, `54`, `76`
- 易混淆：`announcement`, `schedule`, `showcase`
- 边界规则：行动号召压过背景介绍时使用 promotion。

### `showcase` - 素材展示
- 定义：以图、视频、原画、片段、集锦、皮肤展示或粉丝作品为中心。
- 命中条件：媒体素材本身是主要内容，正文只作简短配文。
- 排除条件：纯转发且没有展示性编辑意图时使用 repost；有完整新闻事实时使用对应类型。
- 全量命中：55 条（7.46%）；代表记录：`15`, `16`, `18`, `19`, `28`
- 易混淆：`repost`, `promotion`, `preview`
- 边界规则：展示对象而非发布/报道动作是读者获得的主要价值时使用 showcase。

### `roundup` - 汇总
- 定义：将多个相对独立项目、消息或片段并列整理的发布。
- 命中条件：标题/正文显著汇总多场比赛、多项新闻、多条素材或日常榜单。
- 排除条件：同一版本的一组改动仍为 patch_notes；单一事件赛后总结为 match_result。
- 全量命中：5 条（0.68%）；代表记录：`345`, `432`, `482`, `504`, `513`
- 易混淆：`patch_notes`, `match_result`, `report`
- 边界规则：至少两个独立对象均为发布主角时使用 roundup。

### `correction` - 更正与澄清
- 定义：撤回、修正、道歉或澄清先前发布的信息。
- 命中条件：正文主要动作是更正、道歉、澄清或更新错误信息。
- 排除条件：普通新进展或补充说明仍为 announcement/report。
- 全量命中：3 条（0.41%）；代表记录：`692`, `723`, `731`
- 易混淆：`announcement`, `report`
- 边界规则：必须有明确针对既有信息的纠正关系。

### `personal_post` - 个人状态帖
- 定义：没有稳定新闻或产品事实的私人感想、玩笑、日常状态或简单社交表达。
- 命中条件：正文主要是个人状态、梗图或与项目无关的轻量社交内容。
- 排除条件：可识别出外部新闻事实时使用其相应 message_type。
- 全量命中：13 条（1.76%）；代表记录：`222`, `240`, `395`, `401`, `463`
- 易混淆：`commentary`, `unknown`
- 边界规则：内容足以理解但没有可分类业务主题时使用 personal_post。

### `unknown` - 类型未明
- 定义：原始证据过少，无法稳定判断整条发布的形式。
- 命中条件：空内容、只有无法读取的媒体/链接，且标题也不足。
- 排除条件：标题足以识别高层文档形式时仍给出该类型并标记 review_needed。
- 全量命中：8 条（1.09%）；代表记录：`280`, `411`, `479`, `499`, `695`
- 易混淆：`personal_post`
- 边界规则：unknown 必须同时 review_needed=true。

## Topics 统计

单 Topic：`280`；多 Topic：`457`（62.01%）；Topic 数量分布：`1` 个=280、`2` 个=249、`3` 个=155、`4` 个=39、`5` 个=14。

| 高频 Topic 组合 | 记录数 |
| --- | --- |
| esports_matches + esports_analysis | 41 |
| esports_matches | 33 |
| esports_rosters | 31 |
| cosmetics | 30 |
| esports_schedule | 21 |
| shop_monetization | 21 |
| community | 18 |
| cosmetics + shop_monetization | 17 |
| balance_gameplay + champions + items_runes_systems | 14 |
| unknown | 14 |
| other | 13 |
| game_modes | 13 |

## Topics 推荐表
### `balance_gameplay` - 平衡与玩法改动
- 定义：数值平衡、玩法规则、强弱调整、补丁改动和热修复。
- 命中条件：正文讨论加强、削弱、调整、数值、版本改动或热修复。
- 排除条件：只介绍英雄设定或玩法技巧时分别使用 champions/guides_education。
- 全量命中：113 条（15.33%）；代表记录：`7`, `8`, `9`, `10`, `11`
- 易混淆：`champions`, `items_runes_systems`, `service_technical`
- 边界规则：发生实际玩法改动时标注；受影响对象可再加 champions 或 items_runes_systems。

### `champions` - 英雄内容
- 定义：英雄发布、重做、技能、设计、背景或英雄主体改动。
- 命中条件：英雄本身是消息中的发布对象、改动对象或说明对象。
- 排除条件：仅在比赛战报中出现的英雄选择不标注。
- 全量命中：78 条（10.58%）；代表记录：`7`, `8`, `10`, `11`, `41`
- 易混淆：`balance_gameplay`, `cosmetics`
- 边界规则：英雄是实质内容而不是例子、出场角色或皮肤载体时才标注。

### `items_runes_systems` - 装备、符文与系统
- 定义：装备、符文、强化符文、特质、排位、客户端规则和核心系统。
- 命中条件：这些系统是改动、说明或讨论的实质对象。
- 排除条件：只在英雄/比赛叙述中顺带提到时不标注。
- 全量命中：106 条（14.38%）；代表记录：`7`, `8`, `10`, `11`, `12`
- 易混淆：`balance_gameplay`, `tft_gameplay`, `service_technical`
- 边界规则：系统改动可与 balance_gameplay 共现；云顶赛季系统优先再加 tft_gameplay。

### `game_modes` - 游戏模式
- 定义：端游模式的发布、轮换、规则和体验，包括 Classic、Arena、ARAM 等。
- 命中条件：模式本身是发布、改动、开放或讨论对象。
- 排除条件：云顶赛季主体内容使用 tft_gameplay；仅在比赛中提到模式不标注。
- 全量命中：115 条（15.6%）；代表记录：`7`, `18`, `40`, `43`, `45`
- 易混淆：`tft_gameplay`, `balance_gameplay`
- 边界规则：模式专题可与玩法改动、活动或奖励同时标注。

### `gameplay` - 一般玩法与对局体验
- 定义：非平衡公告的玩法过程、技巧、对局瞬间、机制体验与一般游戏内容。
- 命中条件：正文主要展示、讨论或解释实际游戏内操作、体验或机制，且没有更具体的稳定主题可替代。
- 排除条件：数值/规则改动用 balance_gameplay；装备、符文和系统细节用 items_runes_systems；教学主旨用 guides_education。
- 全量命中：47 条（6.38%）；代表记录：`61`, `99`, `102`, `117`, `130`
- 易混淆：`balance_gameplay`, `items_runes_systems`, `guides_education`
- 边界规则：作为具体玩法内容的兜底，不用于只因提到游戏而无实质玩法信息的消息。

### `tft_gameplay` - 云顶玩法与赛季
- 定义：云顶赛季/套装、羁绊、强化符文、棋子和游戏机制。
- 命中条件：云顶玩法、赛季或机制是消息主体。
- 排除条件：小小英雄、棋盘等外观主导时使用 cosmetics。
- 全量命中：13 条（1.76%）；代表记录：`29`, `138`, `182`, `183`, `194`
- 易混淆：`game_modes`, `cosmetics`, `items_runes_systems`
- 边界规则：需要 tft 产品证据；云顶外观不因产品本身自动触发。

### `cosmetics` - 外观与游戏资产
- 定义：皮肤、炫彩、小小英雄、表情、图标、原画、特效和其他可展示游戏资产。
- 命中条件：外观或资产的发布、展示、设计、返场或获取是实质内容。
- 排除条件：仅列为活动奖励或比赛背景时不标注，除非外观本身也是消息焦点。
- 全量命中：161 条（21.85%）；代表记录：`12`, `14`, `15`, `16`, `29`
- 易混淆：`shop_monetization`, `activities_rewards`, `lore_universe`
- 边界规则：可与购买/活动渠道共现，但不要把渠道取代外观主题。

### `shop_monetization` - 商城与商业化
- 定义：商城轮换、价格、折扣、礼包、抽取、售卖、付费通行证和货币。
- 命中条件：购买、售价、轮换、折扣、概率抽取或商业获取方式是实质信息。
- 排除条件：免费奖励、任务或活动机制为主时使用 activities_rewards。
- 全量命中：85 条（11.53%）；代表记录：`12`, `15`, `40`, `42`, `46`
- 易混淆：`activities_rewards`, `cosmetics`
- 边界规则：外观发布可同时有 cosmetics；消息主讲获取价格/商城时必须有 shop_monetization。

### `activities_rewards` - 活动与奖励
- 定义：游戏内外活动、通行证、任务、兑换、免费领取、福利和口令。
- 命中条件：参与条件、进度、任务、奖励或领取是主要内容。
- 排除条件：只做商品售卖时使用 shop_monetization；只做赛事赛程时使用 esports_schedule。
- 全量命中：86 条（11.67%）；代表记录：`20`, `21`, `25`, `40`, `42`
- 易混淆：`shop_monetization`, `community`, `cosmetics`
- 边界规则：活动赠送外观可以同时标 cosmetics；付费商品主导时再加 shop_monetization。

### `service_technical` - 服务与技术
- 定义：维护、宕机、Bug、性能、客户端、服务器、接口和技术问题。
- 命中条件：故障、修复、维护、可用性、技术实现或工具问题为实质内容。
- 排除条件：反作弊、纪律与处罚使用 security_fair_play；一般平衡更新使用 balance_gameplay。
- 全量命中：56 条（7.6%）；代表记录：`42`, `43`, `47`, `72`, `151`
- 易混淆：`balance_gameplay`, `security_fair_play`, `corporate_partnerships`
- 边界规则：面向服务稳定性或技术行为时标注；补丁中的单个 Bug 修复可与 balance_gameplay 共现。

### `security_fair_play` - 安全与竞技公平
- 定义：反作弊、违规处罚、纪律、骚扰、竞赛诚信和账号安全。
- 命中条件：正文主要讨论违规、处理、禁赛、反作弊或公平性。
- 排除条件：一般服务器故障或技术修复使用 service_technical。
- 全量命中：3 条（0.41%）；代表记录：`518`, `596`, `728`
- 易混淆：`service_technical`, `esports_rosters`
- 边界规则：处理人的行为和公平规则时使用此 topic。

### `esports_matches` - 电竞比赛
- 定义：单场/多场比赛、赛果、战报、集锦、MVP 和比赛进程。
- 命中条件：比赛本身、比分、对阵、关键局面或选手比赛表现是主体。
- 排除条件：纯赛程/售票使用 esports_schedule；人员变动使用 esports_rosters。
- 全量命中：116 条（15.74%）；代表记录：`78`, `87`, `93`, `95`, `116`
- 易混淆：`esports_schedule`, `esports_competition`, `esports_analysis`
- 边界规则：已经发生或正在进行的对局优先使用 esports_matches。

### `esports_schedule` - 电竞赛程与现场
- 定义：未来赛事赛程、首发、票务、场地、转播、节目与观赛安排。
- 命中条件：时间表、购票、直播、现场或未来对阵是实质信息。
- 排除条件：比赛结果使用 esports_matches；赛事制度/积分使用 esports_competition。
- 全量命中：45 条（6.11%）；代表记录：`22`, `23`, `24`, `27`, `75`
- 易混淆：`esports_matches`, `activities_rewards`, `promotion`
- 边界规则：有可执行的未来安排时标注，即使消息也在推广活动。

### `esports_rosters` - 电竞阵容与人员
- 定义：职业选手、教练、战队阵容、转会、离队、合同和职业状态。
- 命中条件：人员归属、转会、替补、休息、退役或队伍构成为主体。
- 排除条件：单场比赛表现或采访不因涉及选手自动标注。
- 全量命中：61 条（8.28%）；代表记录：`84`, `87`, `91`, `92`, `120`
- 易混淆：`esports_matches`, `esports_analysis`
- 边界规则：关注职业身份或队伍归属的变化时标注。

### `esports_competition` - 赛事体系与成绩
- 定义：联赛制度、赛制、排名、晋级、资格、奖项和赛事整体进程。
- 命中条件：比赛之外的竞赛结构、积分、资格、阶段、奖项或赛事整体事实是主体。
- 排除条件：单场赛果使用 esports_matches；未来具体日程使用 esports_schedule。
- 全量命中：54 条（7.33%）；代表记录：`9`, `20`, `24`, `46`, `54`
- 易混淆：`esports_matches`, `esports_schedule`, `esports_analysis`
- 边界规则：对象是赛事体系或跨多场的结果时使用此 topic。

### `esports_analysis` - 电竞分析与评论
- 定义：对比赛、队伍、选手、版本竞技环境或职业场景的解释和评价。
- 命中条件：核心价值是比赛/职业生态的观点、复盘、预测或表现评价。
- 排除条件：纯赛果、赛程或阵容事实分别使用对应 topic。
- 全量命中：110 条（14.93%）；代表记录：`9`, `11`, `26`, `27`, `74`
- 易混淆：`esports_matches`, `commentary`
- 边界规则：这是内容领域；message_type 是否为 commentary/interview 另行判断。

### `community` - 社区与创作
- 定义：玩家社区、粉丝作品、同人、投票、反馈、社区活动和创作者互动。
- 命中条件：粉丝、社区组织、创作者、互动或用户反馈是主体。
- 排除条件：官方赛事/游戏活动的规则本体使用相应电竞或活动 topic。
- 全量命中：78 条（10.58%）；代表记录：`18`, `19`, `20`, `24`, `25`
- 易混淆：`media_entertainment`, `activities_rewards`, `lore_universe`
- 边界规则：以用户/社区贡献为主才标注，官方公告不因面向玩家而自动标。

### `lore_universe` - 世界观与叙事
- 定义：符文之地故事、角色背景、叙事设定和 IP 世界观。
- 命中条件：故事、角色关系、设定或叙事创作为消息主体。
- 排除条件：皮肤视觉展示或游戏机制说明不属于此 topic。
- 全量命中：7 条（0.95%）；代表记录：`140`, `305`, `306`, `307`, `404`
- 易混淆：`cosmetics`, `media_entertainment`
- 边界规则：叙事内容必须是主角，不能仅由皮肤主题名称触发。

### `media_entertainment` - 媒体与娱乐
- 定义：游戏外的视频节目、音乐、动画、电影、直播内容和娱乐合作。
- 命中条件：媒体作品、演出、节目、影视或娱乐内容为主要对象。
- 排除条件：游戏内素材展示使用 cosmetics/showcase；社区作品使用 community。
- 全量命中：38 条（5.16%）；代表记录：`21`, `22`, `25`, `30`, `71`
- 易混淆：`community`, `lore_universe`, `riot_merchandise_media`
- 边界规则：内容领域是媒体作品本身时标注；product 可另为 lol_universe 或 riot_merchandise_media。

### `merchandise_collectibles` - 周边与收藏品
- 定义：实体周边、收藏卡、玩具、服饰、艺术印刷品和零售商品。
- 命中条件：实体商品、收藏/开箱、售卖、预售或物流为主要内容。
- 排除条件：纯游戏内商品使用 shop_monetization。
- 全量命中：20 条（2.71%）；代表记录：`46`, `59`, `80`, `162`, `298`
- 易混淆：`shop_monetization`, `media_entertainment`
- 边界规则：交付物为实体或收藏品时标注。

### `corporate_partnerships` - 公司、开发与合作
- 定义：Riot 公司政策、开发者生态、招聘、商务合作、账户服务和品牌合作。
- 命中条件：企业/开发平台/合作组织关系本身是消息主体。
- 排除条件：具体游戏玩法或赛事内容使用各自主题。
- 全量命中：31 条（4.21%）；代表记录：`17`, `84`, `114`, `169`, `184`
- 易混淆：`service_technical`, `community`, `media_entertainment`
- 边界规则：公司级责任、商业或开发者平台事实才标注。

### `guides_education` - 指南与知识
- 定义：攻略、操作建议、百科、机制讲解和教育性内容。
- 命中条件：目的是教读者理解、学习或执行玩法/知识。
- 排除条件：开发者宣布新机制用 announcement/preview；主观评价用 esports_analysis 或 commentary。
- 全量命中：15 条（2.04%）；代表记录：`125`, `126`, `183`, `191`, `194`
- 易混淆：`items_runes_systems`, `esports_analysis`
- 边界规则：有明确教学或解释读者价值时标注。

### `other` - 其他已识别主题
- 定义：内容可理解，但属于体系外的外部领域或不承载 LeagueNews 业务主题的个人表达。
- 命中条件：明确是非 Riot/非英雄联盟内容，或可理解的个人状态帖没有稳定业务主题。
- 排除条件：证据不足时使用 unknown。
- 全量命中：17 条（2.31%）；代表记录：`31`, `97`, `98`, `113`, `189`
- 易混淆：`unknown`
- 边界规则：other 不能承接本应属于任一稳定业务主题的内容；它也不要求 products 有值。

### `unknown` - 主题未明
- 定义：原始文本、标题和图注不足以稳定识别主题。
- 命中条件：空文本、只有不可读媒体或过短上下文。
- 排除条件：可以从标题判断任何稳定主题时不使用。
- 全量命中：14 条（1.9%）；代表记录：`13`, `241`, `358`, `411`, `479`
- 易混淆：`other`
- 边界规则：unknown 必须同时 review_needed=true。

## 辅助字段建议：content_form

`content_form` 不属于本轮三个主分类轴，只是分析建议。它保留转载、引用、纯媒体和纯链接的发布形态，防止 `repost` 吞掉原本应检索的公告、赛果、展示或通知意图。实际观测到的取值如下；未在本语料中得到例证的候选值不进入 v1。

| code | 中文名称 | 定义 | 数量（占比） | 记录示例 |
| --- | --- | --- | --- | --- |
| `original` | 原创发布 | 可见正文由当前发布者直接组织，未以转发、引用、回复、纯链接或纯媒体为主。 | 607 (82.36%) | `7`, `8`, `9`, `10`, `11` |
| `repost` | 转发 | 正文显式以 RT、转发或原微博语法转载他人内容。 | 103 (13.98%) | `17`, `18`, `19`, `21`, `28` |
| `quote` | 引用发布 | 当前发布附带被引用的帖子或嵌入内容，但不是纯转发语法。 | 21 (2.85%) | `81`, `126`, `129`, `185`, `190` |
| `link_only` | 仅链接 | 没有可读语义正文，只有外部链接或链接占位符。 | 3 (0.41%) | `411`, `741`, `748` |
| `media_only` | 仅媒体 | 没有可读正文或图注，内容证据仅来自无法在本轮读取的图片或视频。 | 3 (0.41%) | `280`, `695`, `696` |

## 边界案例

| 边界 | 回测结果 | 判定规则 |
| --- | --- | --- |
| 版本文档与预览 | `7`: products=[lol_pc], type=`preview`, topics=[balance_gameplay,champions,items_runes_systems,game_modes]；`43`: products=[lol_pc], type=`patch_notes`, topics=[balance_gameplay,items_runes_systems,game_modes,service_technical]；`71`: products=[lol_pc], type=`patch_notes`, topics=[balance_gameplay,champions,items_runes_systems,media_entertainment] | 结构化、实际列出改动的内容归 `patch_notes`；仅展示即将到来的内容或方向归 `preview`。 |
| 公告、运行通知与更正 | `303`: products=[tft], type=`notice`, topics=[service_technical]；`592`: products=[tft], type=`notice`, topics=[service_technical]；`723`: products=[(empty)], type=`correction`, topics=[other] | 运行状态、部署和故障处置归 `notice`；针对已发布信息的纠正归 `correction`；二者不把事件状态写入 topic。 |
| 赛果、赛程与分析 | `75`: products=[lol_esports], type=`schedule`, topics=[esports_schedule]；`116`: products=[lol_esports], type=`match_result`, topics=[esports_matches,esports_schedule,esports_competition]；`197`: products=[lol_esports], type=`match_result`, topics=[esports_matches]；`238`: products=[lol_esports], type=`match_result`, topics=[esports_matches,esports_competition] | 已发生比赛的胜负/过程归 `match_result`，未来可执行安排归 `schedule`，观点或复盘归 `commentary`。 |
| 转发不占主类型 | `17`: products=[lol_pc,riot_corporate,other_riot_product], type=`announcement`, topics=[corporate_partnerships]；`18`: products=[lol_pc], type=`showcase`, topics=[game_modes,community]；`238`: products=[lol_esports], type=`match_result`, topics=[esports_matches,esports_competition]；`592`: products=[tft], type=`notice`, topics=[service_technical] | 转发和引用保留在 `content_form`，主类型仍描述被传递内容的公告、展示、赛果或通知意图。 |
| 模式、赛事与产品 | `57`: products=[lol_pc], type=`explainer`, topics=[champions,items_runes_systems,game_modes,community]；`565`: products=[lol_pc], type=`announcement`, topics=[items_runes_systems,game_modes,activities_rewards]；`579`: products=[lol_pc,lol_esports], type=`announcement`, topics=[game_modes,activities_rewards] | League Classic 是 `lol_pc` 的模式主题；LPL/MSI/Worlds 是 `lol_esports` 下的赛事上下文，不各自变成产品。 |
| 预告与展示 | `15`: products=[lol_pc,other_riot_product], type=`showcase`, topics=[cosmetics,shop_monetization]；`18`: products=[lol_pc], type=`showcase`, topics=[game_modes,community]；`720`: products=[lol_pc], type=`preview`, topics=[gameplay,cosmetics,shop_monetization] | 未来可用内容的核心价值是 `preview`；已有图、视频、原画或创作本身是价值时归 `showcase`。 |

## 被拒绝或重新归位的候选标签

| 候选标签 | 原可能位置 | 实际位置 | 处理决定 | 真实记录 |
| --- | --- | --- | --- | --- |
| LPL、LCS、LCP、MSI、Worlds | products | lol_esports 产品下的赛事/地区辅助上下文 | 不单列为 Product；联赛、赛区、届次应由后续实体或上下文字段承载。 | `23`, `75`, `116`, `579` |
| League Classic / classic_mode | products | lol_pc + game_modes topic | 不把模式当产品。 | `45`, `57`, `565`, `579` |
| patch_notes、patch_preview、interview、poll | topics | message_type | 文档形式或发布意图不作为内容主题。 | `7`, `43`, `74`, `304` |
| hotfix、match_result、skin_release、availability | event 状态或操作 | message_type + topics，或事件层状态 | 消息分类只描述当前发布内容，不记录事件生命周期或聚合动作。 | `47`, `116`, `701`, `728` |
| PBE、datamine、official、retweet、quote | products / topics / message_type | 环境、来源性质或 content_form 辅助字段 | PBE 和 datamine 通过正文决定 preview/leak/notice；转发和引用移至 content_form。 | `17`, `71`, `303`, `592` |
| merchandise、physical_collectibles、ecommerce | products | merchandise_collectibles 或 shop_monetization topic | 实体收藏品与售卖方式是主题；只有 Riot 的独立游戏外业务才使用 riot_merchandise_media 产品。 | `80`, `704`, `705`, `749` |

## review_needed 清单

`715 + 22 = 737`，数量核对通过。复核不是对来源或可信度的判断，只表示在可读原始证据下无法稳定完成三个轴之一。

| 原因 | 数量 | 记录 ID |
| --- | --- | --- |
| 已能识别部分消息形式或产品，但证据不足以稳定判断内容主题。 | 5 | `13`, `241`, `358`, `541`, `653` |
| 原始记录只有未读取的媒体，缺少可判定的标题、正文或图注。 | 3 | `280`, `695`, `696` |
| 原始记录只有链接或链接占位符，无法从可读证据判断内容。 | 3 | `411`, `741`, `748` |
| 消息看起来涉及某个产品，但标题、正文和可用译文不足以识别产品。 | 11 | `477`, `479`, `483`, `489`, `499`, `589`, `611`, `629`, `690`, `738`, `759` |

## 未决问题与后续实现建议

- `other_riot_product` 已覆盖观察到的 2XKO、Riftbound、VALORANT、Wild Rift 和 LoR 等低频但可识别产品。先保留统一桶，待未来全量数据证明其中某一产品长期需要独立路由时再新增 Product。
- 图像和视频未做视觉理解，因此 `media_only` 记录必须人工补足；链接内容也不应被猜测为某个产品或主题。
- 进入实现阶段时，可把本 JSON 作为评审输入而不是直接替换生产枚举；先为分类契约、`unknown` 路径、revision 幂等性和 content_form 添加测试，再做迁移式演进。
- 赛事名称、版本号、英雄/选手/战队、地区和 patch 标识更适合作为后续实体或上下文提取字段；它们不应回流污染本轮三轴。

## 交付物

- `docs/analysis/message-taxonomy-audit.md`：本报告。
- `docs/analysis/message-taxonomy-assignments.jsonl`：737 条逐条审计结果，包含简短理由、来源引用、内容优先级、数据质量标识和 `content_form`。
- `docs/analysis/message-taxonomy-v1.json`：机器可读候选分类定义、计数、示例、辅助字段和被拒绝标签。
- `docs/analysis/message-taxonomy-stats.json`：由同一回测生成的统计辅助文件。
