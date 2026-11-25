"""Backup and restore drill.

The point is not that a file appears, but that the file can be opened, passes integrity checks and
contains the rows the application wrote.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from iptvcc.backup import BackupError, backup_sqlite, create_backup, verify_sqlite_backup


def _make_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE channel (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE source (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
            CREATE TABLE stream (id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL, url TEXT);
            """
        )
        connection.executemany(
            "INSERT INTO channel (name) VALUES (?)", [(f"Channel {index}",) for index in range(37)]
        )
        connection.executemany(
            "INSERT INTO source (name) VALUES (?)", [("Provider A",), ("Provider B",)]
        )
        connection.executemany(
            "INSERT INTO stream (source_id, url) VALUES (?, ?)",
            [(1, f"http://p.test/{index}.ts") for index in range(120)],
        )
        connection.commit()


@pytest.fixture
def database(tmp_path: Path) -> tuple[Path, str]:
    path = tmp_path / "iptv.db"
    _make_database(path)
    return path, f"sqlite+aiosqlite:///{path.as_posix()}"


def test_sqlite_backup_copies_every_row(tmp_path: Path, database: tuple[Path, str]) -> None:
    _path, url = database
    destination = tmp_path / "backup.db"

    result = backup_sqlite(url, destination)

    assert destination.is_file()
    assert result.engine == "sqlite"
    assert result.integrity == "ok"
    assert result.tables == {"channel": 37, "source": 2, "stream": 120}
    assert result.size_bytes > 0


def test_backup_refuses_to_overwrite(tmp_path: Path, database: tuple[Path, str]) -> None:
    _path, url = database
    destination = tmp_path / "backup.db"
    backup_sqlite(url, destination)
    with pytest.raises(BackupError, match="overwrite"):
        backup_sqlite(url, destination)


def test_backup_of_a_missing_database_fails(tmp_path: Path) -> None:
    with pytest.raises(BackupError, match="does not exist"):
        backup_sqlite(f"sqlite+aiosqlite:///{(tmp_path / 'nope.db').as_posix()}", tmp_path / "b.db")


def test_verify_detects_a_non_database_file(tmp_path: Path) -> None:
    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a database")
    with pytest.raises(sqlite3.DatabaseError):
        verify_sqlite_backup(broken)


def test_backup_is_independent_of_the_original(tmp_path: Path, database: tuple[Path, str]) -> None:
    path, url = database
    destination = tmp_path / "backup.db"
    backup_sqlite(url, destination)

    # Simulate the operator damaging the live database after the backup: the copy must survive.
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM channel")
        connection.commit()

    counts, integrity = verify_sqlite_backup(destination)
    assert integrity == "ok"
    assert counts["channel"] == 37


def test_create_backup_dispatches_on_scheme(tmp_path: Path, database: tuple[Path, str]) -> None:
    _path, url = database
    result = create_backup(tmp_path / "out.db", url)
    assert result.engine == "sqlite"
    with pytest.raises(BackupError, match="unsupported"):
        create_backup(tmp_path / "x", "mysql://example/db")


def test_sqlalchemy_driver_suffix_is_stripped_for_pg_dump() -> None:
    """pg_dump speaks libpq, not SQLAlchemy: `postgresql+psycopg://` would be rejected."""
    from iptvcc.backup import _libpq_url

    assert (
        _libpq_url("postgresql+psycopg://user:pass@db:5432/iptv")
        == "postgresql://user:pass@db:5432/iptv"
    )
    assert _libpq_url("postgresql://db/iptv") == "postgresql://db/iptv"
