import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.weibo_browser import (
    WEIBO_FETCH_TIMEOUT_MS,
    WeiboBrowserError,
    WeiboBrowserSession,
    _load_cookie_file,
)


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


def test_weibo_fetch_uses_abort_controller_deadline() -> None:
    captured: dict[str, object] = {}

    class FakePage:
        url = "https://weibo.com/"

        def evaluate(self, script: str, arg: object) -> dict[str, object]:
            assert isinstance(arg, dict)
            captured.update(script=script, arg=arg)
            return {"status": 200, "text": "{}"}

    session = object.__new__(WeiboBrowserSession)
    session._context = SimpleNamespace(pages=[FakePage()])

    assert session._get_json("https://weibo.com/ajax/test") == {}
    assert "AbortController" in captured["script"]
    assert "controller.abort()" in captured["script"]
    assert captured["arg"] == {
        "url": "https://weibo.com/ajax/test",
        "timeoutMs": WEIBO_FETCH_TIMEOUT_MS,
    }
