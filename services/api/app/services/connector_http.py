import asyncio
from urllib.parse import urlsplit

import httpx

from app.core.config import settings


class ConnectorHTTPError(RuntimeError):
    """A connector HTTP request failed after finite retries."""


class ConnectorHTTPClient:
    def __init__(self, *, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            trust_env=False,
            proxy=settings.outbound_proxy_url or None,
            timeout=httpx.Timeout(30, connect=10),
            headers={"User-Agent": settings.connector_user_agent},
        )

    async def __aenter__(self) -> "ConnectorHTTPClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        follow_redirects: bool = True,
    ) -> httpx.Response:
        last_error = "request did not run"
        for attempt in range(1, self.max_attempts + 1):
            try:
                request_options: dict[str, object] = {"headers": headers}
                if not follow_redirects:
                    request_options["follow_redirects"] = False
                response = await self._client.get(url, **request_options)
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return response
                last_error = f"HTTP {response.status_code}"
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = type(exc).__name__
            if attempt < self.max_attempts:
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        parsed = urlsplit(url)
        safe_target = f"{parsed.scheme}://{parsed.hostname or 'unknown'}{parsed.path}"
        raise ConnectorHTTPError(
            f"GET {safe_target} failed after {self.max_attempts} attempts: "
            f"{last_error}"
        )
