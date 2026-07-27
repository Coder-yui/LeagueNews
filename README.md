# LoL Daily Intel

英雄联盟垂直领域的多信源采集、人工审核和消息展示项目。

当前已启用的完整链路是：

```text
Source
  -> 平台 Connector
  -> 不可变 RawItem + MediaAsset
  -> LoL 相关性 AI / 人工审核
  -> 可选版本图片 OCR / 人工修正
  -> 翻译 / 术语审核
  -> 基于已批准中文内容的分析与摘要 / 人工审核
  -> NormalizedItem
  -> 消息卡片与消息详情
```

事件聚合 v2 已有核心数据模型、事务服务、确定性候选检索、受审核 AI 工作流、管理台
以及公开事件列表和详情时间线；报告尚未实现。后续边界与实施顺序见
[`docs/DEVELOPMENT_HANDOFF.md`](docs/DEVELOPMENT_HANDOFF.md)。

## 架构边界

- Connector 是平台级采集能力，如 `x_twitter`、`tencent_lol`、`riot_official`。
- Source 是具体账号或站点。多个 X 账号共享同一个 Connector，但各自拥有独立 Source。
- Connector 只把平台数据映射为统一 `RawItemCandidate`；共享 ingestion 负责校验、去重、
  媒体落盘和入库。
- `raw_items.content_blocks` 是原始图文的唯一事实来源，处理层不得回写。
- `normalized_items` 只保存经过全部人工审核的单条消息结果。
- 事件聚合只消费已批准的 `NormalizedItem`，不得改变采集和单条处理基座。

详细设计：

- [Connector 架构](docs/CONNECTOR_ARCHITECTURE.md)
- [RawItem 与 ContentBlock v2](docs/RAW_ITEM_CONTENT_MODEL.md)
- [人工审核单条处理流程](docs/REVIEWED_AI_WORKFLOW.md)
- [本地运行手册](docs/LOCAL_RUNBOOK.md)
- [Connector 运行与排障](docs/CONNECTOR_OPERATIONS_GUIDE.md)
- [开发 Handoff](docs/DEVELOPMENT_HANDOFF.md)

## 目录

- `apps/web`：Next.js 消息页和管理台
- `services/api/app/connectors`：平台 Connector 与映射
- `services/api/app/services`：共享 ingestion、媒体、LLM 等服务
- `services/api/app/workflows`：显式的受审核处理流程
- `services/api/app/models`：SQLAlchemy 模型
- `infra/postgres/migrations`：不可删除或改写的数据库迁移历史

## 本地启动

首次安装依赖并配置 `.env` 后：

```powershell
Set-Location E:\leagueNews
.\scripts\start.ps1
```

不自动打开浏览器：

```powershell
.\scripts\start.ps1 -SkipBrowser
```

关闭：

```powershell
.\scripts\stop.ps1
```

地址：

- 网站：http://localhost:3000
- 管理台：http://localhost:3000/admin
- API 文档：http://localhost:8000/docs
- pgAdmin：http://localhost:5050

当前主要读取接口：

```text
GET /api/v1/raw-items
GET /api/v1/media-assets
GET /api/v1/normalized-items
GET /api/v1/normalized-items/published
GET /api/v1/normalized-items/{id}/published
GET /api/v1/events
GET /api/v1/events/{id}
GET /api/v1/events/{id}/messages
GET /api/v1/event-workflows/runs
GET /api/v1/event-workflows/reviews
GET /api/v1/workflows/runs
GET /api/v1/workflows/reviews
GET /api/v1/knowledge/rules
GET /api/v1/knowledge/glossary
```

未配置 `OPENAI_API_KEY` 时采集和 RawItem 入库仍可工作，但 AI 处理会返回明确错误，
不会生成兜底结果。
