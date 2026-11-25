"""Import a channel catalog (iptv-org JSON) into the database and optionally match channels.

    .\\.venv\\Scripts\\python.exe scripts\\catalog_import.py --file channels.json
    .\\.venv\\Scripts\\python.exe scripts\\catalog_import.py --file channels.json --match

The file is the array from https://iptv-org.github.io/api/channels.json (or any list of objects with
``id`` and ``name``). The database is expected to be migrated already; the container does that at
start.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from iptvcc import db
from iptvcc.services.catalog import ingest_catalog, match_channels


async def run(path: Path, should_match: bool) -> int:
    payload = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = payload.get("channels", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        print("expected a JSON array of channel objects", file=sys.stderr)
        return 2

    async with db.get_session_factory()() as session:
        stats = await ingest_catalog(session, rows)
        await session.commit()
        print("catalog:", json.dumps(stats.as_dict()))
        if should_match:
            matched = await match_channels(session)
            await session.commit()
            print("match:", json.dumps(matched.as_dict()))
    await db.dispose_engine()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--match", action="store_true", help="run channel matching afterwards")
    args = parser.parse_args()
    return asyncio.run(run(args.file, args.match))


if __name__ == "__main__":
    raise SystemExit(main())
