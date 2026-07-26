import asyncio

import httpx
import pytest

from app.services.connector_http import ConnectorHTTPClient, ConnectorHTTPError


class SequenceClient:
    def __init__(self, outcomes: list[httpx.Response | Exception]) -> None:
        self.outcomes = iter(outcomes)

    async def get(
        self, url: str, *, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        outcome = next(self.outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def response(status: int) -> httpx.Response:
    request = httpx.Request("GET", "https://example.com/news")
    return httpx.Response(status, request=request)


def test_connector_http_retries_429_then_succeeds() -> None:
    client = ConnectorHTTPClient(max_attempts=2)
    client._client = SequenceClient([response(429), response(200)])  # type: ignore[assignment]

    result = asyncio.run(client.get("https://example.com/news"))

    assert result.status_code == 200


def test_connector_http_turns_timeout_into_finite_error() -> None:
    request = httpx.Request("GET", "https://example.com/news")
    client = ConnectorHTTPClient(max_attempts=1)
    client._client = SequenceClient(  # type: ignore[assignment]
        [httpx.ReadTimeout("timed out", request=request)]
    )

    with pytest.raises(ConnectorHTTPError, match="failed after 1 attempts"):
        asyncio.run(client.get("https://example.com/news"))
