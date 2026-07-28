import json
from pathlib import Path

import pytest

from app.services.weibo_browser import WeiboBrowserError, _load_cookie_file


def test_load_cookie_file_accepts_playwright_cookie_export(tmp_path: Path) -> None:
    cookie_file = tmp_path / "weibo-cookies.json"
    cookies = [
        {
            "name": "SUB",
            "value": "session",
            "domain": ".weibo.com",
            "path": "/",
            "secure": True,
        }
    ]
    cookie_file.write_text(json.dumps(cookies), encoding="utf-8")

    assert _load_cookie_file(cookie_file) == cookies


def test_load_cookie_file_rejects_non_cookie_payload(tmp_path: Path) -> None:
    cookie_file = tmp_path / "weibo-cookies.json"
    cookie_file.write_text("{}", encoding="utf-8")

    with pytest.raises(WeiboBrowserError, match="must contain a list"):
        _load_cookie_file(cookie_file)
