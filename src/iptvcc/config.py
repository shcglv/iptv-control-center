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

    # Where `python -m iptvcc` listens. The container entrypoint passes its own host and port to
    # uvicorn directly, so these matter for the documented launcher and for drills.
    host: str = "127.0.0.1"
    port: int = 8000

    # 32 raw bytes, base64-encoded. Empty in development: a well-known dev key is used
    # and a warning is logged once. Production MUST set it.
    encryption_key: str = ""

    display_timezone: str = "Europe/Moscow"

    # SSRF policy. Private targets are refused by default; enable deliberately for LAN sources.
    allow_private_network_targets: bool = False

    # Public delivery endpoints (/p/...). Per token and client address, sliding 60 s window.
    # Set to 0 to disable (a player fetches the playlist once, then one resolve per zap).
    public_rate_limit_per_minute: int = 120

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
    # Probe depth: passive reads 2 KB over HTTP; active also runs ffprobe on the stream.
    probe_mode: str = "passive"
    probe_concurrency: int = 2
    # A single provider should not receive every concurrent probe in a batch.
    probe_host_concurrency: int = 2
    ffprobe_path: str = "ffprobe"
    ffprobe_timeout_seconds: int = 12

    # Smart-TV guard: guides larger than this are re-rendered with a narrower window.
    # 0 disables the cap.
    epg_max_bytes: int = 5000000

    # Telegram bot. Without a token the bot is disabled; polling needs no inbound port.
    telegram_bot_token: str = ""
    telegram_enabled: bool = True
    telegram_owner_id: int | None = None
    telegram_poll_timeout: int = 25
    telegram_proxy: str = ""
    telegram_api_base: str = "https://api.telegram.org"
    telegram_notify_interval_seconds: int = 300
    telegram_dedupe_hours: int = 6

    # Interactive API documentation. Left unset it follows the environment: on in development, off
    # in production, where the schema is not something an unauthenticated visitor should read.
    enable_docs: bool | None = None

    # Requests larger than this are refused before the body is buffered. Playlists and guides are
    # uploaded as JSON, and 50 MB of playlist is already far beyond a realistic provider export.
    max_request_bytes: int = 64 * 1024 * 1024

    # A rendered playlist is cached per profile, device family and base URL for this long, so a
    # household switching several televisions on at once renders once. 0 disables the cache.
    playlist_cache_seconds: int = 30

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"production", "prod"}

    @property
    def docs_enabled(self) -> bool:
        if self.enable_docs is not None:
            return self.enable_docs
        return not self.is_production


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
