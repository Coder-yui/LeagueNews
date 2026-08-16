# LeagueNews MCP 客户端接入与配置记录

> 记录本次将 LeagueNews 云端 MCP 接入 TRAE 的配置过程，说明后续如何修改 MCP 功能，以及如何让其他 agent 也能接入。
>
> 服务端设计细节见 [MCP.md](./MCP.md)。

## 1. 服务端现状（已上线）

- **协议**：MCP Streamable HTTP（官方 MCP Python SDK v2）
- **端点**：`https://leaguenews.me/mcp`
- **已注册工具（只读，共 6 个）**：

  | 工具 | 作用 |
  | --- | --- |
  | `search_news` | 按文本/产品/类型/主题/重要性/时间搜索已发布消息 |
  | `get_news_item` | 读一条完整消息（双语内容、公共媒体、来源、事件关联） |
  | `search_events` | 搜索聚合事件（生命周期/可信度/热度/重要性） |
  | `get_event` | 读单个事件详情 |
  | `get_daily_report` | 读某个 ISO 日期已发布的日报 |
  | `get_latest_daily_report` | 读最新已发布日报 |

- **鉴权**：`MCP_SERVICE_TOKEN`，默认请求头 `X-MCP-Service-Token`
- **生产限制**：`MCP_ALLOWED_HOSTS=leaguenews.me`（防 DNS rebinding）

### 1.1 生产环境变量（在服务器 `.env.production`）

```text
MCP_PRODUCTION=true
MCP_ENABLED=true
MCP_SERVICE_TOKEN=<从服务器 .env.production 读取，勿提交仓库>
MCP_AUTH_HEADER=X-MCP-Service-Token
MCP_ALLOWED_HOSTS=leaguenews.me
MCP_ALLOWED_ORIGINS=
```

> ⚠️ token 属于机密，不写入本文件。需要时到服务器 `~/LeagueNews/.env.production` 查看。

## 2. 本次 TRAE 本地配置

- 配置文件：`<项目根>/.trae/mcp.json`（**已被模型保护，需手动编辑**）
- `/Users/czh/Projects/LeagueNews/.trae/` 已加入 `.gitignore`，避免 token 入库

```json
{
  "mcpServers": {
    "leaguenews": {
      "url": "https://leaguenews.me/mcp",
      "headers": {
        "X-MCP-Service-Token": "<MCP_SERVICE_TOKEN>"
      }
    }
  }
}
```

启用步骤（TraeCode）：
1. 创建 `.trae/mcp.json`（内容如上）
2. 设置 → MCP → 打开 **Enable Project MCP** 开关
3. Reload Window（`Cmd+Shift+P` → `Developer: Reload Window`）

或用 UI 方式：设置 → MCP → Add → Add Manually，粘贴上述 JSON。

### 2.1 验证

```bash
# 握手（应返回 serverInfo: LeagueNews Intelligence）
curl -s -X POST 'https://leaguenews.me/mcp' \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'X-MCP-Service-Token: <TOKEN>' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"probe","version":"1.0"}}}'
```

完整协议流程：`initialize` → `notifications/initialized` → `tools/list` → `tools/call`。

## 3. 后续如何修改 MCP 功能

所有服务端逻辑在 `services/api/app/mcp/`：

| 文件 | 作用 |
| --- | --- |
| `server.py` | 注册 server、工具挂载、HTTP app 组装 |
| `http.py` | 服务 token 鉴权中间件 |
| `tools/news.py` | `search_news` / `get_news_item` |
| `tools/events.py` | `search_events` / `get_event` |
| `tools/reports.py` | `get_daily_report` / `get_latest_daily_report` |
| `tools/_common.py` | 每次调用短生命周期只读 DB session |

修改步骤：
1. 在对应 `tools/*.py` 的 `register()` 内用 `@mcp.tool(...)` 新增/修改工具；新增读逻辑时复用 `services/` 下的只读服务，**不要**触碰内部/写路径数据。
2. 运行检查：`services/api/.venv/bin/python -m ruff check services/api/app services/api/tests` 与 `pytest services/api/tests -q`（含 `test_mcp.py`）。
3. 重新构建并部署 API 镜像（`deploy/scripts/deploy.sh`），确保 `.env.production` 里 MCP 配置不变。
4. 客户端侧无需改动（工具列表由服务端自动下发）。

> 约束：MCP 保持**只读**。新增工具必须是纯读，否则需先评审安全边界（见 `docs/MCP.md`）。

## 4. 如何让其他 agent 接入

只要把 `url` + 鉴权 header 配置到目标 agent 的 MCP 客户端即可。**不同客户端配置路径不同，但 JSON 字段一致**：

```json
{
  "mcpServers": {
    "leaguenews": {
      "url": "https://leaguenews.me/mcp",
      "headers": {
        "X-MCP-Service-Token": "<MCP_SERVICE_TOKEN>"
      }
    }
  }
}
```

| 客户端 | 配置位置 |
| --- | --- |
| TraeCode | 项目级 `.trae/mcp.json`，或 设置→MCP→Add Manually |
| Claude Desktop | `~/Library/Application Support/Claude/claude_desktop_config.json`（macOS） |
| Cursor | 设置 → Features → MCP |
| Windsurf | 设置 → Cascade → MCP |
| VS Code (MCP 扩展) | 扩展的 MCP 配置 |

接入要点：
- **必须**带上 `X-MCP-Service-Token` header，否则返回 `401 authentication_required`。
- token 是唯一凭证，只发给信任的 agent/同事，不要写进会提交的仓库文件。
- 所有工具只读，不会触发采集、处理、聚合或日报生成，可安全放给 agent 使用。
- 若对外面向上游 agent 开放，建议在 `MCP_ALLOWED_HOSTS` 基础上，必要时再加反向代理/IP 白名单（当前仅单机部署，未做）。