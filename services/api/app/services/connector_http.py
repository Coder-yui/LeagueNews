import asyncio

import httpx

from app.core.config import settings


class ConnectorHTTPError(RuntimeError):
    """A connector HTTP request failed after finite retries."""


class ConnectorHTTPClient:
    def __init__(self, *, max_attempts: int = 3) -> None:
        self.max_attempts = max_attempts
        self._client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30, connect=10),
            headers={"User-Agent": settings.connector_user_agent},
        )

    async def __aenter__(self) -> "ConnectorHTTPClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self._client.aclose()

    async def get(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        last_error = "request did not run"
        for attempt in range(1, self.max_attempts + 1):
            try:
                response = await self._client.get(url, headers=headers)
                if response.status_code != 429 and response.status_code < 500:
                    response.raise_for_status()
                    return response
                last_error = f"HTTP {response.status_code}"
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_error = str(exc)
            if attempt < self.max_attempts:
                await asyncio.sleep(0.5 * 2 ** (attempt - 1))
        raise ConnectorHTTPError(f"GET {url} failed after {self.max_attempts} attempts: {last_error}")
