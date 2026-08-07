# LoL Daily Intel 开发 Handoff

更新时间：2026-08-07

本地工作区：`E:\leagueNews`

GitHub：`https://github.com/Coder-yui/LeagueNews`

本文是新开发对话的权威入口。实现细节以代码、数据库迁移和本文引用的专题文档为准。

## 1. 项目当前状态

项目已经运行在 `https://leaguenews.me`，主链路可用：

```text
Source 周期调度或手工触发
  -> Connector
  -> 不可变 RawItem + MediaAsset + provenance
  -> 持久化 Pipeline Job
  -> 相关性
  -> 可选 Patch 图片 OCR
  -> 翻译
  -> 事实分类、摘要、实体、重要性与 Claim
  -> NormalizedItem 发布
  -> 多事件路由、判断与聚合
```

当前已经完成：

- Riot 官网、腾讯 LOL 官网、X、微博、百度贴吧和手工导入 Connector。
- Source 级周期调度、失败重试、运行日志和手工立即执行。
- 新 RawItem 自动跑完整消息与事件链路。
- 同一套阶段草稿既支持自动接受，也支持人工审核。
- 每阶段不可变 checkpoint、失败任务恢复、按阶段撤回、人工/自动重跑。
- 公开消息列表/详情、事件列表/详情；事件成员按最近发生时间在上展示。
- 事件稳定键、候选限制、结构化决策、revision 和成员生命周期。
- 日报/周报生成、公开页面、RSS，以及只读 MCP 查询接口。
- 管理台审核、自动化采集、采集日志、管线日志、撤回、知识和 OCR Lab。
- Docker Compose 单机生产架构、Caddy Basic Auth、GHCR 镜像发布、备份和恢复脚本。

尚未实现或配置：

- 生产环境日报/周报定时 cutoff 调度；生成服务和公开页面已经实现。
- embedding/向量召回和大规模事件候选检索。
- 应用内账号、RBAC 和多管理员操作审计。
- 外部可观测平台、自动异地备份和恢复演练。

## 2. 不可破坏的架构边界

- Connector 是平台能力；Source 是具体账号或站点。新增账号通常只新增 Source。
- `raw_items.content_blocks` 是不可变原文事实来源，包含文字、图片位置和外部媒体入口。
- ingestion 负责校验、去重、媒体落盘、provenance 和自动任务入队。
- `normalized_items` 是单条消息当前投影；历史发布内容保存在
  `normalized_item_revisions`。
- 事件是 NormalizedItem 之上的独立层，成员关系不得写回 RawItem 或 NormalizedItem。
- 自动化不能绕过结构和业务校验；失败必须保留 job、阶段、错误和最后 checkpoint。
- 撤回不修改 RawItem。更早阶段重跑可以隐藏当前发布结果，但复用同一 NormalizedItem ID
  并新增 revision。
- 所有已经提交的 SQL 迁移都是历史账本，只能追加新迁移，不能改名、重写或删除。

专题文档：

- [`CONNECTOR_ARCHITECTURE.md`](CONNECTOR_ARCHITECTURE.md)
- [`RAW_ITEM_CONTENT_MODEL.md`](RAW_ITEM_CONTENT_MODEL.md)
- [`REVIEWED_AI_WORKFLOW.md`](REVIEWED_AI_WORKFLOW.md)
- [`EVENT_EDITORIAL_POLICY.md`](EVENT_EDITORIAL_POLICY.md)

## 3. 自动管线与人工兜底

默认 `PIPELINE_AUTOMATION_ENABLED=true`。新内容入库时在同一事务创建 queued
`pipeline_job`，独立 Worker 按以下阶段推进：

1. `relevance`
2. 可选 `image_ocr`
3. `translation`
4. `fact_classify`
5. `importance`
6. `claim_gen`
7. `event_decision`

自动模式仍创建审核任务记录，并写入：

- `decision_source=automatic`
- `policy_version=auto-approve-v1`
- 接受后的 `processing_checkpoint`

人工模式使用相同草稿和校验，只是等待人工批准、驳回或 OCR 修订。已发布消息可从相关性、
OCR、翻译、分析或事件判断重新开始。失败任务从失败前最后有效 checkpoint 恢复，也可以改走
人工流程。

不要通过直接更新状态字段“修好”任务。使用管理台或 `/api/v1/pipeline` 下的恢复/撤回 API。

## 4. 事件聚合规则

流程：

```text
Published NormalizedItem
  -> 程序将 event_mentions 归并为主题簇
  -> 每个主题簇生成一个稳定 event_route
  -> 确定性候选检索（最多 8 个）
  -> AI 为既定路由返回 0 至 12 个结构化 memberships
  -> 自动接受或人工审核
  -> 一个或多个 Event + EventMessage + EventRevision 原子提交
```

核心规则：

- 模型不能生成 SQL，也不能更新未提供的候选 ID。
- 一个 active NormalizedItem 可以按 `primary`、`component`、`cross_ref` 角色进入多个事件；
  空 memberships 表示不形成事件。
- 事件更新时间来自成员 RawItem 的原始发布时间，不使用审核时间代替发生时间。
- 稳定业务键优先于文本相似度，例如版本事件 `patch:lol_pc:26.13`。
- 版本主题簇中的普通英雄/模式调整作为组件，不另建玩法事件；无版本号热更新在两天短窗口
  内有核心修复对象重叠时，程序直接续接已有批次，不交给模型决定。
- LPL/LCK 普通比赛按联赛和日期使用 `matchday:{league}:{date}`，不依赖当天是一场、两场
  还是三场；缺少联赛字段的普通单局消息按同日同双方唯一命中已有比赛日；后段季后赛、
  决赛和焦点战才使用 `match:{date}:{team-a}-vs-{team-b}`。
- `event_kind`、`aggregation_strategy`、`product_scope` 和可新建的稳定键由程序生成，模型
  只能选择同键候选、可解释的同义实体候选或允许新建的路由。
- 评论、提醒、否定和仅上下文提及使用 `existing_only`，不能据此创建新事件。
- 晚采集的早期赛程只能作为 context 加入，不得让 completed 事件回退状态或标题。

## 5. 采集调度与平台会话

`source_collection_schedules` 保存启用状态、正常周期、失败重试周期、抓取上限、options、
水位、租约和最近结果。`collection-scheduler` 串行领取到期任务，异常退出后租约可过期重领。

全新数据库会创建当前 15 个内置信源，但不会自动启用周期。

平台注意事项：

- X 使用 `.secrets/x-cookies.json`，每次运行导入临时 twscrape SQLite。
- 微博本地使用专用 Edge Profile 登录；生产使用
  `.secrets/weibo-cookies.json` + `WEIBO_BROWSER_USER_AGENT`，每个容器启动浏览器上下文时
  重新注入 Cookie。
- 直接复制 Windows Chromium Profile 不能可靠跨容器复用加密 Cookie。
- 贴吧匿名访问百度接口，偶发连接超时属于上游问题，由 `retry_delay_minutes` 重试。
- 外部平台接口都可能因限流、风控或页面变化失效；不要绕过验证码或自动切换代理池。

操作和排障见
[`CONNECTOR_OPERATIONS_GUIDE.md`](CONNECTOR_OPERATIONS_GUIDE.md)。

## 6. 数据表职责

| 表 | 职责 |
| --- | --- |
| `sources` | 账号/站点和 Connector 配置 |
| `source_collection_schedules` | Source 周期、重试、水位和租约 |
| `connector_runs` | 单次采集结果和错误 |
| `raw_items` | 不可变、版本化原文 |
| `raw_item_source_payloads` | 脱敏 provenance |
| `media_assets` | 原媒体引用和本地存储路径 |
| `processing_runs` / `review_tasks` | 单条消息运行、草稿和决定 |
| `knowledge_rules` / `glossary_terms` | 分阶段知识与术语 |
| `media_extractions` | OCR、结构化表格和人工修订 |
| `normalized_items` | 已发布消息当前投影 |
| `normalized_item_revisions` | 消息不可变发布历史 |
| `events` / `event_messages` | 事件当前状态和成员生命周期 |
| `event_revisions` | 事件标题、摘要、变化和证据历史 |
| `event_aggregation_runs` / `event_review_tasks` | 事件决策运行与审核 |
| `processing_checkpoints` | 阶段接受结果和决定来源 |
| `pipeline_jobs` | 自动任务、当前阶段和失败信息 |
| `pipeline_corrections` | 撤回目标、重跑阶段与模式 |

## 7. 页面和主要 API

页面：

```text
/                    已发布消息
/messages/{id}       消息详情
/events              事件列表
/events/{id}         事件详情
/digests             日报与周报
/digests/{id}        摘要详情
/admin                审核与自动化管理台
```

API 分组：

```text
/api/v1/sources
/api/v1/connectors
/api/v1/collection-schedules
/api/v1/imports
/api/v1/raw-items
/api/v1/workflows
/api/v1/normalized-items
/api/v1/event-workflows
/api/v1/events
/api/v1/digests
/api/v1/feeds
/api/v1/mcp
/api/v1/pipeline
/api/v1/knowledge
/api/v1/ocr-lab
```

生产环境关闭 Swagger。Caddy 匿名开放健康检查、已发布消息和事件读取；`/admin` 与私有 API
使用 Basic Auth。

## 8. 数据库和迁移

当前最新迁移：`055_update_event_market_reach_policy.sql`。

关键阶段：

- `015`–`021`：ContentBlock v2、RawItem 身份和版本化。
- `022`–`025`：审核流程收口和生产 OCR 状态。
- `026`–`028`：事件聚合 v2、事件审核和编辑指标。
- `029`–`030`：revision、checkpoint、撤回、自动任务。
- `031`：Source 周期采集调度。
- `032`–`040`：租约、知识治理、来源可靠性、Claim 时间线与编辑指标。
- `041`–`044`：RawItem 修订链、合并事实分类审核阶段与生产 OCR 基线修复。
- `045`–`047`：显式来源可靠性、编辑基准重要性与事件证据投影。
- `048`–`054`：消息受控 ontology、事件身份与多提及路由、成员贡献重要性、外观发布评分，
  以及当前运行时模型收敛。

本地 `scripts/start.ps1` 和生产 `migrate` 容器现在统一调用
`services/api/scripts/migrate_database.py`。全新数据库通过当前 SQLAlchemy 模型建表、登记历史
迁移、创建生产 OCR Profile 和 15 个内置信源；已有数据库只按文件名顺序执行未应用迁移。

禁止：

- `git reset --hard`、`git clean` 清理用户工作；
- 修改已应用迁移；
- `docker compose down -v`；
- 用空库覆盖生产库；
- 删除知识、术语、审核、revision 或媒体来“解决”状态问题。

## 9. 当前生产

公开地址为 `https://leaguenews.me`，由 Caddy 提供 HTTPS、公开读取边界和单管理员
Basic Auth。仓库文档不记录真实主机 IP、项目标识、账号、Cookie 或私密配置。历史无域名
预发布步骤仅保留在 `GOOGLE_CLOUD_FIRST_DEPLOY.md` 作为环境重建参考，不代表当前访问方式。

生产服务：

- PostgreSQL
- migrate（一次性，成功后 Exited 0）
- API
- Web
- Pipeline Worker
- Collection Scheduler
- Caddy

部署、备份、恢复和域名切换见：

- [`GOOGLE_CLOUD_FIRST_DEPLOY.md`](GOOGLE_CLOUD_FIRST_DEPLOY.md)
- [`PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md)

## 10. 当前生产风险与后续事项

1. Basic Auth 只适合单管理员；升级条件见生产部署手册。
2. 数据库、媒体和平台 Cookie 需要自动异地备份及定期恢复演练。
3. 外部监控仍需覆盖公网可用性、磁盘、容器、备份年龄和 LLM 费用。
4. 微博/X 登录态会过期；贴吧和平台接口可能偶发失败。
5. 扩大 Worker/调度器并发前仍需观察租约回收、数据库锁和资源指标。
6. 生产部署应使用不可变 `sha-<commit>` 镜像标签。

## 12. 开发与验证

开始前：

```powershell
git status --short
git log -5 --oneline
```

后端：

```powershell
services\api\.venv\Scripts\python.exe -m ruff check services/api/app services/api/scripts services/api/tests
services\api\.venv\Scripts\python.exe -m pytest services/api/tests -q
```

前端：

```powershell
pnpm lint:web
pnpm build:web
```

本地运行见 [`LOCAL_RUNBOOK.md`](LOCAL_RUNBOOK.md)。

完成开发后至少检查：

- 迁移是否只追加；
- ingestion 是否仍保持 RawItem 不可变；
- 自动失败是否保留 checkpoint 和错误；
- 人工模式未经批准是否不改变正式数据；
- 消息、事件、图片和撤回页面是否回归；
- 文档、env 示例、Compose 和实际配置字段是否一致。

## 13. 推荐下一步

按优先级：

1. 在管理台处理 RawItem #46 的两个历史失败 job，确认恢复或取消语义。
2. 配置每日数据库备份、媒体备份和异地副本，并完成一次恢复演练。
3. 接入轻量外部监控和磁盘/任务失败告警。
4. 验证生产 RSS discovery 和只读 MCP 客户端经反向代理的互操作性。
5. 把部署从 `latest` 改为明确的 `sha-<commit>`。
6. 观察 1–2 个月采集成功率、LLM 成本和撤回频率，再决定扩容。
7. 配置日报/周报 cutoff 调度并验证晚到事件会产生 DigestRevision；embedding 继续后置。
