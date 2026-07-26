# LoL Daily Intel 开发交接文档（当前版）

更新时间：2026-07-25  
项目目录：`E:\leagueNews`

本文档以当前代码、当前数据库和已经实际验证的行为为准，用于替代已经过时的
`DEVELOPMENT_HANDOFF.md`。后续会话应优先阅读本文，再按需查看专题文档。

## 1. 当前结论

这是一个面向英雄联盟资讯的多信源采集、人工审核 AI 处理、事件聚合与报告生成项目。

当前已经完成：

- Riot 英文官网、腾讯英雄联盟官网、X、微博、百度贴吧和手动导入 Connector。
- 原始发布时间 `published_at`、原始正文、图文顺序和平台原始数据入库。
- Raw 信息逐条手动触发 AI 处理。
- LoL 相关性判断及人工审核。
- 英文内容翻译、实体提取、分类、摘要、重要性和可信度分析。
- 全部结构化 LLM 输出的 Schema 校验、业务校验、一次自动纠错重试和统一错误响应。
- RiotPhroxzon Patch Preview / Full Preview 图片 OCR。
- Patch 表格左右栏和纵向单元格的确定性配对。
- OCR 测试台、可调参数和生产参数配置。
- OCR 审核时人工修正表格内容，再让 AI 重新整理、翻译和分析。
- 人工审核反馈驱动的知识规则和可编辑术语表。
- 候选事件检索、AI 同事件判断、事件创建/增量更新、来源关系和冲突记录。
- 日报、周报、月报草稿生成及人工审核。
- 前台事件 Feed 和后台管理页。

尚未进入真实生产验证：

- 当前 85 条 Raw 全部仍为 `pending`。
- 尚无正式 `normalized_items`、`news_events`、报告或知识规则。
- AI 管线虽然有自动化测试，但还没有用真实内容逐条跑通并积累审核经验。
- 面向大量历史数据的问答知识库/RAG 尚未实现。
- “未来免人工审核模式”尚未实现；当前所有关键阶段仍要求人工确认。

## 2. 当前运行和数据库快照

截至 2026-07-25 最后一次检查：

| 项目 | 状态 |
| --- | --- |
| Web | `http://localhost:3000`，HTTP 200 |
| 管理页 | `http://localhost:3000/admin` |
| API | `http://localhost:8000`，health 为 `ok` |
| Swagger | `http://localhost:8000/docs` |
| pgAdmin | `http://localhost:5050` |
| PostgreSQL | `localhost:5432` |

数据库主要计数：

| 表/对象 | 数量 |
| --- | ---: |
| Sources | 15 |
| Connector Runs | 31 |
| Raw Items | 85 |
| Raw `pending` | 85 |
| Media Assets | 299 |
| Normalized Items | 0 |
| Media Extractions | 0 |
| Processing Runs | 0 |
| Review Tasks | 0 |
| News Events | 0 |
| Event Revisions | 0 |
| Knowledge Rules | 0 |
| Glossary Terms | 0 |
| Generated Reports | 0 |
| OCR Profiles | 1 |
| OCR Test Runs | 0 |

注意：这些是当前本地开发数据库的快照，不应在代码中写死。

## 3. 当前真实处理流程

旧 README 中“每条 normalized item 直接创建一个 event”的描述已经失效。当前实际流程：

```text
Connector / 手动导入
  -> raw_items + media_assets（pending）
  -> 人工点击“开始 AI 处理”
  -> AI 相关性判断
  -> 人工审核相关性
     -> 无关：Raw 变为 irrelevant，流程结束
     -> 相关：进入单条分析
  -> 符合条件的 Patch 图片 OCR + 表格解析 + AI 整理
  -> 基于正文和图片结构生成实体、分类、摘要、重要性、可信度
  -> 非中文正文按内容块翻译
  -> 人工统一审核
     -> 可修正 OCR 并让 AI 重新生成草稿
     -> 可退回并按反馈类型成长知识/术语
     -> 批准后创建 Normalized Item
  -> 人工再次触发事件处理
  -> AI 判断是否为事件
     -> 不是事件：标记 not_event
     -> 是事件：应用检索候选事件
  -> AI 只能在候选事件中选择 update，否则 create
  -> 人工审核事件草稿
  -> 创建/更新 News Event + Event Revision
  -> 人工按时间范围生成日报/周报/月报
  -> 人工审核报告
```

采集和 AI 处理完全解耦。Connector 不会自动消耗 LLM Token，新入库数据统一保持
`pending`，符合“逐条手动触发”的需求。

## 4. 相关性范围

相关性由 `RelevanceResult` Schema 约束。

保留：

- 英雄联盟端游。
- 英雄联盟电竞。
- 云顶之弈。
- 英雄联盟世界观、影视内容。
- Riot 公司新闻。
- LoL 周边、音乐和商业合作。

明确排除：

- Wild Rift / 英雄联盟手游。
- 2XKO。
- 与项目无关的私人内容。

不能仅按账号判断。例如 Spideraxe30 是保留信源，但每条内容仍独立判断。

相关性审核失败可选择 `relevance_correction`。反馈会立即生成可编辑的长期规则，作用域
支持：

- `global`
- `connector:{connector_type}`
- `source:{source_id}`

## 5. 翻译、分析和术语表

相关性批准后，系统生成一份统一的单条分析审核草稿，包括：

- 标准化标题。
- 原始正文。
- 中文标题和中文正文。
- 原语言、目标语言、翻译状态和模型。
- 摘要。
- 分类。
- 实体。
- 重要性分数 `0..1`。
- 可信度：`official`、`corroborated`、`unverified` 或 `rumor`。
- 批准采用的媒体提取 ID。
- Patch 图片结构化结果。

中文内容不会重复翻译；非中文内容按 `content_blocks` 翻译，保留图文顺序。

术语错误应使用 `translation_term` 反馈，并填写：

- 原词。
- 标准译名。
- 禁止译名。
- 作用域和备注。

术语会立即写入 `glossary_terms`，后续翻译和 Patch 图片整理会读取当前启用术语。
术语支持修改、版本递增和 `is_active=false` 停用。

## 6. LLM Schema 校验和错误处理

所有结构化 LLM 操作统一经过 `LLMClient._validated_json_completion`：

1. 要求提供商返回 JSON Object。
2. 执行 JSON 解码。
3. 执行对应 Pydantic Schema 校验。
4. 执行业务约束校验。
5. 首次失败时，将精简错误和原输出反馈给模型。
6. 模型重新输出一次完整 JSON。
7. 第二次仍失败才终止本次工作流。

当前 Schema：

- `RelevanceResult`
- `AnalysisResult`
- `TranslationResult`
- `PatchPreviewExtraction`
- `EventProfile`
- `EventResolution`
- `ReportDraft`

重要业务校验包括：

- 相关性范围与 `is_lol_relevant` 必须一致。
- 翻译必须返回全部输入块索引。
- Full Preview 必须包含解析出的改动。
- Event `update` 必须带 `event_id`。
- AI 只能选择应用传入的候选事件 ID。

DeepSeek 配置会自动传入关闭 thinking 的参数，避免思考内容破坏 JSON 输出。

统一错误格式：

```json
{
  "detail": "面向用户的错误信息",
  "error": {
    "code": "validation_error",
    "message": "面向用户的错误信息",
    "retryable": false,
    "details": []
  }
}
```

常见错误码：

- `validation_error`
- `not_found`
- `conflict`
- `llm_not_configured`
- `llm_invalid_response`
- `ocr_processing_error`
- `upstream_processing_error`
- `service_unavailable`

LLM、OCR 或网络失败不会写入正式 Normalized Item/Event。Processing Run 会进入
`failed`，可通过 retry 接口重新执行当前阶段。

## 7. OCR 和 Patch 表格管线

### 7.1 触发条件

图片理解只针对看起来像 Patch Preview 的内容：

- Raw 必须有图片。
- 标题或正文包含 `preview`、`full preview`、`patch`、`micropatch` 或 `hotfix`。
- RiotPhroxzon 的相关内容优先命中；其他明确包含 `patch` 的内容也可能命中。

### 7.2 当前正式处理方式

```text
RapidOCR
  -> 获取文本、坐标、置信度
  -> 检测左右列分隔位置
  -> 检测左栏完整横线和纵向合并单元格
  -> 识别 section 标题
  -> 生成 target -> raw_changes 键值对
  -> 结构置信度保护
  -> LLM 只整理和翻译已经配对的结构
```

AI 不负责从散乱 OCR 坐标猜测英雄与改动的归属。

Full Preview 结构置信度低于 `0.65` 时停止，不调用 LLM。Preview 可以只有目标名单，
`changes=[]` 是合法结果，防止模型猜测尚未公布的改动。

分类标题左侧的小箭头/图标有专门兼容逻辑：与
`CHAMPION/SYSTEM ... BUFFS/NERFS/ADJUSTMENTS` 同行且紧邻左侧的小框会作为装饰排除，
避免成为上一键值对的伪项目。

正式媒体提取当前 Schema 版本是 `v2`。

### 7.3 当前生产参数

六张现有样图已经由用户确认：

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

数据库目前只有一个激活的 OCR Profile。正式处理会读取这个 Profile；不要把参数重新
写死在代码里。

历史 OCR 测试数据已经按用户要求清理：

- 删除 71 条 `ocr_test_runs`。
- 删除 131 个 OCR/表格叠加图。
- 删除 1 个非激活 OCR Profile。
- 保留当前激活 Profile。
- 激活 Profile 原来引用的测试运行已被删除，`source_test_run_id` 已置空。

这些历史数据没有单独备份。

### 7.4 OCR 测试台

入口：管理页的“OCR 测试台”。

测试台：

- 只显示已经下载到本地的 RiotPhroxzon Patch 图片。
- 可以调缩放、灰度、对比度、锐度、OCR 检测参数、分隔线和表格线参数。
- 保存逐行 OCR、识别框图、表格框图和键值结构。
- 不调用 LLM。
- 不修改正式 `media_extractions`。
- 满意的测试运行可激活为新的生产 Profile。

### 7.5 OCR 人工修订闭环

单条分析审核页会读取 `proposal.approved_media_extraction_ids` 并显示表格编辑器。
审核员可以：

- 修改 section 标题。
- 修改左栏英雄/装备/系统对象。
- 修改右栏改动，每行一项。
- 添加或删除对象。
- 填写修订原因。

保存接口：

```http
POST /api/v1/workflows/reviews/{review_id}/correct-ocr
```

系统行为：

1. 只允许修订当前 pending 的 `item_analysis` 审核。
2. 校验 extraction 属于当前审核和当前 Raw。
3. 不覆盖原始 MediaExtraction。
4. 创建 `schema_version=v2-manual` 的新修订。
5. 在 `processing_config.manual_correction` 中记录来源 extraction、review、说明和时间。
6. 用人工表格重新调用 Patch 整理/翻译 LLM。
7. 用新图片结构重新生成分析草稿，并重新生成正文翻译草稿。
8. 原 pending Review 变为 `superseded`。
9. 创建新的 pending Review，仍需人工批准。

人工 OCR 修订不会创建 Knowledge Rule 或 Glossary Term。可人工修正时应直接使用编辑器；
如果只选择 `ocr_error` 并退回，则只记录错误，不成长知识，之后需要 retry。

## 8. 事件处理和候选检索

单条分析批准后只创建 `NormalizedItem`，不会自动创建事件。需要再次手动调用：

```http
POST /api/v1/normalized-items/{normalized_item_id}/process-event
```

先由 AI 判断是否是事件。支持的典型类型：

- 转会。
- 赛事和锦标赛。
- Patch Preview。
- 游戏更新和热修复。
- 皮肤爆料与正式发布。
- 公告、事故和其他可持续跟踪事件。

纯观点、攻略、闲聊或没有具体发生事项的内容可判为非事件。

候选事件 SQL 由应用程序固定生成，AI 不编写或执行 SQL。检索逻辑：

1. 只看 `active` 或 `monitoring` 事件。
2. 按事件类型使用不同时间窗口。
3. 事件时间不能晚于本条信息发布时间超过两天。
4. 结合实体重叠、标题相似度、事件类型和分类打分。
5. 最多返回 Top 5。
6. AI 只能在 Top 5 中选择 `update`，否则必须 `create`。

时间窗口：

| 事件类型 | 天数 |
| --- | ---: |
| hotfix / incident / esports_match | 7 |
| patch_preview | 14 |
| game_update | 21 |
| skin_leak / skin_release | 30 |
| tournament | 60 |
| roster_change / announcement | 90 |
| other | 30 |

事件批准后：

- 创建或更新 `news_events`。
- 通过 `event_items` 记录多信源关系。
- 写入递增版本的 `event_revisions`。
- 保存来源关系、是否增加新信息、冲突和聚合理由。
- 更高权威来源可以替换 `primary_item_id`。

默认权威级别：

- Riot 官网、腾讯官网和已知官方账号：100。
- 普通 X/微博：60。
- 百度贴吧：30。
- 其他：50。
- Source 的 `connector_config.authority_level` 可以覆盖默认值。

## 9. 人工反馈与知识成长

审核拒绝类型和副作用：

| feedback_type | 行为 |
| --- | --- |
| `relevance_correction` | 创建相关性知识规则 |
| `analysis_correction` | 创建单条分析知识规则 |
| `event_correction` | 创建事件聚合知识规则 |
| `translation_term` | 只更新术语表 |
| `ocr_error` | 只记录，不成长知识或术语 |
| `other` | 只记录 |

拒绝后 Processing Run 进入 `revision_requested`，再调用：

```http
POST /api/v1/workflows/runs/{run_id}/retry
```

知识规则和术语均支持后台人工修改。修改会增加版本号，错误规则可以停用，不需要删除
审计记录。

当前知识库为空，因此所有关键结果仍要求人工确认。未来免审核模式需要另行设计可信度
门槛、规则成熟度和自动回退机制，当前代码不能直接认为已经支持。

## 10. 报告

报告只能从已经批准的 Event 生成，支持：

- `daily`
- `weekly`
- `monthly`

生成时按 `period_start` / `period_end` 查询本期首次发布或本期发生更新的事件，并把：

- Event ID。
- 最新 Event Revision ID。
- 是否为本期新事件。
- 重要性和可信度。

一起交给 LLM。报告生成后是 `pending_review`，人工批准后才成为 `approved`。

当前数据库没有 Event，因此现在调用报告生成会返回“该时间范围没有事件或事件更新”。

## 11. Connector 和 Source

注册的 Connector：

- `manual`
- `riot_official`
- `tencent_lol`
- `x_twitter`
- `weibo`
- `baidu_tieba`

当前 Source：

| ID | Connector | Source |
| ---: | --- | --- |
| 1 | `manual` | 手动导入 |
| 2 | `tencent_lol` | 腾讯英雄联盟官方网站 |
| 3 | `riot_official` | Riot Games Official |
| 4 | `x_twitter` | RiotPhroxzon |
| 7 | `x_twitter` | LoL Esports |
| 8 | `x_twitter` | Spideraxe30 |
| 12 | `x_twitter` | SkinSpotlights |
| 16 | `x_twitter` | League of Legends |
| 17 | `weibo` | 英雄联盟赛事 |
| 18 | `weibo` | 英雄联盟 |
| 19 | `weibo` | 恋恋红茶_244 |
| 20 | `weibo` | 召唤师Park |
| 21 | `weibo` | _尧阿尧y_ |
| 22 | `baidu_tieba` | lol半价吧 · 小老鼠小伟 |
| 23 | `baidu_tieba` | lol半价吧 · 凤舞天_惊鸿恋 |

Source ID 不是跨环境稳定标识，调用前应以 `GET /api/v1/sources` 为准。

统一 Connector Run：

```http
POST /api/v1/connectors/{connector_type}/run

{
  "source_id": 4,
  "limit": 5,
  "since": null,
  "options": {}
}
```

Connector 统一输出 canonical item，经共享 ingestion：

- 以 Source + `external_id` / 内容哈希去重。
- 保存平台原始 `published_at`，不是数据库创建时间。
- 保存 `content_blocks`，维持图文顺序。
- 下载远程图片到 `apps/web/public/media/{connector_type}`。
- 写入 `raw_items`、`media_assets` 和 `connector_runs`。

认证资料：

- X Cookie：`.secrets/x-cookies.json`
- 微博 Playwright Profile：`.secrets/weibo-browser-profile`

`.secrets`、`.env` 和 `.run` 都被 `.gitignore` 排除，不能提交。

Connector 的详细调用、登录和故障排查见
[`CONNECTOR_OPERATIONS_GUIDE.md`](CONNECTOR_OPERATIONS_GUIDE.md)。

## 12. 主要数据模型和状态

### 12.1 数据模型职责

- `sources`：具体站点或账号。
- `connector_runs`：采集运行和计数。
- `raw_items`：无损原始信息，含 `published_at`。
- `media_assets`：图片/视频及其内容块位置。
- `processing_runs`：人工触发 AI 工作流运行。
- `review_tasks`：各阶段 AI 草稿、审核结果和反馈。
- `knowledge_rules`：相关性、分析和事件聚合规则。
- `glossary_terms`：翻译术语。
- `media_extractions`：正式图片 OCR/结构化结果及人工修订版本。
- `ocr_profiles`：生产 OCR 参数。
- `ocr_test_runs`：OCR 测试台历史。
- `normalized_items`：批准后的单条标准化资讯。
- `news_events`：聚合事件。
- `event_items`：事件和多条资讯的关系。
- `event_revisions`：事件每次创建/更新的快照。
- `generated_reports`：日报、周报和月报草稿。

### 12.2 主要状态

Raw：

```text
pending
processing
relevance_review
irrelevant
item_review
analyzed
processed
```

Processing Run：

```text
running
awaiting_review
revision_requested
failed
completed
```

Review：

```text
pending
approved
rejected
superseded
```

Normalized Item 的事件状态：

```text
pending
processing
event_review
not_event
linked
```

## 13. 主要 API

所有路径前缀为 `/api/v1`。

### 采集和原始数据

```text
GET  /sources
POST /sources
GET  /connectors
GET  /connectors/runs
POST /connectors/{connector_type}/run
POST /imports/manual
GET  /raw-items
GET  /media-assets
```

### 单条和审核

```text
POST /raw-items/{id}/process
GET  /workflows/runs
GET  /workflows/runs/{id}
GET  /workflows/runs/{id}/reviews
GET  /workflows/reviews?status=pending
POST /workflows/reviews/{id}/approve
POST /workflows/reviews/{id}/reject
POST /workflows/reviews/{id}/correct-ocr
POST /workflows/runs/{id}/retry
GET  /normalized-items
```

### OCR

```text
GET  /ocr-lab/assets
GET  /ocr-lab/runs
POST /ocr-lab/runs
GET  /ocr-lab/profiles
POST /ocr-lab/runs/{id}/activate
GET  /media-assets/extractions
POST /media-assets/{id}/extract-patch-preview
```

### 事件、知识和报告

```text
POST  /normalized-items/{id}/process-event
POST  /normalized-items/{id}/translate
GET   /events
GET   /events/feed
GET   /knowledge/rules
POST  /knowledge/rules
PATCH /knowledge/rules/{id}
GET   /knowledge/glossary
POST  /knowledge/glossary
PATCH /knowledge/glossary/{id}
GET   /reports
POST  /reports/generate
POST  /reports/{id}/approve
POST  /reports/{id}/reject
```

## 14. 数据库迁移

当前已应用：

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

OCR 人工修订复用 `media_extractions.processing_config`，没有新增迁移。

`start.ps1` 每次会幂等执行 007，然后读取 `schema_migrations`，只补跑缺失的
008–014。不要恢复旧的字符串/引号迁移检测方式，否则可能重复执行带数据迁移。

## 15. 本地启动、停止和验证

首次准备见 [`LOCAL_RUNBOOK.md`](LOCAL_RUNBOOK.md)。

日常启动：

```powershell
Set-Location E:\leagueNews
.\scripts\start.ps1 -SkipBrowser
```

或双击 `start.cmd`。

停止：

```powershell
.\scripts\stop.ps1
```

停止脚本只停止已记录的 Web/API 进程和 Compose 容器，不删除 PostgreSQL Volume。

日志：

```text
E:\leagueNews\.run\logs\api.out.log
E:\leagueNews\.run\logs\api.error.log
E:\leagueNews\.run\logs\web.out.log
E:\leagueNews\.run\logs\web.error.log
```

后端检查：

```powershell
Set-Location E:\leagueNews\services\api
.\.venv\Scripts\python.exe -m ruff check app tests
.\.venv\Scripts\python.exe -m pytest
```

前端检查：

```powershell
Set-Location E:\leagueNews
pnpm --filter web lint
pnpm --filter web build
```

最后一次结果：

- Ruff：通过。
- Pytest：`37 passed`。
- 前端 ESLint：通过。
- TypeScript / Next.js production build：通过。
- pytest 有一个第三方 Starlette deprecation warning。
- Windows 退出 pytest 时偶尔会因系统临时目录权限输出清理 `PermissionError`，测试本身
  已经全部通过。

注意：在正在运行的 Next.js dev server 上执行 production build，可能使当前页面缓存
短暂返回 500。构建后用 stop/start 脚本重启即可。

## 16. 关键代码入口

### 后端

- `services/api/app/workflows/reviewed_pipeline.py`：三阶段人工审核主流程、事件候选和落库。
- `services/api/app/services/llm.py`：全部 LLM Schema、提示词、校验和重试。
- `services/api/app/workflows/translate_item.py`：按内容块翻译。
- `services/api/app/workflows/understand_media.py`：正式 Patch 图片处理。
- `services/api/app/services/media_ocr.py`：RapidOCR 和图像预处理。
- `services/api/app/services/patch_table.py`：表格结构检测和键值配对。
- `services/api/app/workflows/ocr_lab.py`：OCR 测试运行和 Profile 激活。
- `services/api/app/workflows/generate_report.py`：日报/周报/月报。
- `services/api/app/services/ingestion.py`：共享入库和去重。
- `services/api/app/services/connector_runner.py`：统一 Connector Run。
- `services/api/app/api/errors.py`：统一错误响应。

### 前端

- `apps/web/components/admin-console.tsx`：后台单条处理、审核、OCR、知识和报告。
- `apps/web/components/event-feed.tsx`：前台事件 Feed。
- `apps/web/app/globals.css`：前后台样式。

### 测试

- `services/api/tests/test_llm.py`
- `services/api/tests/test_reviewed_pipeline.py`
- `services/api/tests/test_patch_table.py`
- `services/api/tests/test_*_provider.py`

## 17. 当前已知限制和风险

1. **真实 AI 结果尚未验收。**  
   85 条 Raw 尚未开始处理。Prompt 和 Schema 通过了自动化测试，但翻译、实体、重要性、
   可信度、事件归并仍需在真实样本中校正。

2. **知识和术语为空。**  
   当前每条都应人工审核。初期错误反馈会直接成长长期规则，录入后应检查表述；错误规则
   可以在后台修改或停用。

3. **OCR 参数不能覆盖所有未来图片。**  
   当前六张图表现良好，但新模板可能失败。优先使用单条人工修订，不要为了极少数异常
   不断破坏全局参数。只有发现稳定、可复现的模板变化才调整生产 Profile 或解析器。

4. **OCR 修订编辑器编辑的是表格语义层。**  
   它不会改写原始 OCR 行和坐标。原始证据保留，新建 `v2-manual` 修订，这是预期设计。

5. **事件候选仍是轻量检索。**  
   当前依赖时间窗、实体、标题、分类和类型，没有向量数据库。事件规模扩大后再评估
   embedding/全文检索，不要把全部历史事件直接发送给 LLM，也不要让 LLM 生成 SQL。

6. **知识库问答尚未实现。**  
   未来应检索相关 Event、Event Revision、Normalized Item 和 Raw 原文后回答，并返回
   引用，不应把所有历史数据放入上下文。

7. **前端是管理型 MVP。**  
   审核草稿仍包含较多 JSON 展示，后续可逐步改为专门字段视图，但先用真实流程确认
   数据结构。

8. **README 部分内容过时。**  
   有关“每条信息直接创建一个事件”的内容不能作为当前规范。当前以本文档、
   `REVIEWED_AI_WORKFLOW.md` 和实际代码为准。

## 18. 推荐的下一会话工作顺序

建议不要立即批量处理 85 条数据。按下列顺序继续：

1. 检查 `.env` 中 LLM Provider、模型和 Key 是否可用，不要在输出中打印 Key。
2. 在管理页选择 5–10 条代表性 Raw：
   - Riot 英文长文。
   - RiotPhroxzon Preview。
   - RiotPhroxzon Full Preview。
   - 腾讯中文公告。
   - LoL Esports/X 赛事消息。
   - Spideraxe 的无关或 2XKO 内容。
   - 微博或贴吧非官方爆料。
3. 逐条跑相关性审核，验证保留/排除边界。
4. 批准相关性后检查：
   - 翻译是否遵循术语。
   - Patch 图片键值关系和中文整理。
   - 实体、分类、摘要、重要性和可信度。
5. OCR 小错误直接使用人工修订编辑器；只有系统性错误才改 OCR Profile/解析器。
6. 对审核错误选择准确的 feedback type，观察知识规则或术语是否按预期成长。
7. 批准少量 Normalized Item 后，分别测试：
   - 非事件。
   - 新事件创建。
   - 同一事件的第二来源更新。
   - 官方来源替换非官方主来源。
   - 冲突信息记录。
8. 有足够 Event 后再测试日报、周报和月报。
9. 真实流程稳定后，才设计批量处理、自动审核模式和知识库问答。

## 19. Git 和文件安全

仓库当前分支为 `master`，但尚未建立首个正式基线提交。`git status` 中项目主体整体为
untracked，这是当前项目本身，不是可清理垃圾。

后续会话必须遵守：

- 不要执行 `git clean`。
- 不要执行 `git reset --hard`。
- 不要 checkout 覆盖整个工作区。
- 不要删除 `.env`、`.secrets`、`.run` 或媒体目录。
- 不要假设 untracked 文件可丢弃。
- 建立首个提交前，先核对 `.gitignore`、敏感文件和大量本地媒体的提交策略，并取得用户
  明确确认。

## 20. 专题文档

- [`LOCAL_RUNBOOK.md`](LOCAL_RUNBOOK.md)：安装、启动、停止和故障排查。
- [`CONNECTOR_OPERATIONS_GUIDE.md`](CONNECTOR_OPERATIONS_GUIDE.md)：各 Connector
  配置、调用和登录。
- [`MANUAL_IMPORT_GUIDE.md`](MANUAL_IMPORT_GUIDE.md)：手动导入图文信息。
- [`REVIEWED_AI_WORKFLOW.md`](REVIEWED_AI_WORKFLOW.md)：人工审核 AI 工作流和 API 示例。
- [`CONNECTOR_HANDOFF.md`](CONNECTOR_HANDOFF.md)：Connector 开发约定。
