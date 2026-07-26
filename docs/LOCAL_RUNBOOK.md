# LoL Daily Intel 本地运行手册

本文档用于日常启动、检查和关闭整个 LoL Daily Intel 项目。

## 服务与地址

| 服务 | 地址 | 用途 |
|---|---|---|
| Next.js | http://localhost:3000 | 日报和事件页面 |
| FastAPI Swagger | http://localhost:8000/docs | API 文档与手动导入 |
| FastAPI health | http://localhost:8000/api/v1/health | 后端健康检查 |
| pgAdmin | http://localhost:5050 | PostgreSQL 可视化管理 |
| PostgreSQL | localhost:5432 | 数据库 |

## 首次准备

只需要在首次运行或依赖发生变化时执行。

1. 启动 Docker Desktop，等待状态变为 Running。

2. 打开 PowerShell，进入项目根目录：

```powershell
Set-Location E:\leagueNews
```

3. 如果根目录还没有 `.env`：

```powershell
Copy-Item .env.example .env
```

检查 `.env` 至少包含：

```dotenv
DATABASE_URL=postgresql+psycopg://lol:lol_local_password@localhost:5432/lol_daily_intel
OPENAI_API_KEY=你的_Key
OPENAI_BASE_URL=https://api.deepseek.com
MODEL_NAME=deepseek-v4-flash
```

不要把 `.env` 提交到 Git。

4. 安装后端依赖：

```powershell
Set-Location E:\leagueNews\services\api
$env:UV_CACHE_DIR = "E:\leagueNews\.uv-cache"
uv sync --dev
```

5. 安装前端依赖：

```powershell
Set-Location E:\leagueNews
pnpm install
```

执行 `pnpm install` 时，前端开发服务器必须处于停止状态，否则 Windows 可能锁住 `node_modules`。

## 每天启动整个项目

### 推荐：一键启动

首次准备完成后，在项目根目录双击：

```text
start.cmd
```

也可以在 PowerShell 中运行：

```powershell
Set-Location E:\leagueNews
.\scripts\start.ps1
```

脚本会依次：

1. 检查 `.env`、uv、pnpm 和本地依赖；
2. 检查 Docker daemon；
3. 如果 daemon 未运行，尝试自动启动默认安装位置的 Docker Desktop，并等待就绪；
4. 启动 PostgreSQL、pgAdmin、FastAPI 和 Next.js；
5. 等待健康检查通过并打开网站。

因此通常不需要预先手动打开 Docker Desktop。如果 Docker Desktop 未安装在默认位置，
或自动启动后 120 秒内 daemon 仍未就绪，脚本会明确提示你手动启动。

后台日志保存在：

```text
E:\leagueNews\.run\logs
```

不希望自动打开浏览器时：

```powershell
.\scripts\start.ps1 -SkipBrowser
```

### 手动启动

需要三个 PowerShell 窗口。

### 窗口一：数据库和 pgAdmin

```powershell
Set-Location E:\leagueNews
docker compose up -d
docker compose ps
```

正常状态：

- `postgres` 显示 `healthy`。
- `pgadmin` 显示 `Up`。

### 窗口二：FastAPI 后端

```powershell
Set-Location E:\leagueNews\services\api
$env:UV_CACHE_DIR = "E:\leagueNews\.uv-cache"
uv run uvicorn app.main:app --reload
```

保持窗口运行。看到 `Uvicorn running on http://127.0.0.1:8000` 即表示启动成功。

### 窗口三：Next.js 前端

```powershell
Set-Location E:\leagueNews
pnpm dev:web
```

保持窗口运行。看到 `Local: http://localhost:3000` 即表示启动成功。

## 启动后检查

浏览器依次打开：

1. http://localhost:8000/api/v1/health
2. http://localhost:8000/docs
3. http://localhost:3000
4. http://localhost:5050

健康检查应返回：

```json
{"status": "ok"}
```

查看各层数据：

- Raw：`GET /api/v1/raw-items`
- 媒体：`GET /api/v1/media-assets`
- 标准化结果：`GET /api/v1/normalized-items`
- 事件：`GET /api/v1/events`
- 信源：`GET /api/v1/sources`

手动导入后 raw 默认保持 `pending`。需要处理时在 Swagger 执行：

```text
POST /api/v1/raw-items/{item_id}/process
```

## 正常退出

### 推荐：一键关闭

在项目根目录双击：

```text
stop.cmd
```

或执行：

```powershell
.\scripts\stop.ps1
```

脚本只终止由启动脚本记录的前后端进程，然后执行 `docker compose stop`。PostgreSQL
数据卷不会被删除。Docker Desktop 程序本身会保持运行，避免影响其他 Docker 项目；
不再使用时可以自行从系统托盘退出。

### 手动关闭

### 1. 停止前端

切换到运行 `pnpm dev:web` 的窗口，按：

```text
Ctrl + C
```

如果 PowerShell 询问是否终止批处理，输入 `Y`。

### 2. 停止后端

切换到运行 `uvicorn` 的窗口，按：

```text
Ctrl + C
```

### 3. 停止 Docker 服务

回到项目根目录执行：

```powershell
Set-Location E:\leagueNews
docker compose stop
```

`docker compose stop` 只停止容器，不删除数据库数据。

## 移除容器但保留数据

需要清理容器和网络时执行：

```powershell
Set-Location E:\leagueNews
docker compose down
```

命名卷 `postgres_data` 和 `pgadmin_data` 默认保留，下次 `docker compose up -d` 会继续使用原数据。

## 危险命令

不要随意执行：

```powershell
docker compose down -v
```

`-v` 会删除 PostgreSQL 和 pgAdmin 数据卷，业务数据会丢失。只有明确要重建空数据库时才能使用。

## 代码验证

### 后端

```powershell
Set-Location E:\leagueNews\services\api
$env:UV_CACHE_DIR = "E:\leagueNews\.uv-cache"
uv run ruff check app tests
uv run pytest -q
```

### 前端

先停止 `pnpm dev:web`，再执行：

```powershell
Set-Location E:\leagueNews
pnpm build:web
```

## `.env` 修改后如何生效

- 修改 LLM、API 或数据库连接配置后：停止并重新启动 FastAPI。
- 修改 Docker Compose 使用的环境变量后：

```powershell
Set-Location E:\leagueNews
docker compose up -d --force-recreate
```

- 修改 `NEXT_PUBLIC_*` 后：停止并重新启动 Next.js。

## 常见问题

### Docker daemon 无法连接

启动 Docker Desktop，然后检查：

```powershell
docker info
docker compose ps
```

### 8000 或 3000 端口被占用

通常表示上一次后端或前端仍在运行。回到对应 PowerShell 窗口按 `Ctrl + C`，不要重复启动多个实例。

### pgAdmin 网页登录密码

使用 `.env` 中：

```dotenv
PGADMIN_DEFAULT_EMAIL=...
PGADMIN_DEFAULT_PASSWORD=...
```

连接 `LoL Daily Intel` 数据库时要求的密码则是：

```dotenv
POSTGRES_PASSWORD=...
```

两者不是同一个登录环节。

### 没有配置 LLM Key

原始内容会保存在 `raw_items` 并保持 `pending`，API 返回明确错误；系统不会生成兜底 AI 结果。

## 推荐的日常顺序

```text
启动 Docker Desktop
→ docker compose up -d
→ 启动 FastAPI
→ 启动 Next.js
→ 开发或导入资讯
→ Ctrl+C 停前端
→ Ctrl+C 停后端
→ docker compose stop
```
