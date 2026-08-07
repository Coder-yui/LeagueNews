# 事件编辑规则 v3

事件是一个可以被证据验证的现实状态变化，不是相似消息的文件夹。事件准入、重要性和
可信度相互独立：低重要性的普通 LPL 常规赛结果仍可形成事件；低可信度的单源转会爆料
也可立即形成未确认事件。

## 事件准入与主题簇

`event_mentions` 只是消息内的语义证据，不能直接决定事件数量。程序先按事实主题、稳定主体、
时间锚点和产品范围生成主题簇，再由每个主题簇产生一条路由。皮肤、活动、比赛或修复对象的
数量都不能直接变成事件数量。

- LPL、LCK 普通比赛按联赛和比赛日形成 `esports_match + calendar_day`，稳定键为
  `matchday:{league}:{date}`。当天一场、两场或三场都使用同一个比赛日事件，预告、每场
  赛果和整日总结进入同一时间线。季后赛后段、决赛、明确焦点战和国际赛关键淘汰赛才按
  单场形成 `esports_match + timeline`，键为 `match:{date}:{team-a}-vs-{team-b}`。
  缺少联赛字段的普通单局消息，如果同日同双方唯一命中已有比赛日，也直接解析到该
  `matchday`；`scheduled -> live -> completed` 是生命周期变化。
- 明确包含选手或教练的转会事实形成 `roster_change + timeline`，稳定键为
  `transfer:{year}:{person}`。传闻可以形成 `unconfirmed` 事件，模糊暗示不能生成稳定路由。
- 有版本号的 PBE 变更、设计师预告、官方预告和正式公告进入同一
  `gameplay_update + patch_cycle`，稳定键为 `patch:{product_scope}:{version}`。无版本号热更新
  按产品和更新批次起始日期使用 `hotfix:{product_scope}:{date}`，多个修复对象只是批次组件。
  同产品、相隔不超过两天且核心修复对象重叠的爆料、预告和正式公告确定性续接同一批次。
  版本主题簇中的普通英雄或模式改动只是版本组件，不再额外生成玩法更新事件。
- 新英雄、新模式和云顶赛季使用 `gameplay_release + release`；新皮肤、单独报道的新炫彩、
  国服新臻彩和云顶外观使用 `cosmetic_release + release`。发布稳定键统一为
  `release:{product_scope}:{canonical_entity}`。
- 新皮肤配套炫彩即使被单独报道，也与新皮肤处于同一内容档和事件类型；商城轮换、返场、
  折扣仍属于 `commercial_offer`，免费领取则属于 `player_activity`，不能混成外观发布。
- 同一篇非汇总公告中同批上线的系列皮肤、炫彩、臻彩和签名版只形成一个外观发布事件，
  使用版本号或共同上线日期作为批次锚点，单款对象
  是该事件的组成内容；其售价、签名礼包、销售期和未来商城去向是发布属性，不另建商城事件。
  赛事冠军、战队和选手等设计背景也不另建赛事事件。只有汇总文章中彼此独立的发布批次才
  拆成多个事件。
- 玩法/模式发布与围绕它推出的通行证、礼包或付费活动是不同的事实变化，分别形成
  `gameplay_release` 与 `player_activity`/`commercial_offer` 事件。
- 同次更新共同预告或上线的多个活动形成一个 `activity_batch`；同一命名活动的公告、阶段
  开启、奖励开放和领取提醒仍是该命名活动的一条时间线。抽奖、概率奖励和付费活动均可形成
  普通活动事件，不因奖励稀有或“最高可得皮肤”升级为免费皮肤事件。
- 未命中专用主题时，只要有稳定命名主体和明确状态变化，仍可形成通用小事件，例如命名的
  社区活动。周免英雄、互动、二创、泛宣传和纯观点不形成事件。
- 互动、二创、泛宣传和纯观点不创建新事件。评论、提醒、否定和仅上下文提及生成的路由
  `creation_policy=existing_only`，只能加入已经存在且身份相同的事件。

一条消息可以形成多个不同主题簇，并以 `primary`、`component` 或 `cross_ref` 加入不同事件。
稳定键、`event_kind`、`aggregation_strategy`、`product_scope` 和事件数量均由程序生成。
热更新短窗口、活动限定名和比赛日上下文若唯一命中，程序会在模型调用前把临时路由解析成
已有事件的正式键；模型只能处理剩余既定路由的精确候选、可解释同义候选或新事件。

候选检索采用两阶段召回：

- `strong`：稳定键、RawItem 版本链、核心实体或标题明确命中。
- `broad`：没有精确身份信号，但处于该事件类型的合理时间范围内。宽候选携带事件标题、
  摘要和核心实体，交由事件编辑判断别名、缩写、旧译名、父子对象和附属内容关系。

单条分析遇到礼包、皮肤、图标、封面、奖励、截图和测试服资源等附属对象时，必须同时
提取文本明确指向的父级模式、英雄、赛事、版本或活动。候选存在时如果仍选择 `create`，
必须通过 `candidate_rejections` 逐一说明为何每个候选都不是同一事件，否则草稿校验失败
并自动重试。附属素材没有改变核心事件状态时使用 `context`，不增加 revision。

### 商城轮换

- 商城轮换使用 `commercial_offer + recurring_window`，按消息发布时间的中国时区 ISO 周生成
  `shop_rotation:{product_scope}:{market}:{ISO_YEAR}-W{ISO_WEEK}`，`market` 为 `cn` 或
  `global`。
- 同一市场、同一产品、同一周的轮换进入同一事件；国服与外服、跨产品或跨周不合并。
- 每日、稀有、批量和外观轮换影响消息内在重要性档位，但不改变事件身份键。
- 外服商城轮换仍形成每周事件，但事件重要性在最高成员贡献基础上下调 12 分。

## 事件更新

`update_kind` 区分：

- `new_fact`、`confirmation`、`refutation`、`correction`：构成显著更新并增加 revision。
- `context`、`duplicate_evidence`：可以补充证据和改变可信度，但不增加 revision，也不
  改写最新进展。

证据使用 `supports`、`contradicts`、`context` 三种立场。

### 同一来源文档的版本链

`RawItem.supersedes_raw_item_id` 表示同一来源文档的新采集版本，不表示新的消息或独立
来源：

- 消息列表和事件处理台只展示版本链中没有 successor 的最新版本。
- 已被取代的 NormalizedItem 不允许启动或批准事件聚合。
- 新版替代已经属于事件的旧版时，原位替换 `event_messages` 成员，不追加第二条证据。
- 页面修订没有实质新事实时使用 `duplicate_evidence`，不增加事件 revision；确有新增
  事实时才生成新的事件 revision。
- 旧 RawItem 和旧 NormalizedItem 保留用于审计，不从数据库删除。

## 可信度

证据先按 `independence_key` 去重。原创使用 `source:{source_id}`；转载使用规范化的
`upstream:{host}{path}`；无法识别上游的转载不参与加成。同一账号或同一上游只贡献一次。

非官方仅有支持证据时：

```text
base = max(independent supporting source reliability snapshot)
boost = 0.1 × min(independent supporting source count - 1, 3)
credibility = min(0.9, base + boost)
```

只有原始官方来源直接确认其权责范围内的核心事实，并经事件审核确认后，
`is_official_evidence` 才为真。该值由程序根据 `Source.is_official`、是否直接原创和证据立场计算；官方转发不算直接确认。官方支持将事件标为
`official_confirmed` 且可信度为 `1.0`；官方否认将核心主张标为 `officially_refuted`。
官方支持与反对同时存在、或非官方支持与反对同时存在时均为 `disputed`、可信度 `0.5`。

## 重要性

消息和事件重要性使用 100 分制展示，完整可执行细则见
[`IMPORTANCE_SCORING_POLICY.md`](./IMPORTANCE_SCORING_POLICY.md)。当前消息政策为
`importance-v8-official-updates`，事件政策为 `event-importance-v5-component-baselines`。
模型只提取受控结构化特征，程序按版本化基准、有限修正项和子类型区间确定性计算。
紧迫性与可信度均不进入内在重要性。

事件重要性不累加成员消息数量，也不因官方身份或独立信源数量加分。它取所有有贡献成员
折算分的最高值；附属事件按事件类型标准基准封顶，不再因成员角色和消息主主题双重折损。
`context` 不参与；`duplicate_evidence` 参与最高值但不累加，因此不会靠重复消息抬高分数。

公开卡片展示事件类型、生命周期、重要性、可信状态、独立来源数、证据数和最新进展；
内部聚合键与 revision 历史保留在详情审计区。
