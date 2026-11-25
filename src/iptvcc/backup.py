"""Database backups.

SQLite uses the built-in online backup API, which is consistent even while the application is
writing. PostgreSQL shells out to ``pg_dump`` in custom format. Both produce a single file that can
be verified without starting the application.

Restore is deliberately manual (see ``docs/RUNBOOK.md``): replacing a live database automatically is
how people lose data.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sqlite3
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path


class BackupError(RuntimeError):
    """Raised when a backup cannot be produced or verified."""


@dataclass
class BackupResult:
    path: str
    engine: str
    size_bytes: int
    created_at: str
    tables: dict[str, int]
    integrity: str

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)


def _sqlite_path(database_url: str) -> Path:
    prefix = "sqlite+aiosqlite:///"
    if not database_url.startswith(prefix):
        raise BackupError(f"not a sqlite URL: {database_url.split('://', 1)[0]}")
    raw = database_url[len(prefix) :]
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    if not path.is_file():
        raise BackupError(f"database file does not exist: {path}")
    return path


def _table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    counts: dict[str, int] = {}
    for name in sorted(names):
        quoted = '"' + name.replace('"', '""') + '"'
        counts[name] = int(connection.execute(f"SELECT count(*) FROM {quoted}").fetchone()[0])
    return counts


def verify_sqlite_backup(path: Path) -> tuple[dict[str, int], str]:
    """Open a backup and return its table counts plus the SQLite integrity verdict."""
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        counts = _table_counts(connection)
    return counts, integrity


def backup_sqlite(database_url: str, destination: Path) -> BackupResult:
    source_path = _sqlite_path(database_url)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise BackupError(f"refusing to overwrite an existing backup: {destination}")

    with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
        source.backup(target)

    counts, integrity = verify_sqlite_backup(destination)
    if integrity != "ok":
        raise BackupError(f"backup failed integrity check: {integrity}")
    return BackupResult(
        path=str(destination),
        engine="sqlite",
        size_bytes=destination.stat().st_size,
        created_at=datetime.now(UTC).isoformat(),
        tables=counts,
        integrity=integrity,
    )


def _libpq_url(database_url: str) -> str:
    """Strip the SQLAlchemy driver suffix: libpq does not understand ``postgresql+psycopg://``."""
    _scheme, separator, rest = database_url.partition("://")
    return f"postgresql://{rest}" if separator else database_url


def backup_postgres(
    database_url: str, destination: Path, *, pg_dump: str | None = None
) -> BackupResult:
    """Stream ``pg_dump`` output into ``destination``.

    ``pg_dump`` may be a command prefix (``IPTV_PG_DUMP="docker exec -i pg pg_dump"``), which is how
    this path is exercised without client tools installed on the host.
    """
    prefix = shlex.split(pg_dump or os.environ.get("IPTV_PG_DUMP") or "pg_dump")
    if shutil.which(prefix[0]) is None and not Path(prefix[0]).exists():
        raise BackupError(f"{prefix[0]} is not installed in this environment")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise BackupError(f"refusing to overwrite an existing backup: {destination}")

    completed = subprocess.run(
        [*prefix, "--format=custom", "--no-owner", f"--dbname={_libpq_url(database_url)}"],
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        destination.unlink(missing_ok=True)
        detail = completed.stderr.decode("utf-8", errors="replace").strip()[:500]
        raise BackupError(f"pg_dump failed: {detail}")
    destination.write_bytes(completed.stdout)
    if destination.stat().st_size == 0:
        destination.unlink(missing_ok=True)
        raise BackupError("pg_dump produced an empty file")
    return BackupResult(
        path=str(destination),
        engine="postgres",
        size_bytes=destination.stat().st_size,
        created_at=datetime.now(UTC).isoformat(),
        tables={},
        integrity="not-checked",
    )


def create_backup(
    destination: Path, database_url: str, *, pg_dump: str | None = None
) -> BackupResult:
    if database_url.startswith("sqlite"):
        return backup_sqlite(database_url, destination)
    if database_url.startswith(("postgresql", "postgres")):
        return backup_postgres(database_url, destination, pg_dump=pg_dump)
    raise BackupError(f"unsupported database URL: {database_url.split('://', 1)[0]}")


def suggest_name(directory: Path, engine: str) -> Path:
    suffix = ".db" if engine == "sqlite" else ".dump"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return directory / f"iptv-{stamp}{suffix}"
