from pathlib import Path

from app.core.config import Settings, _find_project_root


def test_find_project_root_uses_monorepo_marker(tmp_path: Path) -> None:
    module_file = tmp_path / "services" / "api" / "app" / "core" / "config.py"
    module_file.parent.mkdir(parents=True)
    module_file.touch()
    (tmp_path / "pnpm-workspace.yaml").touch()

    assert _find_project_root(module_file) == tmp_path


def test_find_project_root_falls_back_to_api_root_in_image(tmp_path: Path) -> None:
    api_root = tmp_path / "app"
    module_file = api_root / "app" / "core" / "config.py"
    module_file.parent.mkdir(parents=True)
    module_file.touch()

    assert _find_project_root(module_file) == api_root


def test_weibo_browser_user_agent_is_configurable() -> None:
    user_agent = "Mozilla/5.0 deployment-session"

    configured = Settings(weibo_browser_user_agent=user_agent)

    assert configured.weibo_browser_user_agent == user_agent


def test_empty_weibo_cookie_file_disables_cookie_injection() -> None:
    configured = Settings(weibo_cookie_file="")

    assert configured.resolved_weibo_cookie_file is None
