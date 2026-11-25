"""Pre-deployment checks.

Run before exposing an instance: a wrong encryption key or an unmigrated database must be found by
a person, not by the first device that tries to play something.
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from .config import Settings
from .secrets import is_development_key

TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,}:[A-Za-z0-9_-]{30,}$")

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: str
    detail: str


def check_encryption_key(settings: Settings) -> CheckResult:
    raw = settings.encryption_key
    if not raw:
        return CheckResult("encryption key", FAIL, "IPTV_ENCRYPTION_KEY is not set")
    if is_development_key(raw):
        return CheckResult(
            "encryption key", FAIL, "the public development key is in use; generate a random one"
        )
    try:
        decoded = base64.b64decode(raw, validate=True)
    except Exception:
        return CheckResult("encryption key", FAIL, "value is not valid base64")
    if len(decoded) != 32:
        return CheckResult("encryption key", FAIL, f"decodes to {len(decoded)} bytes, expected 32")
    return CheckResult("encryption key", OK, "32 bytes, unique")


def check_public_base_url(settings: Settings) -> CheckResult:
    raw = settings.public_base_url
    if not raw:
        return CheckResult("public base URL", FAIL, "IPTV_PUBLIC_BASE_URL is empty")
    parts = urlsplit(raw)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return CheckResult("public base URL", FAIL, f"not an absolute URL: {raw}")
    if settings.is_production and parts.scheme != "https":
        return CheckResult("public base URL", WARN, "HTTPS is expected in production")
    if settings.is_production and parts.hostname in {"localhost", "127.0.0.1", "::1"}:
        return CheckResult(
            "public base URL", WARN, "localhost in production: devices cannot reach it"
        )
    return CheckResult("public base URL", OK, raw)


def check_timezone(settings: Settings) -> CheckResult:
    try:
        ZoneInfo(settings.display_timezone)
    except Exception:
        return CheckResult("display timezone", FAIL, f"unknown zone: {settings.display_timezone}")
    return CheckResult("display timezone", OK, settings.display_timezone)


def check_telegram(settings: Settings) -> CheckResult:
    if not settings.telegram_bot_token:
        return CheckResult("telegram", OK, "disabled (no token)")
    if not TELEGRAM_TOKEN_RE.match(settings.telegram_bot_token):
        return CheckResult("telegram", FAIL, "token does not look like <digits>:<secret>")
    if settings.telegram_owner_id is None:
        return CheckResult("telegram", WARN, "token set but IPTV_TELEGRAM_OWNER_ID is empty")
    return CheckResult("telegram", OK, "configured")


def check_rate_limit(settings: Settings) -> CheckResult:
    if settings.public_rate_limit_per_minute <= 0:
        return CheckResult("rate limit", WARN, "public endpoint limiting is disabled")
    return CheckResult("rate limit", OK, f"{settings.public_rate_limit_per_minute}/min")


def check_artifacts_dir(settings: Settings) -> CheckResult:
    path = Path(settings.artifacts_dir)
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".preflight"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as exc:
        return CheckResult("artifacts dir", FAIL, f"{path}: {exc}")
    return CheckResult("artifacts dir", OK, str(path))


async def check_database(database_url: str) -> CheckResult:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        return CheckResult("database", FAIL, str(exc).splitlines()[0][:200])
    finally:
        await engine.dispose()
    return CheckResult("database", OK, database_url.split("@")[-1])


async def check_migrations(database_url: str, alembic_ini: Path | None = None) -> CheckResult:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    ini = alembic_ini or Path("alembic.ini")
    if not ini.is_file():
        return CheckResult("migrations", WARN, "alembic.ini not found; cannot compare revisions")
    try:
        head = ScriptDirectory.from_config(Config(str(ini))).get_current_head()
    except Exception as exc:
        return CheckResult("migrations", WARN, f"cannot read revisions: {exc}")

    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT version_num FROM alembic_version"))
            current = result.scalar_one_or_none()
    except Exception as exc:
        return CheckResult("migrations", FAIL, f"cannot read alembic_version: {str(exc)[:160]}")
    finally:
        await engine.dispose()

    if current is None:
        return CheckResult("migrations", FAIL, "database has no applied migration")
    if current != head:
        return CheckResult("migrations", FAIL, f"database at {current}, head is {head}")
    return CheckResult("migrations", OK, str(head))


async def run_preflight(
    settings: Settings, *, alembic_ini: Path | None = None, database_url: str | None = None
) -> list[CheckResult]:
    checks = [
        check_encryption_key(settings),
        check_public_base_url(settings),
        check_timezone(settings),
        check_telegram(settings),
        check_rate_limit(settings),
        check_artifacts_dir(settings),
        await check_database(database_url or settings.database_url),
        await check_migrations(database_url or settings.database_url, alembic_ini),
    ]
    return checks


def has_failures(results: list[CheckResult]) -> bool:
    return any(result.status == FAIL for result in results)
