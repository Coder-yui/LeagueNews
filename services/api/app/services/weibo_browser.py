from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Playwright, sync_playwright

from app.core.config import settings


class WeiboBrowserError(RuntimeError):
    """A persistent browser session could not be started or queried."""


class WeiboBrowserSession:
    def __init__(
        self,
        *,
        profile_path: Path | None = None,
        channel: str | None = None,
        headless: bool | None = None,
    ) -> None:
        self.profile_path = profile_path or settings.resolved_weibo_browser_profile
        self.channel = channel or settings.weibo_browser_channel or None
        self.headless = settings.weibo_browser_headless if headless is None else headless
        self._playwright: Playwright | None = None
        self._context: BrowserContext | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="weibo-browser")

    async def __aenter__(self) -> WeiboBrowserSession:
        self.profile_path.mkdir(parents=True, exist_ok=True)
        try:
            await self._run(self._start)
        except Exception as exc:
            if self._playwright is not None:
                await self._run(self._playwright.stop)
                self._playwright = None
            self._executor.shutdown(wait=True)
            raise WeiboBrowserError(
                "Unable to start the dedicated Weibo browser profile; "
                "close any previous Weibo setup window and retry "
                f"({type(exc).__name__}: {exc})"
            ) from exc
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._context is not None:
            await self._run(self._context.close)
            self._context = None
        if self._playwright is not None:
            await self._run(self._playwright.stop)
            self._playwright = None
        self._executor.shutdown(wait=True)

    async def open_weibo(self, url: str = "https://weibo.com/") -> None:
        await self._run(self._open_weibo, url)

    async def get_json(self, url: str) -> dict[str, Any]:
        return await self._run(self._get_json, url)

    def _start(self) -> None:
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_path),
            channel=self.channel,
            headless=self.headless,
            locale="zh-CN",
            viewport={"width": 1440, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )

    def _open_weibo(self, url: str) -> None:
        page = self._page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    def _get_json(self, url: str) -> dict[str, Any]:
        page = self._page()
        if not page.url.startswith("https://weibo.com/"):
            page.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=60_000)
        result = page.evaluate(
            """
            async (url) => {
              const response = await fetch(url, {
                method: "GET",
                credentials: "include",
                headers: {
                  "Accept": "application/json, text/plain, */*",
                  "X-Requested-With": "XMLHttpRequest"
                }
              });
              return {
                status: response.status,
                contentType: response.headers.get("content-type") || "",
                text: await response.text()
              };
            }
            """,
            url,
        )
        if not isinstance(result, dict):
            raise WeiboBrowserError("Weibo browser returned an invalid response")
        status = int(result.get("status") or 0)
        text = str(result.get("text") or "")
        if status < 200 or status >= 300:
            raise WeiboBrowserError(f"Weibo browser request returned HTTP {status}")
        try:
            payload = json.loads(text)
        except (TypeError, ValueError) as exc:
            raise WeiboBrowserError("Weibo browser returned non-JSON data") from exc
        if not isinstance(payload, dict):
            raise WeiboBrowserError("Weibo browser returned a non-object JSON response")
        return payload

    def _page(self):
        if self._context is None:
            raise WeiboBrowserError("Weibo browser session is not open")
        pages = self._context.pages
        return pages[0] if pages else self._context.new_page()

    async def _run(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, func, *args)
