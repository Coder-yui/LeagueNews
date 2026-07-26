from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from app.core.config import settings
from app.services.weibo_browser import WeiboBrowserSession


async def setup(uid: str, status_file: Path | None) -> int:
    _write_status(status_file, {"status": "starting"})
    async with WeiboBrowserSession(headless=False) as browser:
        await browser.open_weibo(f"https://weibo.com/u/{uid}")
        _write_status(
            status_file,
            {
                "status": "waiting_for_login",
                "message": "请在 Edge 窗口中完成微博登录；验证成功后窗口会自动关闭。",
            },
        )
        for _ in range(120):
            try:
                payload = await browser.get_json(
                    "https://weibo.com/ajax/statuses/searchProfile?"
                    f"uid={uid}&page=1&hasori=1&hastext=1&haspic=1&"
                    "hasvideo=1&hasmusic=1&hasret=1"
                )
                if payload.get("ok") == 1:
                    count = len((payload.get("data") or {}).get("list") or [])
                    _write_status(
                        status_file,
                        {"status": "authenticated", "sample_count": count},
                    )
                    await asyncio.sleep(1)
                    return 0
            except Exception:
                pass
            await asyncio.sleep(5)
    _write_status(
        status_file,
        {"status": "timeout", "message": "十分钟内未检测到有效微博登录。"},
    )
    return 1


def _write_status(path: Path | None, payload: dict[str, object]) -> None:
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Open a dedicated Edge profile and persist a Weibo login."
    )
    parser.add_argument("--uid", default="5756404150")
    parser.add_argument("--status-file", type=Path)
    args = parser.parse_args()
    print(f"Weibo profile: {settings.resolved_weibo_browser_profile}", flush=True)
    return asyncio.run(setup(args.uid, args.status_file))


if __name__ == "__main__":
    raise SystemExit(main())
