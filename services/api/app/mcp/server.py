from collections.abc import Awaitable, Callable

from mcp.server import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.exceptions import HTTPException
from starlette.types import Receive, Scope, Send

from app.core.config import settings
from app.mcp.http import MCPServiceTokenMiddleware
from app.mcp.tools import events, news, reports


def _csv(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


_allowed_hosts = _csv(settings.mcp_allowed_hosts)
_allowed_origins = _csv(settings.mcp_allowed_origins)
_transport_security = (
    TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_allowed_hosts,
        allowed_origins=_allowed_origins,
    )
    if _allowed_hosts or _allowed_origins
    else None
)


def create_mcp_server() -> MCPServer:
    server = MCPServer(
        name="LeagueNews",
        title="LeagueNews Intelligence",
        description="Read-only access to LeagueNews published messages, events, and daily reports.",
        instructions=(
            "Use search_news for individual published messages, search_events for aggregated "
            "developments, and the report tools for already-published daily reports. All tools "
            "are read-only and never trigger collection, processing, aggregation, or generation."
        ),
        version="0.1.0",
    )
    news.register(server)
    events.register(server)
    reports.register(server)
    return server


def create_mcp_http_app(server: MCPServer) -> Callable[..., Awaitable[None]]:
    return MCPServiceTokenMiddleware(
        server.streamable_http_app(
            streamable_http_path="/mcp",
            json_response=True,
            stateless_http=True,
            transport_security=_transport_security,
        ),
        header=settings.mcp_auth_header,
        token=settings.mcp_service_token,
    )


class MCPRuntime:
    """Own a server/app pair and recreate it if an ASGI lifespan restarts.

    MCP SDK v2 intentionally makes a Streamable HTTP session manager single-use.
    ASGI production processes have one lifespan, while TestClient and development
    reloaders can enter several lifespans in one Python process. Rebuilding the
    pair keeps both cases correct without bypassing the SDK lifecycle.
    """

    def __init__(self) -> None:
        self.server = create_mcp_server()
        self.app = create_mcp_http_app(self.server)
        self._has_run = False

    def prepare_for_lifespan(self) -> None:
        if self._has_run:
            self.server = create_mcp_server()
            self.app = create_mcp_http_app(self.server)
        self._has_run = True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope.get("path", "").rstrip("/") != "/mcp":
            # The host-level root mount is intentionally last in FastAPI's
            # route table. Preserve the host app's normal JSON 404 handler for
            # every path that is not the MCP endpoint.
            raise HTTPException(status_code=404)
        await self.app(scope, receive, send)


# This is the conventional object name used by `mcp dev app/mcp/server.py`.
# It is intentionally separate from the runtime-mounted server so in-process
# SDK tests do not consume the app's one-shot HTTP manager.
mcp_server = create_mcp_server()
mcp = mcp_server
mcp_runtime = MCPRuntime()
mcp_http_app = mcp_runtime
