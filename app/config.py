"""Application configuration, loaded from APIRADAR_* environment variables."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="APIRADAR_", extra="ignore")

    app_name: str = "API Radar"
    api_base_url: str = "http://localhost:8000"

    database_url: str = "postgresql+psycopg://apiradar:apiradar@localhost:5432/apiradar"
    redis_url: str = "redis://localhost:6379/0"

    github_token: str | None = None
    github_app_id: str | None = None
    github_app_private_key: str | None = None
    github_api_url: str = "https://api.github.com"
    github_webhook_secret: str | None = None

    auto_pr: bool = False
    scan_interval_minutes: int = 360
    celery_task_always_eager: bool = False

    # Safety rails for repository-wide scans and generated PRs.
    max_repo_files: int = 4000
    max_file_bytes: int = 200_000
    max_patch_files: int = 25

    @property
    def is_github_app_configured(self) -> bool:
        return bool(self.github_app_id and self.github_app_private_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()