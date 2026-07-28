"""Export the dedicated local Weibo browser session for production containers."""

import argparse
import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright

from app.core.config import settings


def main(output: Path) -> None:
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        context = playwright.chromium.launch_persistent_context(
            user_data_dir=str(settings.resolved_weibo_browser_profile),
            channel=settings.weibo_browser_channel or None,
            headless=settings.weibo_browser_headless,
            user_agent=settings.weibo_browser_user_agent or None,
            locale="zh-CN",
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            user_agent = page.evaluate("navigator.userAgent")
            cookies = context.cookies()
        finally:
            context.close()

    cookie_names = {str(cookie.get("name") or "") for cookie in cookies}
    if "SUB" not in cookie_names:
        raise SystemExit(
            "The dedicated Weibo profile does not contain an authenticated SUB cookie. "
            "Run scripts.setup_weibo_browser and log in before exporting."
        )

    output.write_text(
        json.dumps(cookies, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(output, 0o600)
    except OSError:
        pass
    print(f"Exported {len(cookies)} Weibo cookies to {output}")
    print(f"WEIBO_BROWSER_USER_AGENT={user_agent}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export the dedicated Weibo browser profile to Playwright cookie JSON"
    )
    parser.add_argument(
        "output",
        nargs="?",
        type=Path,
        default=settings.project_root / ".secrets" / "weibo-cookies.json",
    )
    arguments = parser.parse_args()
    main(arguments.output)
