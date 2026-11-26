"""Upgrade drill: data written on the previous schema must survive `alembic upgrade head`.

The fresh-database drills prove a first install. This proves an upgrade, which is what every
subsequent deploy does: start at the previous revision, write rows, upgrade, and check that nothing
was lost and the app still serves.

    .\\.venv\\Scripts\\python.exe scripts\\verify_upgrade.py
"""

from __future__ import annotations

import base64
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
DATABASE = "postgresql+psycopg://postgres:test@127.0.0.1:55432/iptv"
PREVIOUS = "198a8944b683"  # the revision released as 1.4.0
APP_PORT = 18085

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def ensure_postgres() -> None:
    docker("network", "create", NETWORK)
    running = docker("ps", "--filter", f"name={PG}", "--format", "{{.Names}}")
    if PG not in running.stdout:
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
            "POSTGRES_DB=iptv",
            "postgres:16-alpine",
        )
        for _ in range(60):
            if docker("exec", PG, "pg_isready", "-U", "postgres").returncode == 0:
                return
            time.sleep(1)
        raise SystemExit("postgres did not start")
    docker("network", "connect", NETWORK, PG)


def alembic(revision: str, env: dict[str, str]) -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", revision],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        print(completed.stdout[-1500:], completed.stderr[-1500:])
        raise SystemExit(f"alembic upgrade {revision} failed")
    applied = [
        line
        for line in completed.stderr.splitlines()
        if "Running upgrade" in line or "up to date" in line
    ]
    for line in applied:
        print(f"  {line.strip()}")


def main() -> int:
    if sys.platform == "win32":
        # psycopg cannot run on the Proactor loop that is the Windows default.
        import asyncio

        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    ensure_postgres()
    env = {
        **os.environ,
        "IPTV_DATABASE_URL": DATABASE,
        "IPTV_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
        "IPTV_APP_ENV": "test",
        "IPTV_SCHEDULER_ENABLED": "false",
        "IPTV_TELEGRAM_ENABLED": "false",
        "IPTV_PUBLIC_BASE_URL": f"http://127.0.0.1:{APP_PORT}",
        "IPTV_HOST": "127.0.0.1",
        "IPTV_PORT": str(APP_PORT),
    }
    os.environ.update(env)

    # A clean database at the previous revision, exactly as a running deployment would be.
    subprocess.run(
        [
            "docker",
            "exec",
            PG,
            "psql",
            "-U",
            "postgres",
            "-c",
            "DROP DATABASE IF EXISTS iptv WITH (FORCE)",
        ],
        capture_output=True,
    )
    subprocess.run(
        ["docker", "exec", PG, "psql", "-U", "postgres", "-c", "CREATE DATABASE iptv"],
        capture_output=True,
    )
    print(f"installing the schema as of {PREVIOUS}")
    alembic(PREVIOUS, env)

    import asyncio

    async def write_old_rows() -> str:
        from iptvcc import db
        from iptvcc.models import Assignment, Device, PlaylistProfile, Source
        from iptvcc.security import generate_device_token, hash_token
        from iptvcc.services.importing import import_playlist

        playlist = (
            "#EXTM3U\n"
            '#EXTINF:-1 tvg-id="upgrade.test" group-title="Upgrade",Upgrade TV\n'
            "http://p.test/upgrade.ts\n"
        )
        async with db.get_session_factory()() as session:
            source = Source(name="Upgrade", url="http://p.test/l.m3u")
            session.add(source)
            await session.flush()
            # The real import path, so the rows look exactly like a previous release wrote them.
            stats = await import_playlist(session, source, playlist)
            profile = PlaylistProfile(name="Upgrade", is_default=True)
            device = Device(name="Upgrade")
            session.add_all([profile, device])
            await session.flush()
            token = generate_device_token()
            session.add(
                Assignment(
                    device_id=device.id,
                    playlist_profile_id=profile.id,
                    token_hash=hash_token(token),
                    token_prefix="upg",
                )
            )
            await session.commit()
        await db.dispose_engine()
        print(f"imported on the old schema: {stats.as_dict()}")
        return token

    token = asyncio.run(write_old_rows())
    print("wrote a channel, a stream, a device and an assignment on the old schema")

    print("upgrading to head")
    alembic("head", env)

    async def verify() -> None:
        from sqlalchemy import func, select, text

        from iptvcc import db
        from iptvcc.models import Channel, Stream

        async with db.get_session_factory()() as session:
            version = await session.scalar(text("SELECT version_num FROM alembic_version"))
            channels = await session.scalar(select(func.count()).select_from(Channel))
            streams = await session.scalar(select(func.count()).select_from(Stream))
            new_table = await session.scalar(text("SELECT to_regclass('public.match_suggestion')"))
            print(
                f"after upgrade: version={version} channels={channels} "
                f"streams={streams} match_suggestion={bool(new_table)}"
            )
            assert version != PREVIOUS, "still on the old revision"
            assert channels == 1, "channels lost during the upgrade"
            assert streams == 1, "streams lost during the upgrade"
            assert new_table, "the new table was not created"
        await db.dispose_engine()

    asyncio.run(verify())

    # The upgraded database must still serve a playlist for the device created before the upgrade.
    server_log_path = ROOT / "data" / "upgrade-server.log"
    server = subprocess.Popen(
        [sys.executable, "-m", "iptvcc"],
        cwd=ROOT,
        env=env,
        stdout=server_log_path.open("w", encoding="utf-8"),
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
            log_text = (ROOT / "data" / "upgrade-server.log").read_text(
                encoding="utf-8", errors="replace"
            )
            print(log_text[-1500:])
            print("RESULT: FAILED - the upgraded database did not serve /healthz")
            return 4

        import httpx

        response = httpx.get(
            f"http://127.0.0.1:{APP_PORT}/p/{token}/live.m3u", timeout=20, trust_env=False
        )
        playlist = response.text
        channels = playlist.count("#EXTINF")
        print(f"playlist after upgrade: {channels} channel(s), HTTP {response.status_code}")
        print("  head:", (playlist[:200] or "<empty>").replace(chr(10), " | "))
        assert "#EXTINF" in playlist, playlist[:300]
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()

    print(f"RESULT: UPGRADE DRILL PASSED ({PREVIOUS} -> head, data intact, playlist served)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
