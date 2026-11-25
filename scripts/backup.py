"""Create a database backup and print a JSON summary.

.\\.venv\\Scripts\\python.exe scripts\\backup.py
.\\.venv\\Scripts\\python.exe scripts\\backup.py --out backups/iptv.dump
.\\.venv\\Scripts\\python.exe scripts\\backup.py --verify backups/iptv.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from iptvcc.backup import BackupError, create_backup, suggest_name, verify_sqlite_backup
from iptvcc.config import get_settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=None, help="destination file")
    parser.add_argument("--database-url", default=None, help="defaults to IPTV_DATABASE_URL")
    parser.add_argument(
        "--verify", type=Path, default=None, help="verify an existing sqlite backup"
    )
    args = parser.parse_args()

    if args.verify is not None:
        counts, integrity = verify_sqlite_backup(args.verify)
        print(f"integrity: {integrity}")
        for name, rows in counts.items():
            print(f"  {name}: {rows}")
        return 0 if integrity == "ok" else 1

    settings = get_settings()
    database_url = args.database_url or settings.database_url
    engine = "sqlite" if database_url.startswith("sqlite") else "postgres"
    destination = args.out or suggest_name(Path("backups"), engine)

    try:
        result = create_backup(destination, database_url)
    except BackupError as exc:
        print(f"backup failed: {exc}", file=sys.stderr)
        return 2

    print(result.to_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
