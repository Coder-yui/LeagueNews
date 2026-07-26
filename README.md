# LoL Daily Intel

英雄联盟垂直领域多信源 AI 情报项目。当前稳定边界是：平台采集 → 统一
RawItem → LoL 相关性审核 → 可选版本图片 OCR → 单条分析与翻译审核。
事件聚合和报告不属于当前启用流程。

日常启动、检查和退出请参考 [`docs/LOCAL_RUNBOOK.md`](docs/LOCAL_RUNBOOK.md)。
手动添加图文资讯请参考 [`docs/MANUAL_IMPORT_GUIDE.md`](docs/MANUAL_IMPORT_GUIDE.md)。
调用各个平台 Connector、检查入库和排查故障请参考
[`docs/CONNECTOR_OPERATIONS_GUIDE.md`](docs/CONNECTOR_OPERATIONS_GUIDE.md)。
采集层架构与新增平台规范见
[`docs/CONNECTOR_ARCHITECTURE.md`](docs/CONNECTOR_ARCHITECTURE.md)。
切换到新的开发会话前请先阅读
[`docs/DEVELOPMENT_HANDOFF2.md`](docs/DEVELOPMENT_HANDOFF2.md)。

完成首次依赖安装后，可双击 `start.cmd` 一键启动，双击 `stop.cmd` 关闭。
启动脚本在 Docker daemon 未运行时会尝试自动启动 Docker Desktop。

## Architecture

这里把“采集能力”和“具体信源”分开：

- `connector` 是可复用的采集实现，例如 `x_twitter`、`tencent_lol`、`riot_official`。一个平台通常只实现一个 connector。
- `source` 是具体发布者或站点，例如 X 的 `@RiotPhroxzon`、`@LeagueOfLegends`，或微博的某个指定账号。
- 多个 source 可以共用同一个 connector；每次运行只采集一个 source，入库数据通过 `source_id` 保留准确来源。
- 账号型平台用 `sources.external_key` 保存规范化账号标识；平台专属的非敏感参数放在 `sources.connector_config`。
- connector 先获取平台 record，再通过无副作用 mapper 输出经过校验的
  `RawItemCandidate`；后续入库和处理不需要了解原平台。

```text
connector
  -> platform record
  -> validated RawItemCandidate
  -> shared ingestion
  -> raw_items + source payloads + media_assets (pending)
  -> relevance review
  -> optional reviewed patch OCR
  -> reviewed analysis + translation
  -> normalized_items
```

- `apps/web` — Next.js 展示层
- `services/api/app/connectors` — 可插拔采集层；MVP 提供手动导入 connector
- `services/api/app/services/ingestion.py` — 与来源无关的校验、去重、媒体落盘和入库
- `services/api/app/services/connector_runner.py` — connector registry 的统一手动运行入口
- `services/api/app/workflows` — 明确步骤的分析 workflow，不使用复杂 Agent
- `services/api/app/models` — SQLAlchemy 持久化模型
- `services/api/app/api` — FastAPI 接口

### v2 数据职责

- `raw_items`：所有 connector 共用的不可变结构化原文；`content_blocks` 是完整内容的唯一事实来源。平台调试载荷单独保存在 `raw_item_source_payloads`，处理状态保存在工作流表。
- `media_assets`：原始图片及其在内容块中的位置；OCR 和视觉分析结果不属于采集基座。
- `normalized_items`：最终人工批准的单条处理结果，包括中文标题、摘要、分类、
  实体、重要性、可信度等级与分数、正文翻译和分析版本。
- `media_extractions`：版本预览图片的 OCR、表格化结果和人工修订历史。
- `normalized_item_media_extractions`：最终条目采用的图片提取及其结构化中文译文。
- `processing_runs`、`review_tasks`：处理运行和人工审核记录；拒绝的运行不会生成
  `normalized_items`，重新处理会创建关联旧运行的新运行。

完整流程和状态约束见
[`docs/REVIEWED_AI_WORKFLOW.md`](docs/REVIEWED_AI_WORKFLOW.md)。

## Local development

1. 复制环境变量：`Copy-Item .env.example .env`
2. 启动数据库：`docker compose up -d postgres`
3. 安装后端：`Set-Location services/api; uv sync --dev`
4. 启动后端：`uv run uvicorn app.main:app --reload`
5. 新终端安装前端：`pnpm install`
6. 启动前端：`pnpm dev:web`

访问 http://localhost:3000，API 文档位于 http://localhost:8000/docs。

数据库图形界面位于 http://localhost:5050。使用 `.env` 中的
`PGADMIN_DEFAULT_EMAIL` 和 `PGADMIN_DEFAULT_PASSWORD` 登录；预置服务器首次连接时，
输入 `POSTGRES_PASSWORD` 并选择保存密码。

未配置 `OPENAI_API_KEY` 时，原始资讯仍会入库为 `pending`，接口返回 `503` 并提示配置 LLM；不会生成兜底分析结果。

## 手动导入

后端启动后打开 http://localhost:8000/docs，展开 `POST /api/v1/imports/manual`，点击 **Try it out**，输入：

```json
{
  "title": "26.14 版本平衡调整预告",
  "url": "https://example.com/news/26-14",
  "content": "Riot 公布了新版本的英雄与装备调整方向。"
}
```

旧的 `title/url/content` 请求仍兼容。需要保留文章图文顺序时，可以传入 `content_blocks`：

```json
{
  "source_id": 2,
  "title": "示例文章",
  "author": "作者名",
  "language": "zh-CN",
  "content_blocks": [
    {"type": "paragraph", "text": "第一段文字"},
    {
      "type": "image",
      "storage_path": "/media/example.png",
      "alt_text": "图片说明"
    },
    {"type": "paragraph", "text": "图片后的第二段文字"}
  ]
}
```

查询接口：

- `GET /api/v1/raw-items`
- `GET /api/v1/media-assets?raw_item_id=1`
- `GET /api/v1/normalized-items`
- `GET /api/v1/events`
- `GET /api/v1/events/feed`（前端图文、来源及中英文切换数据）

也可以在 PowerShell 中导入。将请求体显式转为 UTF-8，可避免 Windows PowerShell 5 的中文编码问题：

```powershell
$payload = @{
  title = "26.14 版本平衡调整预告"
  url = "https://example.com/news/26-14"
  content = "Riot 公布了新版本的英雄与装备调整方向。"
} | ConvertTo-Json

Invoke-RestMethod `
  -Uri "http://localhost:8000/api/v1/imports/manual" `
  -Method Post `
  -ContentType "application/json; charset=utf-8" `
  -Body ([Text.Encoding]::UTF8.GetBytes($payload))
```

DeepSeek 使用 OpenAI-compatible 接口，在根目录 `.env` 中配置：

```dotenv
OPENAI_API_KEY=你的_DeepSeek_Key
OPENAI_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-v4-flash
```

数据模型说明：[RawItem 与 ContentBlock v2](docs/RAW_ITEM_CONTENT_MODEL.md)。
