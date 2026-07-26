# LoL Daily Intel 开发 Handoff

> 历史状态快照，不再作为当前架构规范。Connector 与 RawItem 以
> [`CONNECTOR_ARCHITECTURE.md`](CONNECTOR_ARCHITECTURE.md) 为准。

更新时间：2026-07-24  
工作区：`E:\leagueNews`

这份文档是下一开发会话的全局状态快照。Connector 专项细节另见
[`CONNECTOR_HANDOFF.md`](CONNECTOR_HANDOFF.md)，日常启动方式见
[`LOCAL_RUNBOOK.md`](LOCAL_RUNBOOK.md)。

## 1. 当前目标与原则

项目是英雄联盟垂直领域的多信源 AI 情报聚合网站。当前优先完成稳定闭环：

```text
具体 Source
  -> 平台 Connector
  -> 统一 ConnectorItem
  -> raw_items（pending）
  -> 人工触发 AI workflow
  -> normalized_items
  -> event_items / news_events
  -> Next.js 日报与事件流
```

已确定的原则：

- 暂不引入复杂 Agent，使用可测试的顺序 workflow。
- 采集、持久化、AI 分析和展示彼此解耦。
- 原始数据入库后保持 `pending`，不会自动调用 AI。
- AI 失败时回滚本次处理，raw 继续保持 `pending`，不生成兜底内容。
- Connector 是平台级复用能力，Source 是具体站点或账号。
- 一个 X/微博平台只实现一个 connector；每个指定账号各建一条 Source。
- 不依赖 X 付费 API；当前 X 实现使用本地 cookie 和 `twscrape`。

## 2. 技术栈与目录

- 前端：Next.js、React、TypeScript、Tailwind CSS、lucide-react、pnpm。
- 后端：FastAPI、Pydantic、SQLAlchemy、uv。
- 数据库：PostgreSQL + Docker Compose。
- 数据库界面：pgAdmin。
- LLM：OpenAI-compatible client，当前 `.env` 已由用户配置 DeepSeek。
- OCR：RapidOCR，随后由 OpenAI-compatible LLM 整理 patch preview 表格结构。

关键目录：

```text
apps/web                         Next.js 前端
apps/web/public/media            本地媒体文件
services/api/app/api             FastAPI 路由
services/api/app/connectors      Connector 实现
services/api/app/services        入库、媒体、LLM 等服务
services/api/app/workflows       翻译、分析、图片理解 workflow
services/api/app/models          SQLAlchemy 模型
services/api/tests               后端测试
infra/postgres/migrations        SQL 迁移
scripts/start.ps1                一键启动
scripts/stop.ps1                 一键关闭
docs                             使用与交接文档
```

## 3. Connector 与 Source 设计

### Connector

Connector 表示一种可复用的采集方式。当前已注册：

- `manual`
- `riot_official`
- `tencent_lol`
- `x_twitter`

所有 connector 返回统一的 `ConnectorItem`：

- `external_id`
- `title`
- `url`
- `author`
- `language`
- `published_at`
- `content`
- 有序 `content_blocks`
- `raw_payload`

Connector 不直接写数据库、不调用 AI。

### Source

Source 表示具体发布者：

- `connector_type`：使用哪个 connector。
- `external_key`：账号名、UID 或站点唯一标识。
- `base_url`：主页。
- `connector_config`：该 Source 的非敏感平台配置。

`(connector_type, external_key)` 有部分唯一索引。运行 connector 时传具体
`source_id`，因此多个 X 账号不会混在同一信源。

不要依赖 Source ID 连续，也不要在代码里硬编码 ID；应使用 API 查询或按
`connector_type + external_key` 识别。

## 4. 当前数据库数据

当前业务数据已重新整理，是下一阶段 AI workflow 的测试集：

| Source | connector | external_key | raw |
|---|---|---|---:|
| 手动导入 | `manual` | 空 | 0 |
| 腾讯英雄联盟官方网站 | `tencent_lol` | `lol.qq.com` | 10 |
| Riot Games Official | `riot_official` | `leagueoflegends.com` | 10 |
| Matt Leung-Harrison | `x_twitter` | `riotphroxzon` | 5 |
| SkinSpotlights | `x_twitter` | `skinspotlights` | 5 |
| League of Legends | `x_twitter` | `leagueoflegends` | 5 |
| LoL Esports | `x_twitter` | `lolesports` | 5 |
| Spideraxe | `x_twitter` | `spideraxe30` | 5 |

总计：

- `raw_items = 45`
- 45 条状态全部为 `pending`
- `normalized_items = 0`
- `news_events = 0`
- `event_items = 0`
- 已完成 connector runs：X 5 次、腾讯 1 次、Riot 1 次

这批数据后续要用于观察翻译、分类、摘要、实体、重要性、可信度和事件展示效果，
不要随意清空。

## 5. 媒体数据现状

图片使用两层表示：

- 数据库 `media_assets` 保存来源 URL、内容块位置和本地路径。
- 实际文件位于 `apps/web/public/media/{connector_type}/`。

当前状态：

- 腾讯：49 个图片引用，49 个都有本地 `storage_path`。
- 腾讯本地唯一文件 45 个；引用数更大是因为重复图片复用同一文件。
- Riot：177 个图片引用，全部已缓存。
- X：21 个图片引用，全部已缓存。

腾讯第一次批量下载时有 14 张图片因为 CDN 慢速传输和偶发 TLS 握手失败而超过
12 秒缓存窗口。之后使用 3 路并发、单张 120 秒重试，14/14 已成功，并同步更新：

- `media_assets.storage_path`
- `media_assets.mime_type`
- 对应 `raw_items.content_blocks[block_index]`

当前腾讯 `remote_only = 0`，图片内容块缺失 `storage_path = 0`。

默认采集策略仍然是：

- 同一篇文章图片最多 6 路并发。
- 单张图片 12 秒内无法下载时保留 `source_url` 和正文位置。
- 单张图片失败不会回滚整批文章。

目前没有正式的“重试 remote-only 图片”API；这适合作为后续小功能。

### X 视频规则

用户明确要求不保存 X 视频。当前 `x_twitter` connector：

- 不创建 `video` content block。
- 不保存视频下载地址。
- 不保存视频缩略图。
- `raw_payload.source_response.media` 只保留 `photos`。
- 原帖 URL 仍保留，用户可跳回 X 查看视频。

当前 25 条 X raw 的视频块数量已核验为 0。

## 6. 三个自动 Connector 的实现状态

### 腾讯官网

文件：`services/api/app/connectors/tencent_lol.py`

- 使用腾讯公开内容列表与正文接口。
- 默认栏目 `target=24`，可由 Source 的 `connector_config.target` 覆盖。
- 保留正文图文顺序。
- 当前栏目可能包含活动、皮肤和云顶之弈内容；以后需要更纯的 LoL 范围时，应在
  采集后增加明确过滤规则，而不是修改统一入库结构。

### Riot 官网

文件：`services/api/app/connectors/riot_official.py`

- Riot 首页 DOM 只渲染少量内部文章，`?page=2` 对普通 HTTP 请求仍返回同一首屏。
- 已改为优先解析页面 `#__NEXT_DATA__` 中的 Smart List；其中包含按时间排序的
  200 条记录。
- 只选择 `www.leagueoflegends.com/en-us/news/...` 内部文章。
- 跳过 YouTube、LoL Esports 等外链和纯视频卡片。
- 如果 Smart List 结构不可用，会回退到 DOM 解析。

该实现依赖 Riot 当前 Next.js 数据路径，官网改版后可能需要更新解析器。

### X

文件：`services/api/app/connectors/x_twitter.py`

- 使用 `.secrets/x-cookies.json` 中的 `auth_token` 和 `ct0`。
- 每个 run 只抓一个 Source 的 `external_key` 账号。
- 临时 twscrape SQLite 位于 `.run`，运行后删除。
- Cookie 不会进入 PostgreSQL、日志或 raw payload。
- 当前依赖 X 内部 Web GraphQL，可能因 cookie 过期、限流或页面变化失败。
- 不会自动切换到付费 API、代理池或多账号池。

## 7. Raw 入库与去重

统一入口位于：

- `services/api/app/services/connector_runner.py`
- `services/api/app/services/ingestion.py`

入库步骤：

1. connector 针对一个具体 Source 返回 `ConnectorItem`。
2. 从有序 `content_blocks` 提取 `plain_text`。
3. 计算 `content_hash`。
4. 优先按 `(source_id, external_id)` 去重。
5. 没有 `external_id` 时再按 `(source_id, content_hash)` 去重。
6. 图片尽量本地化并建立 `media_assets`。
7. 创建 `raw_items`，状态固定为 `pending`。

不同 Source 发布相同内容目前不会跨 Source 合并，这是有意保留的多信源证据。

## 8. AI 处理流程

手动触发：

```http
POST /api/v1/raw-items/{raw_item_id}/process
```

入口：

- `services/api/app/api/routes/raw_items.py`
- `services/api/app/workflows/analyze_item.py`

顺序：

1. 只接受 `pending` raw。
2. 若标题包含 `patch` 且有图片，先执行 patch preview OCR。
3. RapidOCR 输出文字、坐标和置信度。
4. LLM 将 OCR 行整理成版本改动结构。
5. LLM 对正文和图片结构化结果做分类、摘要、实体、重要性和可信度分析。
6. 英文内容调用 LLM 翻译为简体中文，保持原 `content_blocks` 顺序和图片位置。
7. 创建一个 `normalized_items`。
8. 当前策略直接创建一个 `news_events`。
9. 通过 `event_items` 建立关联。
10. raw 状态改为 `processed`。

当前仍是“一条 normalized item 创建一个 event”，尚未实现：

- 跨信源语义去重。
- 多条资讯聚合到同一事件。
- 事件增量更新。
- 矛盾信源对比。

### LLM 行为

配置变量：

```dotenv
OPENAI_API_KEY=...
OPENAI_BASE_URL=...
MODEL_NAME=...
```

不要在文档、日志或提交中暴露真实 Key。

未配置 Key：

- API 返回 503。
- raw 保持 `pending`。
- 不生成兜底结果。

连接、超时、JSON 校验或 OCR 处理失败：

- API 返回明确错误。
- 当前事务回滚。
- raw 保持 `pending`，可重试。

DeepSeek provider 会显式关闭 thinking，分析最多重试 2 次；翻译一次请求发送该文章的
全部文字块，不做自定义小块切割。

## 9. 前端现状

首页：

- 请求 `GET /api/v1/events/feed`。
- 有真实事件时展示事件标题、摘要、分类、重要性、可信度、来源、原文/译文图文块和
  patch 图片结构化结果。
- 当前数据库没有 event，因此首页仍显示 `sampleEvents`，并标注 demo。

尚未完成：

- raw pending 管理页面。
- 在前端选择并触发单条或批量 AI 处理。
- connector 运行控制台。
- Source 管理页面。
- 失败任务和重试界面。
- 日报生成、日期筛选、搜索。
- 真正的事件聚合 UI。

因此下一阶段若开发 workflow，建议先做一个简单后台页：

```text
pending raw 列表
  -> 查看原文和图片
  -> 单条“处理”按钮
  -> 展示处理中/成功/错误
  -> 跳转生成的 event
```

批量处理应限制并发并显示每条独立结果，不要让其中一条失败导致整批不可观察。

## 10. 主要 API

```text
GET  /api/v1/health
GET  /api/v1/sources
POST /api/v1/sources

POST /api/v1/imports/manual
GET  /api/v1/raw-items
POST /api/v1/raw-items/{id}/process

GET  /api/v1/normalized-items
POST /api/v1/normalized-items/{id}/translate

GET  /api/v1/media-assets
GET  /api/v1/media-assets/extractions
POST /api/v1/media-assets/{id}/extract-patch-preview

GET  /api/v1/events
GET  /api/v1/events/feed

GET  /api/v1/connectors
GET  /api/v1/connectors/runs
POST /api/v1/connectors/{connector_type}/run
```

API 文档：http://localhost:8000/docs

## 11. 启动、关闭与验证

启动：

```powershell
Set-Location E:\leagueNews
.\scripts\start.ps1
```

不自动打开网页：

```powershell
.\scripts\start.ps1 -SkipBrowser
```

脚本会在 Docker daemon 未运行时尝试启动 Docker Desktop，并打开：

- 网站：http://localhost:3000
- API：http://localhost:8000/docs
- pgAdmin：http://localhost:5050

关闭：

```powershell
.\scripts\stop.ps1
```

测试：

```powershell
Set-Location E:\leagueNews\services\api
.venv\Scripts\python.exe -m ruff check app tests
.venv\Scripts\python.exe -m pytest -q
```

最近一次结果：

- Ruff 通过。
- `36 passed`。
- 存在一个第三方 `StarletteDeprecationWarning`，不影响测试结果。
- Windows pytest 临时目录清理偶尔输出 `PermissionError`，测试本身仍然通过。

最近一次服务检查：

- `http://localhost:3000`：200
- `http://localhost:8000/api/v1/health`：200
- `http://localhost:5050`：200

## 12. 迁移与启动脚本注意事项

已有迁移：

```text
002_content_pipeline_v2
003_fix_article_media_order
004_add_translation_fields
005_fix_plain_text_from_blocks
006_add_media_extractions
007_add_connector_ingestion
008_seed_web_connector_sources
009_source_identity_per_publisher
010_add_weibo_tieba_sources
011_add_reviewed_ai_workflows
012_track_approved_media_extractions
013_add_ocr_lab
014_add_patch_table_structure
```

`008` 和 `009` 是带数据变更的一次性迁移，不能每次启动都重跑。之前
`scripts/start.ps1` 的 `sh -c` 引号问题导致迁移状态读取为空，曾把多个 X Source
错误归并。

现在已修复：

- 从 postgres 容器读取 `POSTGRES_USER` / `POSTGRES_DB`。
- 直接调用 `psql` 获取 `schema_migrations` 列表。
- 只在版本不存在时执行 008/009。
- 已实际重复运行启动脚本回归验证，输出只执行幂等的 007，没有再次执行 008/009。

不要恢复旧的迁移检测写法。

## 13. Git 与文件安全

虽然已经初始化 Git，但目前项目尚未建立首个正式提交，`git status` 中项目文件整体仍为
untracked。

下一会话必须注意：

- 不要执行 `git clean`。
- 不要执行 `git reset --hard`。
- 不要用 checkout 覆盖整个工作区。
- 不要假设 untracked 文件是垃圾；它们就是当前项目主体。
- `.env`、`.secrets` 和 `.run` 含本地配置或运行状态，不应提交。
- 修改前先执行 `git status --short`。

如果下一阶段要建立首个基线提交，应先人工检查 `.gitignore` 和敏感文件，再由用户明确
确认提交范围。

## 14. 建议的下一步

如果下一会话继续开发 AI workflow，推荐顺序：

1. 先在 pgAdmin 或 `GET /raw-items` 抽查这 45 条真实数据。
2. 增加 pending raw 管理页或最小批处理 API。
3. 先处理少量不同类型样本：
   - Riot 英文长文。
   - Matt patch preview 图文帖。
   - 腾讯中文活动公告。
   - SkinSpotlights 图片帖。
   - LoL Esports 赛事帖。
4. 对照原文检查翻译、摘要、分类和实体。
5. 调整 prompt/schema 后再扩大到全部 45 条。
6. 最后再设计跨信源事件聚类，暂时不要直接上 Agent。

在批量调用 DeepSeek 前，先确认网络代理稳定，并把错误逐条记录；不要因为一次网络错误
把 raw 标成 `processed`。

## 15. 2026-07-25 人工审核工作流更新

本节覆盖本文档第 8、9、14 节中关于“单条 Raw 直接创建 Event”的旧说明。

已新增微博和百度贴吧 Connector，以及迁移：

```text
010_add_weibo_tieba_sources
011_add_reviewed_ai_workflows
012_track_approved_media_extractions
```

AI 管线已经改为三个审核关卡：

```text
Raw
  -> 相关性 AI 草稿
  -> 人工确认
  -> 翻译/OCR/实体/分类/摘要/评分草稿
  -> 人工统一确认
  -> 正式 Normalized Item
  -> 再次手动触发事件处理
  -> 候选事件检索 + AI 聚合草稿
  -> 人工确认
  -> Event + Event Revision
```

明确排除英雄联盟手游和 2XKO；端游、电竞、云顶、世界观/影视、Riot 公司新闻及
LoL 周边音乐商业合作保留。

人工拒绝会按明确反馈类型处理：相关性、分析和事件修正成长长期规则；翻译术语修正只
成长术语表；OCR 错误和其他问题只保留审核记录，不写入知识。等待人工确认时不保持
数据库事务，正式数据在批准前不会改变。

事件候选由应用程序按事件类型时间窗口、状态、实体、分类和文本相似度检索 Top 5，
AI 不生成或执行 SQL。事件批准后写入 `event_revisions`，日报、周报和月报引用具体事件
版本。

管理页面：

```text
http://localhost:3000/admin
```

详细 API 和状态说明见 [`REVIEWED_AI_WORKFLOW.md`](REVIEWED_AI_WORKFLOW.md)。

## 16. 2026-07-25 LLM 校验与 OCR 测试台更新

- 全部结构化 LLM 调用统一为两次尝试；每次都执行 JSON、Pydantic Schema 和业务约束
  校验。第一次失败会把校验错误反馈给模型纠正，第二次失败才终止运行。
- HTTP、请求校验、LLM 和 OCR 错误响应统一包含 `detail` 及
  `error.code/message/retryable/details`。
- 新增迁移 `013_add_ocr_lab`，允许同一图片保留多次 OCR 尝试，并新增
  `ocr_test_runs`、`ocr_profiles`。
- 管理页新增“OCR 测试台”，可调整缩放、灰度、对比度、锐度以及 RapidOCR 检测阈值；
  每次测试保存识别框叠加图和逐行置信度，不调用 LLM。
- 已用 Media Asset `#12`（Patch 26.15 Full Preview）运行默认基线：91 行，平均置信度
  97.04%，测试运行 `#1`。尚未激活为生产参数，等待人工校正和选择。
- 后端测试结果：`36 passed`；Ruff、前端 ESLint 和 TypeScript 检查通过。

## 17. 2026-07-25 Patch 表格结构解析更新

迁移 `014_add_patch_table_structure` 为 OCR 测试记录增加：

- `table_data`
- `structure_confidence`
- `table_overlay_path`

正式 Patch 图片管线已从“整图 OCR 文本框直接交给 LLM”改为：

```text
整图 OCR
  -> 自动检测左右列分隔
  -> 检测左列完整横线和纵向合并单元格
  -> target 与同纵向区间 raw_changes 配对
  -> 结构置信度保护
  -> LLM 仅整理、翻译已配对内容
```

`PATCH_SCHEMA_VERSION` 已升级为 `v2`。Full Preview 结构分低于 65% 时不会调用 LLM；
Preview 允许目标的 `changes=[]`，避免模型为尚未公布具体数值的预览编造改动。

已对 4 张现有 RiotPhroxzon 图片保存结构回归：

| Media Asset | 类型 | 分隔线 | 目标数 | 结构分 |
| --- | --- | ---: | ---: | ---: |
| #12 / 26.15 Full Preview | full_preview | 148 | 15 | 100% |
| #13 / 26.15 Preview | preview | 无详情列 | 12 | 100% |
| #14 / 26.14 Full Preview | full_preview | 181 | 15 | 98% |
| #15 / 26.14 Preview | preview | 无详情列 | 16 | 100% |

Asset #12 的 Bel’Veth 被识别为一个纵向合并目标单元格，并绑定右侧 47 行改动。Asset
#14 的 `Azir (Bugfixes)` 没有右侧内容，系统保留空值并发出人工检查警告，没有猜测内容。

分类标题图标兼容：部分缩放参数会把箭头图标识别为单独文本 `"1"` 或 `"↑/↓"`。
解析器会将与 `CHAMPION/SYSTEM ... BUFFS/NERFS/ADJUSTMENTS` 同行、紧邻左侧的小框
并入分类标题区域，并在键值配对前排除。Asset #447 的测试运行 #65 已验证分类图标
不会再成为上一 section 的伪目标。

## 18. 2026-07-25 OCR 生产参数与人工修订

六张现有样图验证后，当前生产 OCR 参数已经激活：

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

历史 OCR 测试记录已经清空：删除 `71` 条 `ocr_test_runs`、`131` 个叠加图文件和
`1` 个非激活参数配置；保留 `1` 个当前激活配置，并将已删除的来源测试 ID 置空。

单条分析审核页新增 OCR 表格人工修订。审核员可以修改分类标题、左栏对象和右栏改动，
也可以增删对象，然后通过
`POST /api/v1/workflows/reviews/{review_id}/correct-ocr` 保存。系统新建
`MediaExtraction` 修订版（`schema_version=v2-manual`），不覆盖原 OCR；随后让 LLM
用人工修正的表格重新整理，并重新生成该条信息的翻译与分析草稿。旧待审核任务会成为
`superseded`，新草稿继续等待统一审核。

人工 OCR 修订和 `ocr_error` 拒绝都不会创建 `KnowledgeRule` 或 `GlossaryTerm`。
后端测试现为 `37 passed`，Ruff、前端 ESLint、TypeScript 和 Next.js production
build 均通过。
