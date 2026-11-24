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

    # Rendered playlists/guides and other generated files.
    artifacts_dir: str = "./data/artifacts"

    # Probe scheduler. Disable in tests and when probes must not run (e.g. provider slot limits).
    scheduler_enabled: bool = True
    scheduler_tick_seconds: int = 60
    scheduler_batch_size: int = 25
    scheduler_lock_ttl_seconds: int = 180
    # Channels served in the last 24 h are "hot": checked often. Everything else: hourly.
    scheduler_hot_interval_seconds: int = 300
    scheduler_cold_interval_seconds: int = 3600

    # Telegram bot. Without a token the bot is disabled; polling needs no inbound port.
    telegram_bot_token: str = ""
    telegram_enabled: bool = True
    telegram_owner_id: int | None = None
    telegram_poll_timeout: int = 25
    telegram_proxy: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    telegram_notify_interval_seconds: int = 300
    telegram_dedupe_hours: int = 6

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
