"""Container + PostgreSQL drill: the shape Coolify will deploy.

App container on a user-defined network, PostgreSQL by service name, migrations at start, the
acceptance flow over HTTP, a restart, and a backup executed from inside the app container.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pyotp

ROOT = Path(__file__).resolve().parent.parent
IMAGE = "iptv-control-center:pg"
APP = "iptv-pg-app"
PG = "iptv-pg"
NETWORK = "iptv-test"
PASSWORD = "correct-horse-battery"
BASE = "http://127.0.0.1:18080"
M3U = (
    "#EXTM3U\n"
    '#EXTINF:-1 tvg-id="bbc1.uk" group-title="News",BBC One HD\nhttp://p.test/bbc.ts\n'
    '#EXTINF:-1 tvg-id="cnn.us" group-title="News",CNN\nhttp://p.test/cnn.ts\n'
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def docker(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=False,
        **kwargs,  # type: ignore[arg-type]
    )


def wait_health(timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            response = httpx.get(f"{BASE}/healthz", timeout=3, trust_env=False)
            if response.status_code == 200:
                return
            last = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(1)
    running = docker("ps", "-q", "-f", f"name={APP}").stdout
    logs = docker("logs", APP).stderr[-2000:] if running else ""
    raise SystemExit(f"app did not become healthy: {last}\n{logs}")


def login(client: httpx.Client, secret: str) -> dict[str, str]:
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@local", "password": PASSWORD, "totp": pyotp.TOTP(secret).now()},
    )
    response.raise_for_status()
    return {"X-CSRF-Token": response.json()["csrf_token"]}


def main() -> int:
    print("building image")
    built = docker("build", "--build-arg", "INSTALL_PG_CLIENT=true", "-t", IMAGE, str(ROOT))
    if built.returncode != 0:
        print(built.stderr[-2500:])
        return 2

    print("waiting for postgres")
    for _ in range(60):
        ready = docker("exec", PG, "pg_isready", "-U", "postgres")
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        return 3

    docker("rm", "-f", APP)
    # Start from an empty database so the drill is repeatable.
    docker(
        "exec",
        PG,
        "psql",
        "-U",
        "postgres",
        "-c",
        "DROP DATABASE IF EXISTS iptv WITH (FORCE)",
    )
    docker("exec", PG, "psql", "-U", "postgres", "-c", "CREATE DATABASE iptv")
    encryption_key = base64.b64encode(os.urandom(32)).decode()
    started = docker(
        "run",
        "-d",
        "--name",
        APP,
        "--network",
        NETWORK,
        "-p",
        "18080:8000",
        "-e",
        "IPTV_APP_ENV=test",
        "-e",
        f"IPTV_ENCRYPTION_KEY={encryption_key}",
        "-e",
        "IPTV_DATABASE_URL=postgresql+psycopg://postgres:test@iptv-pg:5432/iptv",
        "-e",
        f"IPTV_PUBLIC_BASE_URL={BASE}",
        "-e",
        "IPTV_SCHEDULER_ENABLED=false",
        "-e",
        "IPTV_TELEGRAM_ENABLED=false",
        IMAGE,
    )
    if started.returncode != 0:
        print(started.stderr)
        return 4

    try:
        wait_health()
        print("healthy; checking migrations ran at start")
        version = docker(
            "exec",
            PG,
            "psql",
            "-U",
            "postgres",
            "-d",
            "iptv",
            "-tAc",
            "SELECT version_num FROM alembic_version",
        ).stdout.strip()
        print("alembic_version:", version)
        if not version:
            return 5

        created = docker(
            "exec",
            APP,
            "python",
            "-m",
            "iptvcc.cli",
            "create-admin",
            "--email",
            "admin@local",
            "--password",
            PASSWORD,
        )
        if created.returncode != 0:
            print(created.stdout, created.stderr)
            return 6
        match = re.search(r"^\s{2}([A-Z2-7]{16,})\s*$", created.stdout, re.MULTILINE)
        if not match:
            print(created.stdout)
            return 7
        secret = match.group(1)

        client = httpx.Client(base_url=BASE, trust_env=False, timeout=30)
        headers = login(client, secret)
        source = client.post(
            "/api/v1/sources", json={"name": "PG", "url": "http://p.test/l.m3u"}, headers=headers
        ).json()
        stats = client.post(
            f"/api/v1/sources/{source['id']}/import", json={"m3u": M3U}, headers=headers
        ).json()["stats"]
        print("import:", stats)

        profile = client.post("/api/v1/profiles", json={"name": "PG"}, headers=headers).json()
        device = client.post(
            "/api/v1/devices", json={"name": "PG TV", "profile_id": profile["id"]}, headers=headers
        ).json()
        playlist = client.get(f"/p/{device['token']}/live.m3u").text
        assert playlist.count("#EXTINF") == 2, playlist[:400]
        first = next(line for line in playlist.splitlines() if "/s/" in line)
        redirect = client.get(first, follow_redirects=False)
        print("resolver:", redirect.status_code, redirect.headers.get("location"))
        assert redirect.status_code in {302, 307}

        print("restarting the app container")
        docker("restart", APP)
        wait_health()
        headers = login(client, secret)
        total = client.get("/api/v1/channels", headers=headers).json()["total"]
        print("channels after restart:", total)
        assert total == 2

        print("backup from inside the app container")
        backup = docker("exec", APP, "python", "scripts/backup.py", "--out", "/tmp/pg.dump")
        if backup.returncode != 0:
            print(backup.stdout, backup.stderr)
            return 8
        summary = json.loads(backup.stdout)
        print("backup:", summary["engine"], summary["size_bytes"], "bytes")
        if summary["size_bytes"] <= 0:
            return 9
        listing = docker(
            "exec", APP, "sh", "-c", "pg_restore --list /tmp/pg.dump | grep -c 'TABLE DATA'"
        )
        print("table entries in dump:", listing.stdout.strip())

        print("RESULT: CONTAINER + POSTGRES DRILL PASSED")
        return 0
    finally:
        docker("rm", "-f", APP)


if __name__ == "__main__":
    raise SystemExit(main())
