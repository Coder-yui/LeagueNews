# 事件聚合系统总设计

> 状态：Phase 0–5 已完成
>
> 设计版本：`event-aggregation-v1`
>
> 更新时间：2026-08-11

## 目标与非目标

事件层消费已经发布的 `NormalizedItem`，把可验证的现实状态变化维护成可持续更新的事件。
一条消息可以产生多个 `EventMention`，一个事件也可以累积多条消息证据。事件重要性、可信度和
热度分别计算，事件卡片展示事件当前状态而不是随机挑选一条消息。

本阶段不修改已经稳定的消息分类枚举、消息重要性算法、翻译、OCR、通用摘要或实体提取；不让
Connector 或 RawItem 承担事件成员关系；不引入向量数据库、新微服务或第二套消息流水线。

## 当前仓库审查结论

### 当前运行时

Phase 0 审查时主链路在 `NormalizedItem` 发布后停止：

```text
RawItem
  -> relevance
  -> optional image_ocr
  -> translation
  -> message_analysis
  -> importance
  -> NormalizedItem(published)
  -> stop
```

可直接复用的输入如下。

| 事件输入语义 | 当前字段或关系 | 备注 |
| --- | --- | --- |
| 标题、标准化正文、摘要 | `normalized_title`、`normalized_text`、`summary` | 优先使用译后标题和正文 |
| 翻译与结构化内容块 | `translated_title`、`translated_text`、`translated_content_blocks` | 不在事件阶段重复翻译 |
| 产品与内容形式 | `products`、`content_form` | 当前 taxonomy v3 |
| 消息类型与主题 | `message_type`、`topics` | 用于准入和 event family 路由 |
| 通用实体 | `entities` | 已有规范类型、名称和 `canonical_id` |
| 消息重要性 | `importance_score`、`importance_dimensions` | 只作为输入上下文，不上卷为事件重要性 |
| 分类信源 | `facets.classification_source` | 只说明本轮分类依据，不等于事件官方证据 |
| 来源可靠性 | `RawItem.source.is_official/reliability_score` | 必须结合事件中的实际角色解释 |
| 原始/引用/转发证据 | `content_form`、`RawItem.content_blocks`、`provenance` | 只读；通过结构化 URL 识别上游 |
| 发布时间 | `RawItem.published_at`，缺失时 `ingested_at` | 统一转 UTC 计算窗口 |
| OCR 结果 | `NormalizedItem.media_links` 及译后结构数据 | 只选择相关片段，不重复 OCR |
| 原文版本链 | `RawItem.supersedes_raw_item_id` | 同源修订不是新独立证据 |

当前 `Source` 只有账号级 `is_official` 与 `reliability_score`，没有“对某一事件是否为权责主体”字段；
因此官方确认必须由结构化事件输出提出 `source_role`，再由代码结合 Source、内容形式和上游关系
校验，不能仅凭 `is_official=true` 判定。

### 已退役的事件对象

迁移 026–055 曾创建并演进一套旧事件表和审阅工作流。Phase 0 开始时对应 ORM、路由、工作流、
前端与测试已经删除，`docs/history/EVENT_EDITORIAL_POLICY.md` 也明确是历史文档。已有迁移作为账本
历史保持不动；新的 063 迁移在升级点删除旧事件数据、旧审阅任务、旧 claim 关联和失效的 pipeline
外键，然后按 event-aggregation-v1 模型重新创建事件表。新系统不读取、转换或回填旧事件。

旧设计的审查只用于识别不应继续继承的约束，主要差距如下。

- `event_messages` 的 `(event_id, normalized_item_id)` 主键不能表达 mention 索引和完整作用语义。
- 旧重要性从成员消息分数上卷，不符合新的事件影响维度算法。
- 旧可信度只有支持/反对/上下文三种立场，来源角色与权责范围表达不足。
- 没有事件热度、时间衰减、24 小时统计或四类展示消息引用。
- 旧运行时曾先拆预生成路由再做决定，不能作为“一条消息默认一次事件模型调用”的实现依据。
- 当前自动 worker 在消息发布后停止，没有事件任务入队、API 或 UI。

新实现只有一套事件运行时，不恢复旧代码，不保留兼容字段，也不提供旧事件回填路径。本任务只
生成迁移文件，不连接或修改生产数据库。

## 领域模型

### Event

`Event` 是当前事件投影，修订表保存历史。目标字段分组如下。

```text
identity
  id, event_family, products[], canonical_anchors{}, aggregation_key
presentation
  title, current_summary, latest_development, key_facts[], unresolved_points[]
state
  lifecycle_status, first_seen_at, last_seen_at, last_material_update_at
metrics
  importance_score, importance_breakdown
  credibility_score, credibility_level
  heat_score, heat_calculated_at
  message_count_total, message_count_24h, unique_sources_24h
references
  origin_message_id, primary_source_message_id
  latest_update_message_id, best_media_message_id
governance
  aggregation_policy_version, importance_policy_version
  credibility_policy_version, heat_policy_version, current_revision
```

数据库直接使用 `event_family`、`first_seen_at`、`last_seen_at` 等 v1 字段；`summary` 在应用层暴露为
`current_summary`。`products` 和 `canonical_anchors` 使用 JSON，schema 和业务校验负责受控结构。

### EventMention / EventEvidence

`event_mentions` 是新系统的独立证据表。每条记录至少包含：

```text
id, event_id, normalized_item_id, normalized_item_revision, mention_index
relation, source_role, independence_group, materiality
evidence_excerpt, structured_fact_changes, content_fingerprint
source_published_at, source_reliability_snapshot, created_at
```

`relation`：`reports | supports | confirms | denies | corrects | mentions`。

`source_role`：`responsible_official | direct_subject | first_party_participant |
independent_media | known_leaker | ordinary_account | republisher | unknown`。

`materiality`：`material_update | corroboration_only | duplicate | context_only`。

数据库唯一约束使一次完成的 mention 以 `(normalized_item_id, normalized_item_revision, mention_index,
aggregation_policy_version)` 幂等；聚合运行还以
`(normalized_item_id, normalized_item_revision, aggregation_policy_version)` 幂等。
同一运行的模型响应先完整校验，再在一个事务中应用全部 mention。失败时不允许只更新一部分事件。

### EventAggregationRun

运行记录保存准入决定、候选快照、模型调用次数、严格 schema 响应、状态、错误和策略版本。
`skip` 以及无候选的 `update_existing_only` 也保存 0 调用结果，便于评估调用预算。

## 完整处理流程与调用预算

```text
published NormalizedItem
  -> deterministic admission
       skip ------------------------------> completed (0 calls)
       update_existing_only + no candidate -> completed (0 calls)
  -> deterministic family routing and candidate recall
  -> build one bounded message + candidates payload
  -> one structured event model call -> EventMention[]
  -> schema + business validation
  -> atomic apply/reconcile
  -> deterministic importance / credibility / heat projection
  -> completed run
```

默认每条消息事件模型调用数为 0 或 1。只有严格输出校验重试或确实超过上下文限制的分块异常可
增加调用，并必须记录 `model_call_count`、原因和分块信息。禁止按 topic、mention 或候选事件循环
调用。摘要、关键事实、未决问题和最新进展的 proposed changes 必须包含在同一次响应里。

## 核心验收示例

1. 爆料者发布“下版本平衡改动”，准入为 `create_or_update`，一次调用创建事件 A。
2. 同一或另一爆料者发布“下版本活动”，一次调用创建事件 B。
3. 官网随后发布同时包含平衡、活动和皮肤的综合版本公告。
4. 代码一次召回 A、B 及相关候选；整条官网消息只调用一次事件模型。
5. 返回三个 mention：`update A / confirms`、`update B / confirms`、
   `create C / reports`。
6. 一个事务写入三条证据、更新 A/B、创建皮肤事件 C；各事件使用独立影响维度。官网消息可让
   A/B 进入 `officially_confirmed`，但不会因为官方身份自动提高三个事件的重要性。

## 分阶段文件级实施计划

### Phase 1：模型和持久化（已完成）

- `services/api/app/models/event.py` 恢复 Event、EventMention、revision 和 run ORM，并建立
  NormalizedItem 反向关系。
- `services/api/app/domain/event_types.py` 集中枚举与四个策略版本号。
- `services/api/app/repositories/events.py` 与 `services/api/app/services/events.py` 提供最小读写、
  mention 幂等、修订和时间投影。
- `infra/postgres/migrations/063_replace_event_system_with_v1.sql` 删除退役事件层并创建干净的 v1 表；
  Source、RawItem、NormalizedItem、媒体、规则和消息处理历史不受影响；未编辑任何旧迁移。
- `services/api/tests/test_event_service.py` 覆盖多对多、幂等、时间、状态与修订；迁移账本测试已扩展。

最终迁移验证已在明确可销毁的 PostgreSQL 16 容器完成：全新数据库按当前 ORM 初始化成功；从旧 031
夹具顺序执行 032→063 成功，预置旧事件被删除，只留下空的 v1 事件表。

### Phase 2：准入、召回和一次调用（已完成）

- `domain/event_admission.py` 和 `event_families.py` 实现 0 调用准入、family hints 与 anchors。
- `services/event_candidates.py` 在现有数据库中做受窗口、产品、family、anchor 和文本约束的规则召回。
- `schemas/event_aggregation.py` 与 `LLMClient.aggregate_events` 提供一次多 mention 严格 schema；Prompt
  已注册为 `event-aggregation/v1-multi-mention-single-call`。
- `workflows/event_aggregation.py` 保存调用审计，在一个外层事务中应用全部动作；
  `automatic_pipeline.py` 在消息成功发布后调用该工作流。
- 固定输出测试验证官网综合公告一次调用更新 A/B、创建 C，0 调用路径、超长正文相关块选择、
  重试幂等以及任一动作失败时整批回滚。

Phase 2 首轮全量验证：Ruff 通过；后端 `164 passed, 1 skipped`，随后新增的超长正文测试也通过。
模型结构校验失败最多由统一 LLM 边界重试一次，实际次数写入 `model_call_count`。

### Phase 3：三个独立指标（已完成）

- `domain/event_importance.py`、`event_credibility.py`、`event_heat.py` 分别实现确定性计算；
  `services/event_metrics.py` 只负责从持久化 mention 组装输入和刷新投影。
- migration 063 为每条 mention 保存事件 impact 快照；基准、权重、阈值与策略版本集中管理。
- 重要性只在 material update 更新；可信度按同一 Source/上游去重；热度按写入即时刷新并保存
  计算时间，跨 Source 转载保留传播权重。
- 测试覆盖高重要性低热度、低重要性高热度、两个独立来源、官方确认/否认、20 次转载、同源限流、
  完全重复和时间自然衰减。

Phase 3 全量验证：Ruff 通过；后端 `170 passed, 1 skipped`。

### Phase 4：展示投影、API 和前端（已完成）

- `schemas/event.py`、`services/event_presentation.py`、`api/routes/events.py` 提供事件列表和详情，
  读取时对超过 5 分钟的热度缓存执行按需刷新。
- 展示投影分别维护 origin、primary source、latest update、best media；timeline 只包含 material mention。
- `apps/web/app/events`、前端类型和 API client 提供事件卡片与详情；保留现有消息页和首页消息流。
- 后端展示测试、前端 ESLint 和 Next.js production build 均通过。

### Phase 5：回归、评估和清理（已完成）

- 固定结构化模型 mock 覆盖 15 类核心链路，不访问在线模型。
- 全量 Ruff、175 项后端测试、web lint 和 production build 通过；1 项既有 opt-in PostgreSQL 测试
  因未配置 URL 跳过。
- 不提供旧事件重聚合脚本；新系统只处理接入后发布或显式重新运行消息流水线产生的消息。
- 旧运行时代码、事件数据和兼容字段都不进入 v1；历史迁移账本和 RawItem 证据保持不变。
- PostgreSQL 16 已验证当前 schema 的全新初始化、旧 031→063 顺序升级和现有 pipeline 并发测试；
  临时容器在验证后已自动删除。

## 边界情况

- 已撤回或被 RawItem 新修订取代的 `NormalizedItem` 不启动新聚合；新的消息修订使用独立运行键。
- `media_only`、`link_only`、全 unknown 消息直接 0 调用。
- 普通转载可以增加热度，不能成为独立确认，也不能重写摘要。
- 候选相似但强身份锚点冲突时必须创建新事件或忽略，不能仅靠文本相似度合并。
- 多 mention 中任意一项校验失败，整条响应不应用；允许一次受控重试。
- 超长公告优先用译后结构块、摘要、实体、主题和相关段落裁剪；v1 超限后失败，不静默分块增加调用。

## 当前未解决问题

- Source 是否需要增加按产品/职责范围配置；v1 先由受控 `source_role` 与结构证据校验，避免扩大
  Source 模型。
- 热度采用按需刷新后，是否还需要定时批量刷新热门列表，将由真实流量决定。

## 实现对应与可调参数

本文负责总边界、流程和阶段计划。准入与拆分见
[`EVENT_ADMISSION_AND_GRANULARITY.md`](EVENT_ADMISSION_AND_GRANULARITY.md)，三个指标与展示分别见
对应子文档。各子文档的实现对应已更新为实际模块。

可调参数包括候选上限、上下文字符预算、输出重试次数和运行策略版本；这些
参数必须集中配置并进入运行审计，不能散落在 Prompt 或业务 if/else 中。

## 最终验收结果

- 非事件消息与无候选转载为 0 次事件模型调用；普通事件消息为 1 次。
- 严格输出校验失败时统一边界最多重试一次，实际次数进入 `model_call_count`。
- 一次复杂官网公告已由回归测试证明可以更新 A、更新 B、创建 C，且整批事务原子。
- `EventMention` 实现消息与事件多对多，并以消息 revision、mention index 和策略版本保证幂等。
- 事件重要性、可信度和热度使用三个独立模块与版本；转载只影响热度，不伪造独立证据。
- 摘要、关键事实和时间线只随 material update 更新；普通讨论和转载仍可进入证据/相关消息。
- 公开事件卡片和详情展示当前事件投影、四类消息引用、三个指标、实质时间线和证据。

上线前重点人工审核：family/准入映射是否符合编辑口径；事件影响基准是否符合产品排序；
`responsible_official` 的权责判断；以及生产执行 063 前的备份、停写窗口和回退方案。063 会有意删除
旧事件层，不能把旧事件表当作回退来源。
