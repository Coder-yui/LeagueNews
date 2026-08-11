# LoL Daily Intel 本地运行手册

## 服务

| 服务 | 地址 | 用途 |
| --- | --- | --- |
| Next.js | http://localhost:3000 | 已发布消息列表与详情 |
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

macOS/Linux（需 uv、pnpm，以及 OrbStack 或 Docker Desktop 提供 Docker）：

```bash
cd /path/to/leagueNews
cp .env.example .env

cd services/api
uv sync --dev

cd ../..
pnpm install
```

在 `.env` 配置数据库和 OpenAI-compatible LLM。不要提交 `.env`、`.secrets` 或
`.run`。没有 LLM Key 时仍可采集入库，但不能进入 AI 审核流程。

## 启动

推荐在项目根目录运行（Windows）：

```powershell
.\scripts\start.ps1
```

macOS/Linux：

```bash
./scripts/start.sh
```

脚本检查依赖和 Docker daemon，启动 PostgreSQL、pgAdmin，使用与生产环境相同的迁移入口
应用全部待执行迁移，然后启动 FastAPI、Next.js、Pipeline Worker 和采集调度器。无需打开
浏览器时（Windows 用 `-SkipBrowser`，macOS/Linux 用 `--skip-browser`）：

```powershell
.\scripts\start.ps1 -SkipBrowser
```

```bash
./scripts/start.sh --skip-browser
```

后台日志位于项目根目录 `.run/logs`。

排障时可以分别启动基础服务，但还需要自行启动迁移、Pipeline Worker 和采集调度器；
日常开发优先使用 `scripts/start.ps1`（Windows）或 `scripts/start.sh`（macOS/Linux）：

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

新采集或手工导入的 RawItem 默认创建持久化 `pipeline_job`，由 Worker 自动完成消息处理与
发布。`POST /api/v1/raw-items/{id}/process` 是显式人工审核入口，不会直接发布消息。
已发布结果有误时，从管理台选择撤回阶段和后续人工/自动模式。

## 关闭

```powershell
Set-Location E:\leagueNews
.\scripts\stop.ps1
```

macOS/Linux：

```bash
./scripts/stop.sh
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

macOS/Linux：

```bash
cd services/api
.venv/bin/python -m ruff check app scripts tests
.venv/bin/python -m pytest -q
```

前端：

```powershell
Set-Location E:\leagueNews
pnpm lint:web
pnpm build:web
```

执行 `pnpm install` 或 production build 前，最好先停止前端开发服务器，避免 Windows
锁住 `node_modules` 或 `.next` 文件。

## 清理前的资源测量

2026-08-03 的本地只读基线（`docker image ls`、`docker system df`）为：镜像共
1.314 GB、活跃本地卷 122.7 MB、build cache 为 0；PostgreSQL 17 Alpine 为 416 MB，
Caddy Alpine 约 71–82 MB。本机当时没有应用镜像，API/Chromium/OCR 分层大小需在下次
拉取 CI 镜像后再测量。API、Worker、Scheduler 当前引用同一个 API image；Docker 会共享
相同只读层，三个容器不会让镜像磁盘占用变成三倍。

清理前必须分别测量 PostgreSQL、媒体卷、容器日志、旧 image tag 和 build cache。不要把
破坏性 prune 当作例行维护。任何媒体保留工具都必须先证明候选文件没有被 RawItem、
NormalizedItem、revision、checkpoint 或公开路径引用。Collector/OCR 是否拆镜像，
等实际分层证明有明确安全或资源收益后再决定。

## 常见问题

- Docker 无法连接：启动 Docker Desktop（macOS 上也可以是 OrbStack），再运行
  `docker info` 和 `docker compose ps`。`start.sh` 会尝试自动启动 OrbStack 或
  Docker Desktop。
- macOS 上 `uv sync` 或 Python 报 `No module named 'encodings'`：当前 shell 导出了
  指向其他 Python 的 `PYTHONHOME`/`PYTHONPATH`（常见于 IDE 内置终端）。先执行
  `unset PYTHONHOME PYTHONPATH` 再重试；`start.sh` 已自动清除这两个变量。
- macOS 主目录下的 `OrbStack/` 文件夹不是应用程序，而是 OrbStack 的 Docker 虚拟机
  数据目录（镜像和容器都在里面），应用本体在 `/Applications/OrbStack.app`。
- 3000/8000 被占用：先停止上一次启动的对应进程，不要重复启动实例。
- LLM 返回 503：检查 `OPENAI_API_KEY`、`OPENAI_BASE_URL`、`MODEL_NAME`，重启后端。
- pgAdmin 登录使用 `PGADMIN_DEFAULT_EMAIL` 和 `PGADMIN_DEFAULT_PASSWORD`；连接数据库
  使用 `POSTGRES_PASSWORD`，二者不是同一个环节。
