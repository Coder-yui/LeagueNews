# LoL Daily Intel 开发 Handoff

更新时间：2026-07-27

工作区：`E:\leagueNews`

本文是后续开发对话的权威入口。当前事实以代码、迁移和本文为准；旧的事件/报告实现
说明已经清理。

## 1. 当前完成边界

```text
平台内容
  -> Source 对应的平台 Connector
  -> RawItemCandidate
  -> shared ingestion
  -> 不可变 RawItem + MediaAsset + provenance
  -> 相关性 AI
  -> 可选 Patch 图片 OCR / 确定性结构化
  -> 翻译
  -> 实体、摘要、重要性、可信度分析
  -> 事件判断与聚合
  -> 自动发布，或按阶段进入人工审核
```

这条基座已经工作，事件聚合不得反向改写它。

- Connector 是平台级能力；Source 是具体账号或站点。新增平台实现新 Connector，新增账号
  通常只新增 Source。
- `raw_items.content_blocks` 是不可变原文唯一事实来源，包含文字、图片位置和外部媒体入口。
- 图片本地化；视频等媒体以“请在原始位置查看”的链接展示。
- 新采集的 RawItem 默认进入持久化自动任务，由独立 worker 自动跑完整条链路。
- 每个阶段的已接受输出保存为不可变 checkpoint；自动决定和人工决定明确区分。
- 已发布消息可以选择从相关性、OCR、翻译、分析或事件判断重新开始，并选择后续人工或
  自动模式。RawItem 始终不改写。
- 消息列表按 `coalesce(raw_items.published_at, raw_items.ingested_at)` 降序排列。
- 事件聚合 v2 已有核心模型、事务服务、只读路由、确定性候选检索、受审核 AI 工作流、
  管理台入口和公开事件页面；报告尚未实现。

## 2. 单条处理的当前顺序

权威细节见 [`REVIEWED_AI_WORKFLOW.md`](REVIEWED_AI_WORKFLOW.md)。

1. AI 判断是否与 LoL 产品范围相关，人工审核。
2. RiotPhroxzon 的版本预览类图片进入现有 OCR 分支。
3. OCR 表格结构通常可靠，人工修正文字后做最终确定性结构化；不要改生产 OCR 参数。
4. 先翻译标题、内容块和结构化图片数据，再进行翻译与术语审核。
5. 分析阶段只读取已批准中文内容，不加载术语表；加载 `analysis` 知识规则。
6. 提取 2–4 个核心实体，最多 5 个。版本预览优先版本号和文档类型，不把图片中的批量
   英雄/装备都当作资讯级实体。
7. 人工批准分析与摘要后写入 `NormalizedItem`。

知识按调用用途加载，不是每次把全部知识交给模型：

- 相关性阶段：`relevance` 规则。
- 翻译阶段：翻译规则和 `glossary_terms`。
- 分析阶段：`analysis` 规则，不加载术语表。
- OCR 人工修订：保存具体修订，不沉淀通用知识。

翻译驳回允许只提交一项或多项术语修正，不强制填写文字理由。知识整理 API 会让 AI
压缩、去重现有规则，但保留审核来源和版本记录。

### 2026-07-27 运行数据快照

通过当前本地 API 核验：

- `raw_items = 98`
- 已发布 `NormalizedItem = 71`
- `normalized_item_revisions = 71`（029 迁移为全部历史消息建立 revision 1）
- `knowledge_rules = 15`
- `glossary_terms = 32`
- `events = 16`
- active `event_messages = 37`
- `pipeline_jobs = 0`（迁移完成时；新采集才入队）

上述是交接时快照，不应写进业务逻辑。知识、术语和已批准消息都要保留。

生产 OCR profile 是当前唯一激活的 `production-2026-07-25`：

```json
{
  "scale": 2,
  "grayscale": false,
  "contrast": 1,
  "sharpness": 1,
  "text_score": null,
  "box_thresh": null,
  "unclip_ratio": 1.2,
  "use_cls": true,
  "divider_x_ratio": null,
  "line_brightness": 105,
  "line_coverage": 0.82
}
```

事件聚合开发不应改动或重新激活 OCR 参数。

## 3. 当前数据职责

| 表 | 当前职责 |
| --- | --- |
| `sources` | 具体账号或站点及 Connector 配置 |
| `connector_runs` | 一次采集运行及统计 |
| `raw_items` | 不可变、版本化的统一原始内容 |
| `raw_item_source_payloads` | 脱敏后的平台 provenance |
| `media_assets` | 原始媒体引用、本地路径和内容块位置 |
| `processing_runs` | 单条消息一次处理运行 |
| `review_tasks` | 阶段草稿、人工决定和反馈 |
| `knowledge_rules` | 按 `knowledge_type` 与 scope 使用的相关性/分析/翻译规则 |
| `glossary_terms` | 原词到标准中文术语 |
| `media_extractions` | OCR、表格结构与人工修订版本 |
| `normalized_items` | 已发布消息的当前投影、当前 revision 和发布/撤回状态 |
| `normalized_item_revisions` | 消息每次发布的不可变内容快照 |
| `normalized_item_media_extractions` | 消息采用的图片结构及一一对应中文译文 |
| `events` | 持续演化的事件当前状态 |
| `event_messages` | 事件成员及 active/withdrawn 生命周期；仅 active 成员全局唯一 |
| `event_revisions` | 事件标题、摘要、变更原因和证据快照历史 |
| `event_aggregation_runs` | 一条已批准消息的一次事件聚合运行及候选快照 |
| `event_review_tasks` | AI 事件决策草稿、人工决定和反馈 |
| `processing_checkpoints` | 每个已接受阶段的输出、引用、模型和决定来源 |
| `pipeline_corrections` | 撤回目标、重跑阶段、人工/自动模式和状态 |
| `pipeline_jobs` | 可持久化的全自动任务、失败阶段和最后有效 checkpoint |

不要把新的事件状态塞回 `raw_items` 或 `normalized_items`。事件成员关系应由独立关联表
表达。

## 4. 当前页面和 API

页面：

```text
/                    已批准消息列表
/messages/{id}       消息详情
/admin               RawItem 处理、审核、知识和 OCR Lab
```

消息详情：

- 中文页展示译文；版本图片下展示左侧英文、右侧中文的结构化对照。
- 原文页只展示原始正文和原图，不展示 OCR 标签或结构化表。
- 页面有固定圆形回到顶部按钮。

主要 API：

```text
GET  /api/v1/sources
POST /api/v1/connectors/{connector_type}/run
POST /api/v1/imports/manual
GET  /api/v1/raw-items
POST /api/v1/raw-items/{id}/process

GET  /api/v1/workflows/runs
GET  /api/v1/workflows/reviews
POST /api/v1/workflows/reviews/{id}/approve
POST /api/v1/workflows/reviews/{id}/reject
POST /api/v1/workflows/reviews/{id}/correct-ocr
POST /api/v1/workflows/runs/{id}/retry

GET  /api/v1/normalized-items
GET  /api/v1/normalized-items/published
GET  /api/v1/normalized-items/{id}/published

GET  /api/v1/events
GET  /api/v1/events/{id}
GET  /api/v1/events/{id}/messages

POST /api/v1/event-workflows/items/{id}/process
GET  /api/v1/event-workflows/runs
GET  /api/v1/event-workflows/reviews
POST /api/v1/event-workflows/reviews/{id}/approve
POST /api/v1/event-workflows/reviews/{id}/reject
POST /api/v1/event-workflows/runs/{id}/retry

GET  /api/v1/pipeline/jobs
POST /api/v1/pipeline/jobs/{id}/recover
GET  /api/v1/pipeline/corrections
GET  /api/v1/pipeline/checkpoints
POST /api/v1/pipeline/normalized-items/{id}/corrections

GET  /api/v1/knowledge/rules
POST /api/v1/knowledge/rules/organize
GET  /api/v1/knowledge/glossary
```

事件正式数据 API 仍只提供读取。新采集通过 `pipeline_jobs` 自动推进；管理台
“自动化与撤回”页签用于查看任务、撤回已发布消息以及从失败前阶段恢复。报告生成尚未实现。

## 5. 数据库历史与不可触碰边界

旧事件表由历史迁移创建，最终由
`023_remove_deferred_event_reporting.sql` 删除。以下文件是数据库迁移历史，不能删除、
改名或改写：

- `002_content_pipeline_v2.sql`
- `011_add_reviewed_ai_workflows.sql`
- `023_remove_deferred_event_reporting.sql`

事件聚合 v2 已通过追加迁移
`026_create_event_aggregation_v2.sql`、`027_add_event_review_workflow.sql` 和
`028_add_event_editorial_metrics.sql` 创建核心表、审核流程、编辑指标和约束。后续仍
必须使用新的追加迁移。

自动化与纠错底座由以下追加迁移创建：

- `029_add_pipeline_corrections.sql`：消息 revision、checkpoint、correction、运行模式和
  事件成员生命周期。
- `030_add_automatic_pipeline_jobs.sql`：持久化自动任务及失败恢复信息。
这里的“迁移”只表示创建或扩展新表和约束：

- 不移动或重写 RawItem。
- 不删除或重建 NormalizedItem。
- 不清空审核记录、知识规则、术语或 OCR 修订。
- 不修改已经应用的旧迁移来假装旧事件系统从未存在。

现有业务数据属于测试与验收基线。开始开发前先查看 `git status --short` 和数据库实际
状态，不要使用 `git reset --hard`、`git clean` 或带 `-v` 的 Docker 数据卷删除命令。

## 6. 当前事件聚合 v2

### 目标

把多条已经批准的消息聚合为一个持续演化的事件，同时保留每条消息独立展示和审计。
事件是 `NormalizedItem` 之上的新层，不是对单条处理流程的替代。

```text
Published NormalizedItem
  -> 自动或显式触发事件聚合
  -> 程序生成确定性检索条件和最多 5 个候选事件
  -> AI 只在候选内提出 not_event / create / update 草稿
  -> 自动接受，或按运行模式等待人工审核
  -> 事务写入 Event + membership + revision
  -> 事件详情时间线
```

模型仍不得生成 SQL，候选检索仍限定最多 5 个事件。自动模式接受结构化决策并保留
`decision_source=automatic`、policy version、checkpoint 和 event revision；失败不吞掉，
而是保留 job 和最后有效 checkpoint。

### 建议模型

名称可以在实现时微调，但职责要保持：

```text
events
  id
  event_key nullable
  title
  summary
  category
  status
  first_published_at
  last_published_at
  current_revision
  created_at / updated_at

event_messages
  event_id
  normalized_item_id
  relation_type
  source_published_at
  added_at

event_revisions
  id
  event_id
  revision
  title
  summary
  change_note
  evidence_snapshot
  created_at
```

关键约束：

- `(event_id, normalized_item_id)` 唯一。
- 一个 `NormalizedItem` 第一版至多属于一个主事件；如果未来需要多事件关系再显式扩展。
- 有稳定业务键时 `event_key` 唯一，例如 `patch:26.13`。
- 更新事件时锁定事件行并递增 revision，事件正文和 revision 在一个事务提交。
- `first_published_at` / `last_published_at` 来自成员 RawItem 的原始发布时间；revision
  的 `created_at` 只代表审计顺序。

不要预先给 `normalized_items` 增加 `event_status`。是否已聚合应从聚合运行和
`event_messages` 推导，避免双重状态。

### 候选检索

先使用可解释、可测试的确定性检索，不急于引入向量数据库：

1. 稳定规范键精确匹配，如版本号 + 文档类型归一到 `patch:26.13`。
2. 事件类型对应的时间窗口。
3. 核心实体、分类和规范标题重叠。
4. 简单文本相似度排序。
5. 最多把 5 个候选及其证据交给模型。

模型只能返回结构化决策草稿：

- `not_event`：消息不应进入事件层。
- `create`：建议新事件及原因。
- `update`：只能引用给定候选 ID，并说明新增事实、标题/摘要修改和成员关系。

人工模式的决定进入审核中心；自动模式由 worker 接受同一份严格结构化草稿。
`event_aggregation_runs` / `event_review_tasks` 保持独立，不塞入消息处理运行。

## 7. 推荐实施顺序

### A. 数据与只读接口

已于 2026-07-26 完成：

1. 新增 `026_create_event_aggregation_v2.sql`、SQLAlchemy 模型和 schema。
2. 实现事件列表、详情和成员读取 API。
3. 实现仅供内部与测试调用的人工建事件/关联服务，验证唯一约束、时间计算、幂等和
   revision。
4. PostgreSQL 并发集成测试验证同一消息并发关联只产生一个 membership 和一个新
   revision。
5. 未接 LLM，未改现有消息页面，正式事件表保持为空。

### B. 确定性候选

已于 2026-07-26 完成：

1. 从实体、分类、标题和原始发布时间构建检索输入。
2. 为 patch 版本实现稳定 `event_key`。
3. 只查询正式 active 事件，返回候选分数及可解释命中原因，硬性限制最多 5 个。
4. 单测覆盖零候选、精确候选、多个相似候选、上限和重复触发。

### C. AI 草稿与人工审核

已于 2026-07-26 完成：

1. 定义严格 Pydantic 输出 schema，限制 `not_event/create/update` 和候选 ID。
2. AI 只读取当前消息、最多 5 个候选和 `event_aggregation` 专用知识。
3. 管理台事件页签管理已审核/未审核消息；审核中心的事件聚合审核页展示候选、AI 理由
   和拟议变更。
4. 人工批准后才事务写入正式事件；拒绝保留审核记录并沉淀独立知识。
5. create/update/not_event/reject/retry 均有测试，运行通过 `supersedes_run_id` 保留重试链。

### D. 事件前端

已于 2026-07-26 完成：

1. 首页消息流保持独立可用，并增加事件导航。
2. 新增 `/events` 和 `/events/{id}`。
3. 详情按原始发布时间展示成员消息时间线，并能回到单条消息和原文。
4. 明确显示来源、首次发生、最近更新和 revision 历史。
5. 已通过 production build 和本地浏览器空状态、导航、管理台页签检查。

### E. 自动化、撤回与失败恢复

已于 2026-07-27 完成：

1. 采集事务为每条新 RawItem 写入 queued `pipeline_job`，独立 worker 自动推进消息与事件。
2. 审核表继续承载统一的阶段草稿；自动接受写入 `decision_source=automatic` 和
   `policy_version=auto-approve-v1`。
3. 每个已接受阶段写入 checkpoint；失败 job 保存当前阶段、错误和最后有效 checkpoint。
4. 已发布结果可按阶段撤回。事件阶段只撤回 active membership；更早阶段同时隐藏消息。
5. 重跑复用同一个 NormalizedItem ID，递增 `current_revision` 并新增不可变 revision。
6. 重跑可选人工或自动；人工完成消息修正后会继续生成事件审核，自动模式由 worker 跑完。
7. `scripts/start.ps1` 在迁移完成后启动并跟踪 `pipeline-worker`，停止脚本同步关闭。

报告、日报、embedding 和大规模召回仍未实现。

## 8. 第一组验收样本

使用已经人工处理完成的同版本设计师消息验证：

- 2026-06-16 的 `26.13 Preview`：创建 `patch:26.13`。
- 2026-06-17 的 `26.13 Full Preview`：命中并更新同一事件。
- 事件 `first_published_at` 为 6 月 16 日，`last_published_at` 为 6 月 17 日。
- 两条消息仍能在消息列表和各自详情中独立访问。
- 重复处理同一条 NormalizedItem 不产生第二个 membership 或重复 revision。
- AI 建议错误候选时，人工拒绝不会改变正式事件。

## 9. 完成定义

事件聚合第一阶段只有在以下条件全部满足时才算完成：

- 追加迁移可从现有数据库安全应用，旧数据行数和内容不变。
- 模型外键、唯一约束、删除策略和并发更新有测试。
- 候选检索不执行模型生成的 SQL，候选数不超过 5。
- 人工模式未经批准不改变正式事件；自动模式的决定来源、策略和 checkpoint 可审计。
- create/update/reject/retry 都有审计记录。
- 消息列表、详情、原文、OCR 中文对照展示不回归。
- Ruff、pytest、前端 lint 和 production build 全部通过。

## 10. 开始新对话时先读

按顺序：

1. 本文。
2. [`CONNECTOR_ARCHITECTURE.md`](CONNECTOR_ARCHITECTURE.md)。
3. [`RAW_ITEM_CONTENT_MODEL.md`](RAW_ITEM_CONTENT_MODEL.md)。
4. [`REVIEWED_AI_WORKFLOW.md`](REVIEWED_AI_WORKFLOW.md)。
5. `infra/postgres/migrations/022_refine_reviewed_item_pipeline.sql` 至
   `025_reset_item_processing_state.sql`。
6. `services/api/app/models/normalized_item.py`、
   `services/api/app/api/routes/normalized_items.py`。
7. `apps/web/components/message-feed.tsx` 和 `message-detail.tsx`。

实现前以实际 `git log -5 --oneline`、`git status --short`、迁移表和测试结果为准，不要
把本文中的日期或样本 ID 当作永久事实。

## 11. 按 Source 周期自动采集

已于 2026-07-27 完成：

1. `031_add_source_collection_schedules.sql` 为每个 Source 保存启用状态、正常周期、失败重试周期、
   单次抓取上限、Connector 运行参数、下次执行时间、成功水位和最近运行结果。
2. 独立 `collection-scheduler` 进程从数据库领取到期计划；行锁和带过期时间的租约避免重复执行，
   进程异常退出后可自动重领。
3. 首次成功前 Connector 的 `since` 为 `None`；后续使用 `last_success_at`。采集产生的新 RawItem
   仍由既有 ingestion 事务自动写入 `pipeline_jobs`，继续走全自动消息与事件管线。
4. `GET /api/v1/collection-schedules`、`PUT /api/v1/collection-schedules/sources/{source_id}` 和
   `POST /api/v1/collection-schedules/sources/{source_id}/run-now` 分别用于查询、配置和单次触发。
5. 管理台“自动化与撤回”页可以逐个非 manual Source 配置周期、重试间隔、抓取上限和 options，
   并查看最近状态、下次执行时间与失败原因。
6. `scripts/start.ps1` 在迁移完成后启动并跟踪 `collection-scheduler`；
   `scripts/stop.ps1` 会同步停止它。

新迁移不会自动为现有 Source 建立或启用计划。部署后必须在管理台逐项保存并启用，避免升级时
意外向外部平台发起采集。

## 12. LPL 赛事事件身份

普通 LPL 常规赛按中国时区的比赛日聚合。赛程预告、进行中、赛果和赛后集锦共享
`matchday:lpl:YYYY-MM-DD` 稳定事件键；季后赛后程的半决赛、胜者组决赛、败者组决赛和
总决赛在能够确定双方战队时使用单场系列赛键。`scheduled -> live -> completed` 是同一事件
的生命周期，不是拆分事件的依据；晚采集的早期赛程只能作为 context 加入，不能回退事件状态、
标题或摘要。

2026-07-27 已通过 correction #3 将误拆的事件 #26 撤回，并把消息 #134 作为 context 合并到
事件 #21。事件 #21 和 #15 已分别补为 7 月 26 日、7 月 25 日的比赛日稳定键。
