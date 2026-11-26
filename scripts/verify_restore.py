"""Restore drill: a PostgreSQL dump must actually restore, and the restored database must serve.

The other drills prove a dump is produced. This proves it is usable: dump a database with real
imported data, restore it into a fresh one, compare every table's row count, and start the
application against the restored copy.

    .\\.venv\\Scripts\\python.exe scripts\\verify_restore.py
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PG = "iptv-pg"
NETWORK = "iptv-test"
PORT = 55432
SRC = "iptv_restore_src"
DST = "iptv_restore_dst"
APP_PORT = 18095
DUMP = "/tmp/restore-drill.dump"

M3U = (
    "#EXTM3U\n"
    '#EXTINF:-1 tvg-id="one.test" group-title="Restore",Restore One\n'
    "http://p.test/one.ts\n"
    '#EXTINF:-1 tvg-id="two.test" group-title="Restore",Restore Two\n'
    "http://p.test/two.m3u8\n"
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def url(database: str) -> str:
    return f"postgresql+psycopg://postgres:test@127.0.0.1:{PORT}/{database}"


def ensure_postgres() -> None:
    docker("network", "create", NETWORK)
    if PG not in docker("ps", "--filter", f"name={PG}", "--format", "{{.Names}}").stdout:
        docker("rm", "-f", PG)
        docker(
            "run",
            "-d",
            "--name",
            PG,
            "--network",
            NETWORK,
            "-p",
            f"{PORT}:5432",
            "-e",
            "POSTGRES_PASSWORD=test",
            "-e",
            "POSTGRES_DB=postgres",
            "postgres:16-alpine",
        )
        for _ in range(60):
            if docker("exec", PG, "pg_isready", "-U", "postgres").returncode == 0:
                return
            time.sleep(1)
        raise SystemExit("postgres did not start")
    docker("network", "connect", NETWORK, PG)


def psql(sql: str) -> subprocess.CompletedProcess[str]:
    return docker("exec", PG, "psql", "-U", "postgres", "-c", sql)


def main() -> int:
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    ensure_postgres()

    for database in (SRC, DST):
        psql(f"DROP DATABASE IF EXISTS {database} WITH (FORCE)")
        psql(f"CREATE DATABASE {database}")

    env = {
        **os.environ,
        "IPTV_DATABASE_URL": url(SRC),
        "IPTV_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
        "IPTV_APP_ENV": "test",
        "IPTV_SCHEDULER_ENABLED": "false",
        "IPTV_TELEGRAM_ENABLED": "false",
        "IPTV_PUBLIC_BASE_URL": f"http://127.0.0.1:{APP_PORT}",
        "IPTV_HOST": "127.0.0.1",
        "IPTV_PORT": str(APP_PORT),
    }
    os.environ.update(env)

    migrated = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if migrated.returncode != 0:
        print(migrated.stdout[-1200:], migrated.stderr[-1200:])
        return 2

    async def seed() -> str:
        from iptvcc import db
        from iptvcc.models import Assignment, Device, PlaylistProfile, Source
        from iptvcc.security import generate_device_token, hash_token
        from iptvcc.services.importing import import_playlist

        async with db.get_session_factory()() as session:
            source = Source(name="Restore", url="http://p.test/l.m3u")
            session.add(source)
            await session.flush()
            stats = await import_playlist(session, source, M3U)
            profile = PlaylistProfile(name="Restore", is_default=True)
            device = Device(name="Restore")
            session.add_all([profile, device])
            await session.flush()
            token = generate_device_token()
            session.add(
                Assignment(
                    device_id=device.id,
                    playlist_profile_id=profile.id,
                    token_hash=hash_token(token),
                    token_prefix="res",
                )
            )
            await session.commit()
        await db.dispose_engine()
        print(f"seeded source database: {stats.as_dict()}")
        return token

    token = asyncio.run(seed())

    def counts(database: str) -> dict[str, int]:
        tables = docker(
            "exec",
            PG,
            "psql",
            "-U",
            "postgres",
            "-d",
            database,
            "-t",
            "-A",
            "-c",
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename",
        ).stdout.split()
        values: dict[str, int] = {}
        for table in tables:
            row = docker(
                "exec",
                PG,
                "psql",
                "-U",
                "postgres",
                "-d",
                database,
                "-t",
                "-A",
                "-c",
                f'SELECT count(*) FROM "{table}"',
            )
            values[table] = int(row.stdout.strip() or 0)
        return values

    print("dumping with pg_dump")
    dumped = docker("exec", PG, "pg_dump", "-U", "postgres", "-Fc", "-d", SRC, "-f", DUMP)
    if dumped.returncode != 0:
        print(dumped.stderr[-1200:])
        return 3

    print("restoring with pg_restore")
    restored = docker("exec", PG, "pg_restore", "-U", "postgres", "-d", DST, DUMP)
    if restored.returncode != 0:
        print(restored.stderr[-1200:])
        return 4

    source_counts = counts(SRC)
    restored_counts = counts(DST)
    print(f"tables compared: {len(source_counts)}")
    mismatches = {
        table: (source_counts[table], restored_counts.get(table))
        for table in source_counts
        if source_counts[table] != restored_counts.get(table)
    }
    if mismatches:
        print("row-count mismatches:", json.dumps(mismatches, indent=2))
        return 5
    total = sum(source_counts.values())
    print(f"row counts match across {len(source_counts)} tables ({total} rows)")

    # The restored copy must serve, not merely contain rows.
    env_dst = {**env, "IPTV_DATABASE_URL": url(DST)}
    log_path = ROOT / "data" / "restore-server.log"
    server = subprocess.Popen(
        [sys.executable, "-m", "iptvcc"],
        cwd=ROOT,
        env=env_dst,
        stdout=log_path.open("w", encoding="utf-8"),
        stderr=subprocess.STDOUT,
    )
    try:
        for _ in range(120):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{APP_PORT}/healthz", timeout=2) as r:
                    if r.status == 200:
                        break
            except (urllib.error.URLError, TimeoutError, ConnectionError):
                time.sleep(0.5)
        else:
            print(log_path.read_text(encoding="utf-8", errors="replace")[-1200:])
            print("RESULT: FAILED - the restored database did not serve /healthz")
            return 6

        import httpx

        response = httpx.get(
            f"http://127.0.0.1:{APP_PORT}/p/{token}/live.m3u", timeout=20, trust_env=False
        )
        channels = response.text.count("#EXTINF")
        print(
            f"playlist from the restored copy: {channels} channel(s), HTTP {response.status_code}"
        )
        if channels != 2:
            print("RESULT: FAILED - the restored copy does not serve the expected channels")
            return 7
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    print("RESULT: RESTORE DRILL PASSED (dump restored, row counts match, playlist served)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
