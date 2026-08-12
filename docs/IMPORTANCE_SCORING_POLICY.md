# 消息重要性计算

本文档是当前可执行重要性政策的唯一说明，对应
`services/api/app/domain/importance.py`。当前版本：

- 消息重要性：`importance-v11-repost-weekly-rotation`
- 消息排序优先级：`importance-v11-repost-weekly-rotation`

修改分类到评分的路由、评分档案、修正项或排序规则时，必须同时更新实现、测试、本文档和版本号。

## 一、评分口径与三层职责

数据库和 API 使用 `0.00-1.00`，前端按 `0-100` 展示。

系统区分三个概念：

1. **领域重要性 `profile_score`**：衡量一份内容证据所描述事实本身有多重要。Message 和
   EventMention 共用 profile、档案区间与领域修正政策，但分别针对整条消息和独立 Event 本体评分。
2. **消息重要性 `importance_score`**：衡量这条具体消息有多值得展示。在领域分上叠加消息载体
   修正；当前只有纯转发 `repost -0.08`。
3. **消息排序优先级 `priority_score`**：只在消息重要性上叠加引用形式和受众范围等投放约束，
   用于消息流排序。纯转发已经在消息重要性中扣分，不在排序阶段重复扣分。

可信度不是重要性字段；Source 可靠性不直接增减分。官方或非官方身份已经通过候选目录约束
`message_type`，评分程序只使用批准后的分类结果。

`media_only` 和 `link_only` 不执行重要性模型与评分算法，两个分数均写为 0。

## 二、分类原生计算流程

```text
批准后的 message_type + 全部 topics + content_form + 当前消息文本
  -> topics 映射为 topic families
  -> message_type × topic families 路由到一个 importance_profile
  -> 读取档案的基准分、下限和上限
  -> 应用有限的结构化修正项
  -> 应用领域修正并限制在档案区间内，得到 profile_score（Domain Importance）
  -> 纯转发扣 8 分
  -> importance_score（Message Importance）
  -> 应用引用形式和受众修正
  -> priority_score
```

公式：

```text
原始分 = 档案基准分 + 所有领域修正项
领域重要性 = 档案分 = min(档案上限, max(档案下限, 原始分))
消息重要性 = min(100, max(0, 领域重要性 + 消息载体修正项))
排序优先级 = min(100, max(0, 消息重要性 + 投放修正项))
```

系统不再生成或使用 `primary_topic`、`subtopic`、`source_kind`、`information_stage` 或
`editorial_subtype`。计算审计字段统一使用 `importance_profile`。

## 三、Topics 如何参与评分

所有 topics 先映射为评分领域：

| topic family | topics |
| --- | --- |
| `gameplay` | `balance_gameplay`、`champions`、`items_runes_systems`、`game_modes`、`gameplay` |
| `tft` | `tft_gameplay` |
| `service` | `service_technical` |
| `security` | `security_fair_play` |
| `cosmetics` | `cosmetics` |
| `commerce` | `shop_monetization` |
| `activity` | `activities_rewards` |
| `community` | `community` |
| `guide` | `guides_education` |
| `esports_competition` | `esports_competition` |
| `esports_schedule` | `esports_schedule` |
| `esports_matches` | `esports_matches` |
| `esports_rosters` | `esports_rosters` |
| `esports_analysis` | `esports_analysis` |
| `esports_media` | `esports_broadcast`、`esports_fandom_live` |
| `universe` | `lore_universe` |
| `media` | `media_entertainment` |
| `merchandise` | `merchandise_collectibles` |
| `corporate` | `corporate_partnerships` |
| `platform` | `platform_services` |
| `unknown` | `unknown` |

同一消息最终只选择一个重要性档案：

- 不只读取第一个 topic；路由使用完整 topic family 集合。
- 不累加多个 topics 的分数。
- 不按最高分选择 topic，避免多标签自动抬分。
- 每种 `message_type` 都有显式、固定的路由优先级，因此 topics 输入顺序不影响结果。

## 四、Message type 到评分档案的路由

### 4.1 游戏消息

| message_type | 路由顺序 |
| --- | --- |
| `game_patch_notes` | `patch_official_notes` |
| `game_official_preview` | 玩法/云顶 -> `official_gameplay_preview`；其他 -> `official_content_preview` |
| `game_announcement` | 周免英雄 -> `weekly_free_champion_rotation`；安全 -> `security_notice`；服务 -> `service_notice`；活动 -> `activity_announcement`；商城 -> `commerce_announcement`；外观 -> `cosmetic_announcement`；云顶 -> `tft_announcement`；玩法 -> `gameplay_announcement`；其他 -> `game_announcement_general` |
| `game_notice` | 安全 -> `security_notice`；其他 -> `service_notice` |
| `game_promotion_interaction` | 活动/商城 -> `promotion_activity`；外观 -> `promotion_cosmetic`；玩法/云顶 -> `promotion_gameplay`；社区/攻略/媒体 -> `promotion_community`；其他 -> `promotion_general` |
| `game_community_notice` | 商城 -> 商城轮换档案；活动 -> `free_reward`；服务/安全 -> `community_service_notice`；玩法/云顶/外观 -> `community_game_notice`；其他 -> `community_notice_general` |
| `game_community_promotion_interaction` | 与官方游戏推广使用相同的推广档案路由，不因非官方身份额外加减分 |
| `game_leak` | 玩法/云顶/服务/安全 -> `leak_gameplay`；外观/商城/活动 -> `leak_content`；其他 -> `leak_general` |
| `game_community_discussion` | 攻略 -> `gameplay_guide`；其他 -> `game_discussion` |

`game_promotion_interaction` 与 `game_community_promotion_interaction` 永远只能进入推广档案，
不得进入正式玩法公告、版本预览、PBE 改动或其他高信息密度档案。两者评分相同，独立 code
只用于保留信源性质。`game_community_notice` 同样不会因为非官方来源而自动变成 leak。

### 4.2 赛事消息

| message_type | 路由顺序 |
| --- | --- |
| `esports_announcement` | 赛果 -> 赛果档案；赛程 -> `esports_schedule`；阵容 -> `roster_announcement`；其他 -> `esports_announcement_general` |
| `esports_promotion_interaction` | `esports_promotion` |
| `esports_rumor_speculation` | `esports_rumor` |
| `esports_community_discussion` | 赛事分析 -> `esports_analysis`；其他 -> `esports_discussion` |

赛事推广不会因为包含 `esports_matches`、`esports_schedule` 或 `esports_rosters` 而使用正式赛果、
赛程或转会档案。

### 4.3 英雄联盟宇宙、其他产品与 Riot 生态

| message_type | importance_profile |
| --- | --- |
| `lol_universe_announcement` | `universe_announcement` |
| `lol_universe_promotion_interaction` | `universe_promotion` |
| `lol_universe_leak` | `universe_leak` |
| `lol_universe_community_discussion` | `universe_discussion` |
| `other_lol_product_announcement` | `other_product_announcement` |
| `other_lol_product_promotion_interaction` | `other_product_promotion` |
| `other_lol_product_leak` | `other_product_leak` |
| `other_lol_product_community_discussion` | `other_product_discussion` |
| `riot_ecosystem_announcement` | 周边 -> `merch_release`；合作 -> `partnership`；媒体 -> `media_release`；其他 -> `riot_announcement` |
| `riot_ecosystem_promotion_interaction` | `riot_promotion` |
| `riot_ecosystem_leak` | `riot_leak` |
| `riot_ecosystem_community_discussion` | `riot_discussion` |
| `unknown` | `unknown`，固定 0 分 |

## 五、评分档案

下表均为 100 分制的“基准 / 下限 / 上限”。

### 5.1 正式游戏信息

| importance_profile | 含义 | 基准 | 下限 | 上限 |
| --- | --- | ---: | ---: | ---: |
| `patch_official_notes` | 正式版本说明 | 92 | 88 | 95 |
| `patch_full_preview` | 完整版本预览 | 90 | 87 | 93 |
| `official_gameplay_preview` | 官方玩法或云顶实质预览 | 86 | 80 | 93 |
| `official_content_preview` | 官方其他内容实质预览 | 76 | 66 | 84 |
| `gameplay_announcement` | 正式玩法公告 | 86 | 78 | 95 |
| `tft_announcement` | 云顶正式公告 | 82 | 74 | 90 |
| `activity_announcement` | 正式活动公告 | 72 | 62 | 82 |
| `cosmetic_announcement` | 正式外观公告 | 68 | 58 | 80 |
| `commerce_announcement` | 正式商城公告 | 58 | 48 | 70 |
| `weekly_free_champion_rotation` | 周免英雄轮换 | 50 | 44 | 56 |
| `game_announcement_general` | 其他游戏正式公告 | 62 | 52 | 76 |
| `patch_hotfix` | 热修复 | 72 | 62 | 84 |
| `service_notice` | 服务与运营通知 | 68 | 54 | 86 |
| `security_notice` | 安全与公平竞技通知 | 82 | 72 | 92 |

### 5.2 推广、社区提醒、爆料与讨论

| importance_profile | 含义 | 基准 | 下限 | 上限 |
| --- | --- | ---: | ---: | ---: |
| `promotion_gameplay` | 玩法、模式或云顶宣传 | 52 | 38 | 68 |
| `promotion_activity` | 活动或商城宣传 | 50 | 36 | 66 |
| `promotion_cosmetic` | 外观宣传 | 48 | 34 | 64 |
| `promotion_community` | 社区、创作、攻略或媒体互动 | 34 | 16 | 50 |
| `promotion_general` | 其他游戏推广 | 42 | 26 | 58 |
| `shop_daily_standard` | 商城普通轮换 | 48 | 42 | 54 |
| `shop_cosmetic_rotation` | 商城外观轮换 | 58 | 52 | 64 |
| `shop_rare_cosmetic` | 商城稀有外观轮换 | 66 | 60 | 72 |
| `shop_bulk_refresh` | 商城批量刷新 | 66 | 60 | 72 |
| `free_reward` | 免费奖励或活动提醒 | 62 | 52 | 75 |
| `activity_free_skin` | 确定性免费皮肤提醒 | 84 | 78 | 90 |
| `community_game_notice` | 社区玩法或内容提醒 | 54 | 42 | 68 |
| `community_service_notice` | 社区服务与安全提醒 | 58 | 46 | 72 |
| `community_notice_general` | 其他社区提醒 | 46 | 34 | 60 |
| `leak_gameplay` | 未确认玩法、云顶、服务或安全信息 | 62 | 50 | 75 |
| `leak_content` | 未确认外观、商城或活动信息 | 54 | 42 | 68 |
| `leak_general` | 其他未确认信息 | 50 | 38 | 64 |
| `gameplay_guide` | 玩法攻略 | 42 | 32 | 52 |
| `game_discussion` | 普通游戏讨论 | 34 | 16 | 55 |

### 5.3 赛事

| importance_profile | 含义 | 基准 | 下限 | 上限 |
| --- | --- | ---: | ---: | ---: |
| `esports_schedule` | 正式赛程 | 52 | 46 | 62 |
| `esports_regular` | 普通常规赛结果 | 60 | 54 | 68 |
| `esports_playoffs` | 季后赛结果 | 70 | 66 | 76 |
| `esports_final` | 赛区决赛结果 | 76 | 71 | 82 |
| `worlds_regular` | 世界赛普通场次 | 68 | 62 | 74 |
| `worlds_key` | 世界赛关键场次 | 80 | 74 | 86 |
| `roster_announcement` | 正式转会或阵容公告 | 62 | 54 | 74 |
| `esports_announcement_general` | 其他赛事正式公告 | 56 | 44 | 70 |
| `esports_promotion` | 赛事宣传、集锦和观赛引导 | 44 | 28 | 62 |
| `esports_rumor` | 赛事传闻与推测 | 50 | 38 | 68 |
| `esports_analysis` | 赛事分析 | 44 | 30 | 60 |
| `esports_discussion` | 普通赛事讨论 | 34 | 18 | 54 |

### 5.4 其他产品域

| importance_profile | 含义 | 基准 | 下限 | 上限 |
| --- | --- | ---: | ---: | ---: |
| `universe_announcement` | 英雄联盟宇宙正式公告 | 66 | 58 | 78 |
| `universe_promotion` | 英雄联盟宇宙推广 | 46 | 32 | 62 |
| `universe_leak` | 英雄联盟宇宙未确认信息 | 54 | 42 | 68 |
| `universe_discussion` | 英雄联盟宇宙讨论 | 38 | 22 | 56 |
| `other_product_announcement` | 其他英雄联盟产品正式公告 | 68 | 56 | 82 |
| `other_product_promotion` | 其他英雄联盟产品推广 | 46 | 32 | 62 |
| `other_product_leak` | 其他英雄联盟产品未确认信息 | 54 | 42 | 70 |
| `other_product_discussion` | 其他英雄联盟产品讨论 | 38 | 22 | 56 |
| `merch_release` | 实体周边正式发布 | 52 | 44 | 64 |
| `partnership` | 商业合作正式公告 | 58 | 50 | 68 |
| `media_release` | 媒体内容正式发布 | 58 | 48 | 70 |
| `riot_announcement` | 其他 Riot 生态正式公告 | 66 | 58 | 76 |
| `riot_promotion` | Riot 生态推广 | 46 | 32 | 62 |
| `riot_leak` | Riot 生态未确认信息 | 54 | 42 | 68 |
| `riot_discussion` | Riot 生态讨论 | 38 | 22 | 56 |
| `unknown` | 无法评分 | 0 | 0 | 0 |

## 六、档案内的专门识别

专门识别只在已经由 `message_type × topic family` 选定的合法范围内执行：

| 适用分类 | 识别结果 |
| --- | --- |
| `game_official_preview` | 正文明示“完整预览”或 `full preview` -> `patch_full_preview` |
| `game_notice` | 明示热修复、不停机更新等服务端更新 -> `patch_hotfix` |
| `game_announcement + gameplay` | 明示周免英雄、每周免费英雄或免费英雄轮换 -> `weekly_free_champion_rotation` |
| `game_community_notice + commerce` | 稀有/限定 -> `shop_rare_cosmetic`；批量刷新 -> `shop_bulk_refresh`；皮肤/炫彩 -> `shop_cosmetic_rotation`；否则 `shop_daily_standard` |
| `game_community_notice + activity` | 明确、确定性免费获得皮肤且不是抽奖或概率 -> `activity_free_skin`；否则 `free_reward` |
| `esports_announcement + esports_matches` | 根据世界赛、季后赛和决赛证据选择对应赛果档案 |

这些识别不能跨越消息类型边界。例如推广消息即使写有“完整预览”“决赛”或“现已上线”，仍只能
使用推广档案。

## 七、LLM 结构化特征与修正项

LLM 不输出分数，只提取：

| 字段 | 允许值 | 用途 |
| --- | --- | --- |
| `scale` | minor / standard / major | 同一档案内的影响规模 |
| `audience_region` | cn / global / international_only / unknown | 只影响排序优先级 |
| `competition_region` | lpl / lck / international / other / none | 赛事档案修正 |
| `prominence` | normal / notable / star | 赛果、阵容公告和赛事传闻修正 |
| `skin_tier` | none / standard / legendary / prestige_or_mythic / ultimate | 正式外观公告和外观推广修正 |
| `is_bulk_update` | true / false | 批量内容修正 |
| `evidence` | 1-6 条消息原文 | 审核依据，不直接计分 |

领域修正项：

| 修正项 | 适用范围 | 分值 |
| --- | --- | ---: |
| `scale` | `patch_hotfix`、`service_notice`、`security_notice` | minor -7 / standard 0 / major +9 |
| `scale` | 其他非 unknown 档案 | minor -3 / standard 0 / major +3 |
| `bulk_update` | 除商城批量刷新、完整预览、正式版本说明和周免英雄轮换外 | +3 |
| `competition_region` | 所有赛事档案 | LPL +3 / LCK 0 / 国际 +1 / 其他 -3 / none -3 |
| `prominence` | 赛果、阵容公告、赛事传闻 | normal 0 / notable +3 / star +7 |
| `skin_tier` | 正式外观公告、外观推广 | standard 0 / legendary +4 / prestige_or_mythic +6 / ultimate +10 |

“臻彩”不能单独作为至臻或神话外观证据；只有文本明确说明“至臻皮肤、神话皮肤、神话炫彩”等
档次时才能使用 `prestige_or_mythic`。

所有修正后的结果都必须限制在当前档案的下限和上限之间。

这些判断由 `score_importance_profile()` 集中实现。Message 先通过 `derive_importance_profile()` 从
整条消息派生 profile；EventMention 使用事件聚合已有调用输出的受控 event-specific profile。两者不
各自维护赛事、外观、活动、商城等另一套领域权重。

档案区间限制完成后，若 `content_form` 为 `repost`，消息重要性再扣 8 分，并限制在 0-100。
这是消息载体修正，不属于 Domain Importance，也不得进入 Event Importance。
这反映纯转发本身较低的编辑价值，也保证处于档案下限的转发仍能稳定减分。`original`、`quote`、
`media_only` 和 `link_only` 不在此步加减分；后两者按前述规则直接为 0。

## 八、排序优先级

消息类型已经决定消息信息价值，因此排序阶段不再对推广、讨论、提醒、爆料或传闻重复降分，也不再
使用 `information_stage`。

| 修正项 | 条件 | 分值 |
| --- | --- | ---: |
| 内容形式 | `quote` | -2 |
| 受众范围 | `international_only` | -12 |

`original`、`cn`、`global` 和 `unknown` 不修正。排序优先级限制在 0-100。

## 九、审计输出与不变量

每次计算保存：

- `policy_version`
- `message_type` 和完整 `topics`
- `importance_profile`
- 基准、上下限、修正项、修正合计、限制前分数、档案分和最终分数
- 排序优先级的独立修正项

必须满足：

1. 每个受控 `message_type` 都有评分路由。
2. topic 输入顺序不改变评分档案。
3. 多个 topics 不累加分数。
4. 同主题的推广档案低于正式公告档案。
5. 推广、讨论和社区提醒不能进入正式公告、正式赛果或官方预览档案。
6. `unknown` 固定为 0。
7. LLM 不得直接决定基准、上下限、修正值或最终分数。
8. `repost` 只在消息重要性中扣 8 分，排序阶段不得重复扣分。
9. 共享的是 Domain Importance Policy，不是整条 Message 的 `profile_score`。每个 material
   EventMention 针对其独立事件语义使用同一 scorer，并固化自己的 domain snapshot；Event 不读取包含
   消息载体修正的 `importance_score`。
