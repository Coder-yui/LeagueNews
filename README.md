# LeagueNews V3

英雄联盟垂直领域的多信源采集、AI 消息处理与发布系统。

## 当前能力

```text
Source 周期调度或手工触发
  -> 平台 Connector
  -> 不可变 RawItem + MediaAsset + provenance
  -> PostgreSQL 持久化 Pipeline Job
  -> LangGraph Item Graph（8 个可恢复阶段）
  -> NormalizedItem 发布
  -> LangGraph Event Graph
  -> Event 投影
  -> LangGraph Daily Report Graph
```

- 已接入 Riot 官网、腾讯 LOL 官网、X、微博、百度贴吧和手工导入。
- 新内容默认由独立 Worker 调用 LangGraph；技术 checkpoint 与业务 checkpoint 分层持久化。
- 已发布消息可以从任意业务阶段创建新运行，复用上游已审核结果并选择人工或自动模式。
- 人工审核使用 LangGraph interrupt/resume；已发布消息和事件也支持直接、幂等、有版本审计的人工修订。
- 管理台提供审核、采集计划、采集日志、管线日志、失败恢复、撤回、知识与 OCR Lab。
- 已有单机 Docker Compose 生产部署、Caddy 边界认证、GHCR 镜像发布、备份与恢复脚本。
- Event 聚合和按上海自然日生成的日报均已进入独立 LangGraph。
- embedding/向量召回和应用内多用户权限尚未实现；当前数据规模不依赖它们。

## 架构边界

- Connector 是平台级采集能力；Source 是具体账号或站点。
- Connector 只映射统一 `RawItemCandidate`；共享 ingestion 负责校验、去重、媒体落盘和入库。
- `raw_items.content_blocks` 是不可变原文事实来源，后续处理不得回写。
- `normalized_items` 是单条消息当前投影，历史版本保存在 `normalized_item_revisions`。
- 消息处理在发布 `NormalizedItem` 后结束；独立 Event 层只消费发布投影，不回写 RawItem。
- 自动与人工流程使用相同结构化草稿；区别记录在决定来源和运行模式中。
- LangGraph checkpoint 只负责故障恢复；ProcessingCheckpoint、Revision 和 ReviewTask 承担业务审计。

## 目录

- `apps/web`：Next.js 公开页面与管理台
- `services/api/app/connectors`：平台 Connector
- `services/api/app/orchestration`：Graph、运行服务、持久化 checkpoint 和实验执行器
- `services/api/app/services`：ingestion、队列、调度、媒体、LLM 与领域服务
- `services/api/app/workflows`：V3 baseline 复用的领域处理函数和历史 V2 运行兼容实现
- `services/api/app/models`：SQLAlchemy 模型
- `infra/postgres/migrations`：只追加、不可改写的迁移历史
- `deploy`：生产 Compose、Caddy、部署/备份/恢复脚本
- `docs`：权威运行、架构与交接文档

## 本地启动

首次准备（Windows PowerShell）：

```powershell
Copy-Item .env.example .env

Set-Location services\api
uv sync --dev

Set-Location ..\..
pnpm install
```

首次准备（macOS/Linux，需 uv、pnpm，以及 OrbStack 或 Docker Desktop 提供 Docker）：

```bash
cp .env.example .env

cd services/api
uv sync --dev

cd ../..
pnpm install
```

在 `.env` 配置数据库和 OpenAI-compatible LLM，然后启动：

```powershell
# Windows
.\scripts\start.ps1
```

```bash
# macOS/Linux
./scripts/start.sh
```

不打开浏览器：Windows 加 `-SkipBrowser`，macOS/Linux 加 `--skip-browser`。

关闭：Windows 运行 `.\scripts\stop.ps1`，macOS/Linux 运行 `./scripts/stop.sh`。

本地地址：

- 网站：http://localhost:3000
- 管理台：http://localhost:3000/admin
- API 文档：http://localhost:8000/docs
- pgAdmin：http://localhost:5050

未配置 `OPENAI_API_KEY` 时仍可采集入库，但自动与人工 AI 流程会明确失败，不生成兜底结果。

## 验证

```powershell
# Windows
services\api\.venv\Scripts\python.exe -m ruff check services/api/app services/api/scripts services/api/tests
services\api\.venv\Scripts\python.exe -m pytest services/api/tests -q
pnpm lint:web
pnpm build:web
```

```bash
# macOS/Linux
services/api/.venv/bin/python -m ruff check services/api/app services/api/scripts services/api/tests
services/api/.venv/bin/python -m pytest services/api/tests -q
pnpm lint:web
pnpm build:web
```

## 文档入口

- [完整文档导航](docs/README.md)
- [当前架构](docs/ARCHITECTURE.md)
- [LangGraph 架构](docs/V3_LANGGRAPH_ARCHITECTURE.md)
- [本地实验](docs/V3_LOCAL_EXPERIMENTATION.md)
- [本地运行](docs/LOCAL_RUNBOOK.md)
- [Connector 操作与排障](docs/CONNECTOR_OPERATIONS_GUIDE.md)
- [Connector 架构](docs/CONNECTOR_ARCHITECTURE.md)
- [RawItem 内容模型](docs/RAW_ITEM_CONTENT_MODEL.md)
- [消息处理流程](docs/REVIEWED_AI_WORKFLOW.md)
- [消息分类规则](docs/MESSAGE_CLASSIFICATION.md)
- [重要性计算](docs/IMPORTANCE_SCORING_POLICY.md)
- [OCR 配置记录](docs/OCR_CONFIGURATION.md)
- [生产部署](docs/PRODUCTION_DEPLOYMENT.md)
