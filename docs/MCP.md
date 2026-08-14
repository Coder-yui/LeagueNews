# LeagueNews MCP

## Purpose

LeagueNews MCP is the agent-facing, read-only intelligence interface for data that LeagueNews has
already processed and published. It does not collect sources, classify messages, recompute scores,
aggregate events, generate reports, or mutate any database record.

## Architecture

```text
Published NormalizedItem / current public Event / published DailyReport
                              |
                       shared read services
                         /              \
                      REST              MCP
                                         |
                                       Agent
```

The MCP server is mounted in the existing FastAPI process and opens a short-lived SQLAlchemy read
session for each tool call. It never calls the LeagueNews REST API over HTTP.

The public boundary is:

- the current, published `NormalizedItem` projection;
- the current public `EventMention` projection, where the mention is published and its revision
  equals `NormalizedItem.current_revision`;
- an already-published `DailyReport` and its currently eligible published messages.

Private storage paths, review and pipeline state, raw LLM responses, internal checkpoints,
aggregation audit records, secrets, and administrative data are not exposed. Published media is
returned only through its public path or source URL.

## Tools

| Tool | Use |
| --- | --- |
| `search_news` | Search published messages by text, product, message type, topic, minimum importance, time, sort, and pagination. Returns compact triage projections. |
| `get_news_item` | Read one published message with its safe bilingual content, public media, source details, importance, and current event associations. |
| `search_events` | Search persisted aggregated events by text, product, category, family, lifecycle, credibility, importance, heat, time, sort, and pagination. |
| `get_event` | Read one event's current public card/detail projection, evidence, supporting messages, sources, and timeline. |
| `get_daily_report` | Read a published report for an ISO date. It never generates a missing report. |
| `get_latest_daily_report` | Read the newest published report, or return a clear not-found tool error when none exists. |

Search results are deliberately smaller than detail results. An agent should search first and then
call the corresponding detail tool when it needs evidence or full content.

## Security

MCP is read-only by construction: only the six read tools are registered, and the mounted endpoint
does not expose any REST write route. The transport can require a service token in a configurable
header:

- `MCP_SERVICE_TOKEN` is the secret value and must come from the environment;
- `MCP_AUTH_HEADER` defaults to `X-MCP-Service-Token`;
- when `MCP_AUTH_HEADER=Authorization`, the expected value is `Bearer <MCP_SERVICE_TOKEN>`;
- an empty token keeps local development convenient, but the production Compose file requires a
  non-empty `MCP_SERVICE_TOKEN`.

The SDK's DNS-rebinding protection is enabled for configured hosts. Production must set
`MCP_ALLOWED_HOSTS` to the public host (for example `news.example.com`); optional browser origins
are configured with `MCP_ALLOWED_ORIGINS`.

## Local usage

Install the API development dependencies, then start the existing backend:

```bash
cd services/api
uv sync --dev
MCP_SERVICE_TOKEN=local-secret uv run uvicorn app.main:app --reload --port 8000
```

The Streamable HTTP endpoint is `http://localhost:8000/mcp`. With the default header, configure an
MCP client with:

```text
X-MCP-Service-Token: local-secret
```

The development CLI can open the server in MCP Inspector:

```bash
cd services/api
MCP_SERVICE_TOKEN=local-secret uv run --group dev mcp dev app/mcp/server.py
```

For an in-process smoke test, the official SDK v2 client can connect directly to the registered
server object:

```python
from mcp import Client
from app.mcp.server import mcp_server

async with Client(mcp_server) as client:
    tools = await client.list_tools()
    result = await client.call_tool("search_news", {"limit": 5})
```

## Production

The public deployment keeps REST under `/api/*` and proxies `/mcp` (including the SDK's trailing
slash form) to the API container. Caddy does not route MCP to Next.js. Streamable HTTP is served by
the official MCP Python SDK with JSON responses and stateless sessions; Caddy's normal reverse
proxy preserves HTTP request/response streaming behavior.

Set these values in the deployment environment, using a secret manager or protected environment
file for the token:

```text
MCP_ENABLED=true
MCP_SERVICE_TOKEN=<long-random-secret>
MCP_AUTH_HEADER=X-MCP-Service-Token
MCP_ALLOWED_HOSTS=news.example.com
MCP_ALLOWED_ORIGINS=
```

The production environment example contains the same configuration names. Do not commit a real
token.
