"""Pre-deployment checks must be able to fail."""

from __future__ import annotations

import base64
import hashlib
import sqlite3
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from iptvcc.config import get_settings
from iptvcc.preflight import (
    FAIL,
    OK,
    WARN,
    check_artifacts_dir,
    check_database,
    check_encryption_key,
    check_migrations,
    check_public_base_url,
    check_telegram,
    check_timezone,
    has_failures,
    run_preflight,
)

ROOT = Path(__file__).resolve().parent.parent
DEV_KEY = base64.b64encode(
    hashlib.sha256(b"iptv-control-center-dev-key-do-not-use-in-production").digest()
).decode()


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


# --- encryption key ----------------------------------------------------------------------------


def test_valid_key_passes() -> None:
    result = check_encryption_key(get_settings())
    assert result.status == OK, result.detail


def test_missing_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "encryption_key", "", raising=False)
    assert check_encryption_key(get_settings()).status == FAIL


def test_development_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "encryption_key", DEV_KEY, raising=False)
    result = check_encryption_key(get_settings())
    assert result.status == FAIL
    assert "development key" in result.detail


def test_short_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        get_settings(), "encryption_key", base64.b64encode(b"short").decode(), raising=False
    )
    assert check_encryption_key(get_settings()).status == FAIL


def test_non_base64_key_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "encryption_key", "not-base64!!", raising=False)
    assert check_encryption_key(get_settings()).status == FAIL


# --- URL, timezone, telegram, artifacts ---------------------------------------------------------


def test_public_url_https_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "public_base_url", "https://iptv.example", raising=False)
    assert check_public_base_url(get_settings()).status == OK


def test_public_url_must_be_absolute(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "public_base_url", "iptv.example", raising=False)
    assert check_public_base_url(get_settings()).status == FAIL


def test_localhost_in_production_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "public_base_url", "http://127.0.0.1:8000", raising=False)
    monkeypatch.setattr(settings, "app_env", "production", raising=False)
    result = check_public_base_url(settings)
    assert result.status == WARN


def test_unknown_timezone_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "display_timezone", "Mars/Olympus", raising=False)
    assert check_timezone(get_settings()).status == FAIL


def test_malformed_telegram_token_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "telegram_bot_token", "not-a-token", raising=False)
    assert check_telegram(get_settings()).status == FAIL


def test_telegram_without_owner_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_bot_token", "123456:" + "a" * 35, raising=False)
    monkeypatch.setattr(settings, "telegram_owner_id", None, raising=False)
    assert check_telegram(settings).status == WARN


def test_artifacts_dir_must_be_writable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(get_settings(), "artifacts_dir", str(blocker / "sub"), raising=False)
    assert check_artifacts_dir(get_settings()).status == FAIL


# --- database and migrations -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_database_connectivity(tmp_path: Path) -> None:
    url = _sqlite_url(tmp_path / "ok.db")
    assert (await check_database(url)).status == OK

    broken = await check_database("sqlite+aiosqlite:///Z:/definitely/missing/dir/x.db")
    assert broken.status == FAIL


@pytest.mark.asyncio
async def test_migrations_fail_without_a_version_table(tmp_path: Path) -> None:
    database = tmp_path / "empty.db"
    sqlite3.connect(database).close()
    result = await check_migrations(_sqlite_url(database), ROOT / "alembic.ini")
    assert result.status == FAIL


@pytest.mark.asyncio
async def test_migrations_fail_when_behind(tmp_path: Path) -> None:
    database = tmp_path / "behind.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    connection.execute("INSERT INTO alembic_version VALUES ('0ac4c0e50565')")
    connection.commit()
    connection.close()

    result = await check_migrations(_sqlite_url(database), ROOT / "alembic.ini")
    assert result.status == FAIL
    assert "head is" in result.detail


@pytest.mark.asyncio
async def test_migrations_pass_at_head(tmp_path: Path) -> None:
    head = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini"))).get_current_head()
    database = tmp_path / "head.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
    connection.execute("INSERT INTO alembic_version VALUES (?)", (head,))
    connection.commit()
    connection.close()

    result = await check_migrations(_sqlite_url(database), ROOT / "alembic.ini")
    assert result.status == OK, result.detail
    assert result.detail == head


@pytest.mark.asyncio
async def test_full_run_reports_failures(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "encryption_key", "", raising=False)
    monkeypatch.setattr(settings, "artifacts_dir", str(tmp_path / "artifacts"), raising=False)
    results = await run_preflight(
        settings,
        alembic_ini=ROOT / "alembic.ini",
        database_url=_sqlite_url(tmp_path / "p.db"),
    )
    assert has_failures(results)
    names = {result.name for result in results}
    assert {"encryption key", "migrations", "database"} <= names
