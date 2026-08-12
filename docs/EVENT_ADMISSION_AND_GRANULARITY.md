# 事件准入、粒度、召回与单次调用

> 状态：Phase 2 已实现
>
> 策略版本：`event-aggregation-v2-mention-importance`

## 目标与非目标

本规则在不调用模型时过滤明确非事件消息、确定事件 family 和候选范围；随后以整条
`NormalizedItem` 为单位进行至多一次结构化模型调用，返回全部 `EventMention[]`。

准入不重新做消息分类或通用实体提取，不用消息 topic 数量决定事件数量，不按候选逐个问模型，
也不因追求召回率在 v1 引入向量数据库。

事件粒度的核心原则是：**独立生命周期 + 独立更新 + 用户认知上的独立事情**。商品、奖励、组成部分、
子项目和附件默认进入主 Event 的 `key_facts`、structured facts 或 components，不因为实体或 topic
数量自动拆分。

本轮边界规则：

- 周免英雄（中文“周免英雄/免费英雄轮换”、英文 `free champion rotation`）在 event admission 层
  `skip`，保存明确原因且 0 次事件模型调用；英雄平衡调整和新英雄发布不命中排除规则。
- 神话商店轮换按每个市场、每个轮换周期一个 `commercial_offer` Event。规范化 anchors 为
  `shop=mythic_shop`、`market`、`rotation_period`；同市场同周期的补充/修正 update，下一周期或另一
  市场 create。轮换商品都是该事件的 facts/components。
- 普通电竞比赛按每场真实比赛一个 `esports_match` Event，适用于 LPL、LCK、LEC 等所有赛事。每日
  赛前预告或赛后总结只创建/update 对应比赛，不创建 Daily Preview、Daily Schedule、Daily Summary
  或 Results Summary Event。正式公布未知赛程、延期、提前、重赛、场地/对阵/赛制变化仍可进入
  `esports_schedule`。
- 活动、付费活动、通行证、活动商店和奖励体系内的皮肤、臻彩、图标、边框、表情、代币、战利品和
  其他商品默认归活动主 Event；只有子对象拥有独立发布日期和后续生命周期时才允许另建 Event。

## 输入与输出

输入是已发布且位于最新 RawItem 版本链末端的 `NormalizedItem`，以及只读的 Source、发布时间、
引用/转发结构和已批准 OCR 结构。输出分两层：

```text
AdmissionDecision
  decision: skip | update_existing_only | create_or_update
  family_hints[]
  reasons[]
  strong_anchors{}

EventAggregationResult
  mentions[]
  ignored_fragments[]
  input_truncation{}
```

## 事件 family

| family | 典型 topics/实体 | 默认时间窗 |
| --- | --- | --- |
| `gameplay_balance` | balance、champion/item/rune、patch | 45 天 |
| `gameplay_release` | champion、game_mode、tft_gameplay | 120 天 |
| `cosmetic_release` | cosmetics、skin、patch | 120 天 |
| `player_activity` | activities_rewards、activity | 90 天 |
| `commercial_offer` | shop_monetization、skin/activity | 35 天 |
| `service_incident` | service_technical、system | 14 天 |
| `security_enforcement` | security_fair_play | 90 天 |
| `esports_match` | esports_matches、team/tournament | 14 天 |
| `esports_schedule` | esports_schedule、league/tournament | 120 天 |
| `roster_change` | esports_rosters、player/team | 180 天 |
| `esports_rules` | esports_competition、league | 180 天 |
| `universe_release` | lore_universe、media | 365 天 |
| `media_release` | media_entertainment、product/person | 365 天 |
| `corporate_change` | corporate_partnerships、organization | 365 天 |
| `platform_service` | platform_services、system/product | 180 天 |
| `other_named_development` | 稳定命名主体与明确状态变化 | 90 天 |

`topics -> family` 是候选集合映射，不是一对一最终分类。模型可以在受控 family 列表中修正
family，但不能创造枚举外类型。`products` 原样复用，最多三个。

## 零调用准入

规则按顺序执行，并把命中原因保存到运行记录。

### `skip`

- `publication_status != published` 或不是最新 RawItem 修订。
- `content_form in {media_only, link_only}`。
- `products=[unknown]`、`message_type=unknown`、`topics=[unknown]` 且无可用核心实体。
- editorial exclusion：周免英雄/免费英雄轮换。
- 明确的纯观点、指南、泛互动、二创、赛事集锦或推广，且没有版本、日期、命名活动、比赛、
  发布对象等强锚点。
- 完全重复采集或已存在相同成功运行。

### `update_existing_only`

- `content_form=repost`：只能补充已有事件的讨论/转载证据；无候选时 0 调用。
- 社区讨论、推广互动、分析类消息没有明确新状态变化，但含强锚点；只允许
  `update | ignore`。
- 仅提醒、回顾或对旧事实的上下文提及；不允许仅因摘要相似创建事件。

### `create_or_update`

- 正式公告、版本说明、官方实质预览、服务通知、赛事公告。
- 游戏爆料、电竞传闻、其他产品/宇宙/生态爆料，且至少有一个核心实体或稳定时间/名称锚点。
- `quote` 或原创讨论明确提供了新的可核验事实变化，而不只是观点。
- 推广类消息同时具备强发布锚点和明确状态变化，例如“命名皮肤系列于 8 月 20 日上线”。

准入只做安全的上限约束。不能从 taxonomy 确定是否独立成事件的内容进入一次模型调用，由
`mentions[].action=ignore` 处理。

## 粒度与 canonical anchors

一个 mention 对应一个可独立验证、拥有自己的生命周期或影响范围的事实变化。一个 topic 不等于
一个事件；同批多个组件也不必拆开。

强锚点键包括：

```text
patch_version, release_name, activity_name, champion, skin_series,
team, player, match, league, tournament, region, effective_at,
window_start, window_end, product
```

粒度规则：

- 同一版本公告中的平衡、活动和皮肤发布是可独立更新的 family，应拆为多个 mention。
- 同一平衡批次中的多个英雄/装备改动通常是一个 `gameplay_balance` 事件，组件进入 `key_facts`。
- 同批上线的命名皮肤系列通常是一个 `cosmetic_release`；单款皮肤不是自动拆分条件。
- 一个命名活动的预告、开启、奖励开放、延期和结束进入同一事件时间线。
- 转会按人员与赛季窗口识别；同一人同一窗口的爆料、确认、否认属于同一事件。
- 普通常规赛可按明确比赛标识；缺少稳定比赛锚点时不凭战队名和同日相似文本强行合并。
- 宣传、观点、复述和附属素材没有独立状态变化时返回 `ignore` 或 `context_only`。

`aggregation_key` 由代码根据标准化 anchors 生成，模型只能提供/修正 anchor 值，不能自由输出
最终 key。无足够强锚点的事件可使用带命名主体、family、产品和时间桶的受控散列 key。

## 候选召回

召回先确定 family hints，再对每个 family 评分：

| 信号 | 分值 |
| --- | ---: |
| aggregation key 或唯一强 anchor 完全一致 | 100 |
| patch / match / named activity 等核心 anchor 一致 | 60 |
| family 与产品重合 | 20 |
| 核心实体 canonical id 重合 | 每个 12，最多 36 |
| topics 支持同一 family | 10 |
| 标题/摘要规范化 token 相似 | 0–20 |
| 位于 family 时间窗内且事件活跃 | 8 |
| 强 anchor 冲突 | -100 并排除 |
| 已关闭且超出时间窗 | -40 |

每个 family 取 5 个，整条消息去重后最多 12 个；100 分精确候选优先。候选快照包含当前标题、
摘要、anchors、关键事实、未决点、生命周期、重要证据摘要和匹配原因。文本相似只能辅助，不能
覆盖版本号、比赛、人员窗口等强冲突。

## 一次模型调用 schema

每个 mention 严格表达：

```text
mention_index: integer
event_family: enum
action: create | update | ignore
candidate_event_id: integer | null
relation: reports | supports | confirms | denies | corrects | mentions
source_role: enum
materiality: material_update | corroboration_only | duplicate | context_only
canonical_anchors: object
event_title: string | null
proposed_summary: string | null
latest_development: string | null
key_fact_changes: {add[], replace[], remove[]}
unresolved_point_changes: {add[], resolve[]}
importance: {profile, scale?, competition_region?, prominence?, skin_tier?, is_bulk_update?}
evidence_excerpt: string
candidate_rejections: [{event_id, reason}]
```

业务校验至少要求：

- `create` 不得携带 candidate id，并满足 family 的最小 anchor 要求。
- `update` 必须指向输入候选，family 与强 anchors 不冲突。
- `ignore` 不写事件，可解释为什么不是独立事实。
- `corroboration_only/duplicate/context_only` 不得修改摘要、关键事实或最新进展。
- `confirms/denies/corrects` 必须有支持该关系的 excerpt；Source 角色由代码二次校验。
- 选择 `create` 时要解释所有同 family 强候选为何不是同一事件。
- mention_index 在响应内连续、唯一；响应外再与本次策略版本形成幂等键。

## 输入裁剪与异常调用

正常输入按“标题、摘要、核心实体/主题、译后正文相关段落、OCR 结构、候选状态”的顺序构造。
正文按 family/anchor 相关段落选择，保留原 block id 以便 excerpt 追溯。若仍超预算：

1. 候选只携带当前投影、关键事实和未决点，不携带完整历史。
2. 正文超过 24,000 字符时，按现有实体选择译后结构块并记录选中的 block index；无结构块时才
   使用受控截断，不重新摘要原文。
3. 当前 v1 不自动分片；仍超过 provider 上下文时本次运行失败，避免悄悄突破一次调用预算。未来若
   增加分片，必须作为明确异常并计入 `model_call_count`。

分片不是默认的“每个事件一次调用”，运行记录必须标记 `exception_reason=context_limit`。

## 核心示例

爆料平衡消息创建 A，爆料活动消息创建 B。官网版本公告的输入包含 A、B 以及同 family 候选，
一次响应为：

```json
{
  "mentions": [
    {"mention_index": 0, "action": "update", "candidate_event_id": 101,
     "event_family": "gameplay_balance", "relation": "confirms",
     "materiality": "material_update"},
    {"mention_index": 1, "action": "update", "candidate_event_id": 102,
     "event_family": "player_activity", "relation": "confirms",
     "materiality": "material_update"},
    {"mention_index": 2, "action": "create", "candidate_event_id": null,
     "event_family": "cosmetic_release", "relation": "reports",
     "materiality": "material_update"}
  ]
}
```

它只消费一个模型响应；A、B、C 的影响维度、摘要改动和证据分别校验并在同一事务应用。

## 边界情况

- 多 topic 但只有一个状态变化：只返回一个 mention，其余片段进入 `ignored_fragments`。
- 候选为空且准入是 `update_existing_only`：0 调用结束，避免转载创建事件。
- 候选为空且准入是 `create_or_update`：调用一次，允许创建。
- 相同实体不同版本、不同比赛或不同转会窗口：强锚点冲突，禁止合并。
- 同一 Source 短期重复发布：可以形成不同 mention 记录用于审计，但 materiality/热度会降权，
  independence_group 不增加可信来源数。
- 响应第三个 mention 校验失败：前两个也不落库；整条响应重试或失败。

## 实现对应、可调参数与未解决问题

实现位于 `domain/event_admission.py`、`domain/event_families.py`、
`services/event_candidates.py`、`schemas/event_aggregation.py` 和
`workflows/event_aggregation.py`。Prompt 通过现有 registry 管理，枚举和业务校验保留在代码中。

可调参数集中为 family 时间窗、每 family/总候选上限、召回权重、最低候选分、输入字符/token
预算和最多校验重试次数。需要用回归集校准“推广含强发布事实”的准入边界，以及普通比赛的最佳
稳定 anchor；在有评估证据前不增加向量召回。
