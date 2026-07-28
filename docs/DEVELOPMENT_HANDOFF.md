# LoL Daily Intel 开发 Handoff

更新时间：2026-07-28

本地工作区：`E:\leagueNews`

GitHub：`https://github.com/Coder-yui/LeagueNews`

本文是新开发对话的权威入口。实现细节以代码、数据库迁移和本文引用的专题文档为准。

## 1. 项目当前状态

项目已经从本地原型进入 Google Cloud 无域名预发布阶段，主链路可用：

```text
Source 周期调度或手工触发
  -> Connector
  -> 不可变 RawItem + MediaAsset + provenance
  -> 持久化 Pipeline Job
  -> 相关性
  -> 可选 Patch 图片 OCR
  -> 翻译
  -> 摘要、实体、重要性、可信度分析
  -> NormalizedItem 发布
  -> 事件判断与聚合
```

当前已经完成：

- Riot 官网、腾讯 LOL 官网、X、微博、百度贴吧和手工导入 Connector。
- Source 级周期调度、失败重试、运行日志和手工立即执行。
- 新 RawItem 自动跑完整消息与事件链路。
- 同一套阶段草稿既支持自动接受，也支持人工审核。
- 每阶段不可变 checkpoint、失败任务恢复、按阶段撤回、人工/自动重跑。
- 公开消息列表/详情、事件列表/详情；事件成员按最近发生时间在上展示。
- 事件稳定键、候选限制、结构化决策、revision 和成员生命周期。
- 管理台审核、自动化采集、采集日志、管线日志、撤回、知识和 OCR Lab。
- Docker Compose 单机生产架构、Caddy Basic Auth、GHCR 镜像发布、备份和恢复脚本。

尚未实现：

- 日报或定时摘要页面。
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
4. `item_analysis`
5. `event_decision`

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
  -> 确定性候选检索（最多 5 个）
  -> AI 返回 not_event / create / update
  -> 自动接受或人工审核
  -> Event + EventMessage + EventRevision 原子提交
```

核心规则：

- 模型不能生成 SQL，也不能更新未提供的候选 ID。
- 一个 active NormalizedItem 当前只能属于一个主事件。
- 事件更新时间来自成员 RawItem 的原始发布时间，不使用审核时间代替发生时间。
- 稳定业务键优先于文本相似度，例如版本事件 `patch:26.13`。
- 普通 LPL 常规赛按中国时区比赛日使用 `matchday:lpl:YYYY-MM-DD`。
- 同一天的赛程预告、进行中、赛果和赛后内容属于同一事件。
- 季后赛后程在能够确定对阵双方时才允许使用单场系列赛事件。
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
/api/v1/pipeline
/api/v1/knowledge
/api/v1/ocr-lab
```

生产环境关闭 Swagger。Caddy 匿名开放健康检查、已发布消息和事件读取；`/admin` 与私有 API
使用 Basic Auth。

## 8. 数据库和迁移

当前最新迁移：`031_add_source_collection_schedules.sql`。

关键阶段：

- `015`–`021`：ContentBlock v2、RawItem 身份和版本化。
- `022`–`025`：审核流程收口和生产 OCR 状态。
- `026`–`028`：事件聚合 v2、事件审核和编辑指标。
- `029`–`030`：revision、checkpoint、撤回、自动任务。
- `031`：Source 周期采集调度。

本地 `scripts/start.ps1` 和生产 `migrate` 容器现在统一调用
`services/api/scripts/migrate_database.py`。全新数据库通过当前 SQLAlchemy 模型建表、登记历史
迁移、创建生产 OCR Profile 和 15 个内置信源；已有数据库只按文件名顺序执行未应用迁移。

禁止：

- `git reset --hard`、`git clean` 清理用户工作；
- 修改已应用迁移；
- `docker compose down -v`；
- 用空库覆盖生产库；
- 删除知识、术语、审核、revision 或媒体来“解决”状态问题。

## 9. 当前 Google Cloud 预发布

资源：

```text
GCP project: project-5f162905-6b28-4d14-8bf
instance:    instance-20260727-160248
zone:        asia-east1-a
OS:          Ubuntu 24.04
规格:        2 vCPU / 4 GB RAM / 40 GB disk
部署目录:    /home/czh69423821/LeagueNews
```

当前无域名，通过本机 SSH 隧道访问：

```powershell
gcloud config set project project-5f162905-6b28-4d14-8bf
gcloud compute ssh instance-20260727-160248 --zone=asia-east1-a -- -N -L 8080:127.0.0.1:8080
```

浏览器打开 `http://localhost:8080`。服务器 Caddy 只绑定
`127.0.0.1:8080`，不要在无 HTTPS 时开放到公网。

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

## 10. 2026-07-28 生产快照

以下是交接时观测值，不应写进业务逻辑：

```text
sources                 15
raw_items               205
published items         190
active events           25
active event messages   88
queued pipeline jobs     0
running pipeline jobs    0
failed pipeline jobs     2
```

已启用周期：

- X：3 / 5
- 微博：5 / 5
- 百度贴吧：2 / 2
- Riot 官网：0 / 1
- 腾讯官网：0 / 1

微博和贴吧最近一轮均成功。两个失败任务都属于 RawItem #46 的历史重跑：

- job #18：`item_analysis`，旧的商城事件类型校验失败。
- job #22：`event_decision`，旧的商城重要性区间校验失败。

它们没有造成当前队列积压，但应在管理台确认是否取消旧任务或按当前规则恢复，不要直接删行。

## 11. 当前风险与上线前缺口

1. 仍无域名和 HTTPS 公网入口，只适合作为 SSH 隧道预发布。
2. Basic Auth 只适合单管理员；没有应用内用户权限和操作审计。
3. 数据库、媒体和平台 Cookie 尚需自动异地备份及定期恢复演练。
4. 缺少外部可用性、磁盘、容器、采集连续失败、管线积压和 LLM 费用告警。
5. 微博/X 登录态会过期；贴吧和平台私有接口可能偶发失败。
6. 当前 Worker/调度器按单实例部署；扩大并发前要先做资源和锁验证。
7. `latest` 镜像便于预发布，但正式上线应使用不可变 `sha-<commit>` 标签。

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
4. 购买域名，切换 Caddy 到 80/443 自动 HTTPS。
5. 把部署从 `latest` 改为明确的 `sha-<commit>`。
6. 观察 1–2 个月采集成功率、LLM 成本和撤回频率，再决定扩容。
7. 稳定后再开发日报；embedding 和 Agent 类能力继续后置。
