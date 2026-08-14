from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from playwright.sync_api import BrowserContext, Playwright, sync_playwright

from app.core.config import settings


WEIBO_FETCH_TIMEOUT_MS = 45_000


class WeiboBrowserError(RuntimeError):
    """A persistent browser session could not be started or queried."""


def _load_cookie_file(path: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise WeiboBrowserError(f"Weibo cookie file cannot be read: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WeiboBrowserError(f"Weibo cookie file is invalid JSON: {path}") from exc
    if not isinstance(payload, list) or not all(
        isinstance(cookie, dict)
        and cookie.get("name")
        and cookie.get("value")
        and cookie.get("domain")
        and cookie.get("path")
        for cookie in payload
    ):
        raise WeiboBrowserError(
            "Weibo cookie file must contain a list of browser cookies "
            "with name, value, domain, and path"
        )
    return payload


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
        try:
            if self._context is not None:
                await self._run(self._context.close)
        finally:
            self._context = None
            try:
                if self._playwright is not None:
                    await self._run(self._playwright.stop)
            finally:
                self._playwright = None
                self._executor.shutdown(wait=True)

    async def open_weibo(self, url: str = "https://weibo.com/") -> None:
        await self._run(self._open_weibo, url)

    async def get_json(self, url: str) -> dict[str, Any]:
        return await self._run(self._get_json, url)

    async def has_cookie(self, name: str) -> bool:
        return await self._run(self._has_cookie, name)

    async def save_cookies(self, path: Path) -> int:
        return await self._run(self._save_cookies, path)

    def _start(self) -> None:
        self._playwright = sync_playwright().start()
        self._context = self._playwright.chromium.launch_persistent_context(
            user_data_dir=str(self.profile_path),
            channel=self.channel,
            headless=self.headless,
            user_agent=settings.weibo_browser_user_agent or None,
            locale="zh-CN",
            viewport={"width": 1440, "height": 1000},
            args=["--disable-blink-features=AutomationControlled"],
        )
        cookie_file = settings.resolved_weibo_cookie_file
        if cookie_file is None:
            local_default = settings.project_root / ".secrets" / "weibo-cookies.json"
            cookie_file = local_default if local_default.is_file() else None
        if cookie_file is not None:
            self._context.add_cookies(_load_cookie_file(cookie_file))

    def _open_weibo(self, url: str) -> None:
        page = self._page()
        page.goto(url, wait_until="domcontentloaded", timeout=60_000)

    def _get_json(self, url: str) -> dict[str, Any]:
        page = self._page()
        if not page.url.startswith("https://weibo.com/"):
            page.goto("https://weibo.com/", wait_until="domcontentloaded", timeout=60_000)
        try:
            result = page.evaluate(
                """
                async ({url, timeoutMs}) => {
                  const controller = new AbortController();
                  const timeout = setTimeout(() => controller.abort(), timeoutMs);
                  try {
                    const response = await fetch(url, {
                      method: "GET",
                      credentials: "include",
                      signal: controller.signal,
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
                  } finally {
                    clearTimeout(timeout);
                  }
                }
                """,
                {
                    "url": url,
                    "timeoutMs": WEIBO_FETCH_TIMEOUT_MS,
                },
            )
        except Exception as exc:
            raise WeiboBrowserError(
                f"Weibo browser request failed from {page.url} "
                f"({type(exc).__name__}: {exc})"
            ) from exc
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

    def _has_cookie(self, name: str) -> bool:
        if self._context is None:
            raise WeiboBrowserError("Weibo browser session is not open")
        return any(
            cookie.get("name") == name and bool(cookie.get("value"))
            for cookie in self._context.cookies("https://weibo.com/")
        )

    def _save_cookies(self, path: Path) -> int:
        if self._context is None:
            raise WeiboBrowserError("Weibo browser session is not open")
        cookies = self._context.cookies()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(cookies, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return len(cookies)

    def _page(self):
        if self._context is None:
            raise WeiboBrowserError("Weibo browser session is not open")
        pages = self._context.pages
        return pages[0] if pages else self._context.new_page()

    async def _run(self, func, *args):
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, func, *args)
