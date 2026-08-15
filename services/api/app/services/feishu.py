from __future__ import annotations

import base64
import hashlib
import hmac
import time
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import settings


class FeishuDeliveryError(RuntimeError):
    """The Feishu webhook did not accept a notification."""


class FeishuBotClient:
    def __init__(
        self,
        *,
        webhook_url: str,
        secret: str = "",
        timeout_seconds: float = 15.0,
        client_factory: Any = httpx.AsyncClient,
    ) -> None:
        if not webhook_url.strip():
            raise ValueError("Feishu webhook URL is required")
        self.webhook_url = webhook_url
        self.secret = secret
        self.timeout_seconds = timeout_seconds
        self.client_factory = client_factory

    async def send(self, body: dict[str, Any]) -> None:
        payload = dict(body)
        if self.secret:
            timestamp = str(int(time.time()))
            payload["timestamp"] = timestamp
            payload["sign"] = self.signature(timestamp, self.secret)
        try:
            # Only the explicit deployment proxy is honored; ambient environment
            # variables must not change delivery behavior.
            async with self.client_factory(
                timeout=self.timeout_seconds,
                trust_env=False,
                proxy=settings.outbound_proxy_url or None,
            ) as client:
                response = await client.post(self.webhook_url, json=payload)
        except httpx.HTTPError as exc:
            raise FeishuDeliveryError(f"Feishu webhook request failed: {type(exc).__name__}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            raise FeishuDeliveryError(f"Feishu webhook returned HTTP {response.status_code}")
        try:
            result = response.json()
        except ValueError as exc:
            raise FeishuDeliveryError("Feishu webhook returned invalid JSON") from exc
        if not isinstance(result, dict):
            raise FeishuDeliveryError("Feishu webhook returned an invalid response")
        code = result.get("code", result.get("StatusCode", 0))
        if code not in (0, "0", None):
            message = str(result.get("msg") or result.get("StatusMessage") or "business error")
            raise FeishuDeliveryError(f"Feishu webhook business error code={code}: {message[:400]}")

    @staticmethod
    def signature(timestamp: str, secret: str) -> str:
        string_to_sign = f"{timestamp}\n{secret}".encode("utf-8")
        digest = hmac.new(string_to_sign, digestmod=hashlib.sha256).digest()
        return base64.b64encode(digest).decode()

    @property
    def endpoint_host(self) -> str:
        return urlsplit(self.webhook_url).hostname or "feishu"
