from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_project_root(module_file: Path) -> Path:
    resolved = module_file.resolve()
    for parent in resolved.parents:
        if (parent / "pnpm-workspace.yaml").is_file():
            return parent
    # Production images contain only the API project under /app, without the
    # monorepo marker. app/core/config.py is two directories below that root.
    return resolved.parents[2]


class Settings(BaseSettings):
    app_name: str = "LoL Daily Intel API"
    api_v1_prefix: str = "/api/v1"
    api_docs_enabled: bool = True
    database_url: str = "postgresql+psycopg://lol:lol_local_password@localhost:5432/lol_daily_intel"
    api_cors_origins: str = "http://localhost:3000"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    model_name: str = "gpt-4.1-mini"
    media_root: str = "../../apps/web/public/media"
    media_max_bytes: int = 20 * 1024 * 1024
    connector_user_agent: str = "LoLDailyIntel/0.1 (local development)"
    x_cookie_file: str = ".secrets/x-cookies.json"
    x_fetch_limit: int = 10
    weibo_browser_profile: str = ".secrets/weibo-browser-profile"
    weibo_cookie_file: str = ""
    weibo_browser_channel: str = "msedge"
    weibo_browser_headless: bool = True
    weibo_browser_user_agent: str = ""
    pipeline_automation_enabled: bool = True
    pipeline_worker_poll_seconds: float = 2.0
    pipeline_worker_lease_seconds: int = 300
    pipeline_worker_heartbeat_seconds: int = 30
    collection_scheduler_poll_seconds: float = 5.0
    collection_scheduler_lease_minutes: int = 30
    collection_scheduler_heartbeat_seconds: int = 60

    model_config = SettingsConfigDict(env_file=("../../.env", ".env"), extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [origin.strip() for origin in self.api_cors_origins.split(",") if origin.strip()]

    @property
    def resolved_media_root(self) -> Path:
        path = Path(self.media_root)
        if path.is_absolute():
            return path
        api_root = Path(__file__).resolve().parents[2]
        return (api_root / path).resolve()

    @property
    def project_root(self) -> Path:
        return _find_project_root(Path(__file__))

    def _resolve_project_path(self, value: str) -> Path:
        path = Path(value)
        return path.resolve() if path.is_absolute() else (self.project_root / path).resolve()

    @property
    def resolved_x_cookie_file(self) -> Path:
        return self._resolve_project_path(self.x_cookie_file)

    @property
    def resolved_weibo_browser_profile(self) -> Path:
        return self._resolve_project_path(self.weibo_browser_profile)

    @property
    def resolved_weibo_cookie_file(self) -> Path | None:
        return (
            self._resolve_project_path(self.weibo_cookie_file)
            if self.weibo_cookie_file.strip()
            else None
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
