import hmac
import json
from collections.abc import Awaitable, Callable

from starlette.types import Receive, Scope, Send


class MCPServiceTokenMiddleware:
    """Protect the MCP transport with an optional configured service token.

    An empty token intentionally keeps localhost development friction-free. Production
    Compose requires MCP_SERVICE_TOKEN to be set before the API container starts.
    """

    def __init__(self, app: Callable[..., Awaitable[None]], *, header: str, token: str):
        self.app = app
        self.header = header.lower().encode("latin-1")
        self.header_name = header
        self.token = token

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.token or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return
        supplied = next(
            (value for name, value in scope.get("headers", []) if name.lower() == self.header),
            None,
        )
        expected = self.token
        if self.header_name.lower() == "authorization":
            expected = f"Bearer {expected}"
        if supplied is None or not hmac.compare_digest(supplied.decode("latin-1"), expected):
            body = json.dumps(
                {"error": "authentication_required", "message": "valid MCP service token required"}
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 401,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
