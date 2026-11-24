"""Application settings loaded from the environment (prefix ``IPTV_``)."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Values are never committed; see ``.env.example``."""

    model_config = SettingsConfigDict(env_prefix="IPTV_", env_file=".env", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite+aiosqlite:///./data/iptv.db"
    public_base_url: str = "http://127.0.0.1:8000"

    # 32 raw bytes, base64-encoded. Empty in development: a well-known dev key is used
    # and a warning is logged once. Production MUST set it.
    encryption_key: str = ""

    display_timezone: str = "Europe/Moscow"

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
