# LoL Daily Intel 本地运行手册

## 服务

| 服务 | 地址 | 用途 |
| --- | --- | --- |
| Next.js | http://localhost:3000 | 已发布消息、事件列表与详情 |
| 管理台 | http://localhost:3000/admin | 审核、自动化日志、采集配置、撤回、知识与 OCR Lab |
| FastAPI Swagger | http://localhost:8000/docs | API 文档与调试 |
| FastAPI health | http://localhost:8000/api/v1/health | 后端健康检查 |
| pgAdmin | http://localhost:5050 | PostgreSQL 管理 |
| PostgreSQL | localhost:5432 | 数据库 |

## 首次准备

```powershell
Set-Location E:\leagueNews
Copy-Item .env.example .env

Set-Location E:\leagueNews\services\api
$env:UV_CACHE_DIR = "E:\leagueNews\.uv-cache"
uv sync --dev

Set-Location E:\leagueNews
pnpm install
```

在 `.env` 配置数据库和 OpenAI-compatible LLM。不要提交 `.env`、`.secrets` 或
`.run`。没有 LLM Key 时仍可采集入库，但不能进入 AI 审核流程。

## 启动

推荐在项目根目录运行：

```powershell
.\scripts\start.ps1
```

脚本检查依赖和 Docker daemon，启动 PostgreSQL、pgAdmin，使用与生产环境相同的迁移入口
应用全部待执行迁移，然后启动 FastAPI、Next.js、Pipeline Worker 和采集调度器。无需打开
浏览器时：

```powershell
.\scripts\start.ps1 -SkipBrowser
```

后台日志位于 `E:\leagueNews\.run\logs`。

排障时可以分别启动基础服务，但还需要自行启动迁移、Pipeline Worker 和采集调度器；
日常开发优先使用 `scripts/start.ps1`：

```powershell
Set-Location E:\leagueNews
docker compose up -d

Set-Location E:\leagueNews\services\api
$env:UV_CACHE_DIR = "E:\leagueNews\.uv-cache"
uv run uvicorn app.main:app --reload

Set-Location E:\leagueNews
pnpm dev:web
```

## 启动后检查

```text
GET /api/v1/health
GET /api/v1/sources
GET /api/v1/raw-items
GET /api/v1/media-assets
GET /api/v1/normalized-items
GET /api/v1/normalized-items/published
GET /api/v1/workflows/runs
GET /api/v1/workflows/reviews?status=pending
GET /api/v1/pipeline/jobs
GET /api/v1/collection-schedules
```

新采集或手工导入的 RawItem 默认创建持久化 `pipeline_job`，由 Worker 自动完成消息发布和
事件判断。`POST /api/v1/raw-items/{id}/process` 是显式人工审核入口，不会直接发布消息。
已发布结果有误时，从管理台选择撤回阶段和后续人工/自动模式。

## 关闭

```powershell
Set-Location E:\leagueNews
.\scripts\stop.ps1
```

脚本停止本项目记录的前后端进程，并执行 `docker compose stop`。数据卷会保留。

`docker compose down` 会移除容器和网络但默认保留命名数据卷。不要执行
`docker compose down -v`，除非明确要永久删除数据库数据。

## 验证

后端：

```powershell
Set-Location E:\leagueNews\services\api
.venv\Scripts\python.exe -m ruff check app scripts tests
.venv\Scripts\python.exe -m pytest -q
```

前端：

```powershell
Set-Location E:\leagueNews
pnpm lint:web
pnpm build:web
```

执行 `pnpm install` 或 production build 前，最好先停止前端开发服务器，避免 Windows
锁住 `node_modules` 或 `.next` 文件。

## 常见问题

- Docker 无法连接：启动 Docker Desktop，再运行 `docker info` 和
  `docker compose ps`。
- 3000/8000 被占用：先停止上一次启动的对应进程，不要重复启动实例。
- LLM 返回 503：检查 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`MODEL_NAME`，重启后端。
- pgAdmin 登录使用 `PGADMIN_DEFAULT_EMAIL` 和 `PGADMIN_DEFAULT_PASSWORD`；连接数据库
  使用 `POSTGRES_PASSWORD`，二者不是同一个环节。
