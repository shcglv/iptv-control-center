"""Verify the panel checks inside the real image, not just under pytest.

The panel reads `alembic.ini` from the working directory and opens the app's own session, so the
container is the only place where "it works" means anything for this feature.

    .\\.venv\\Scripts\\python.exe scripts\\verify_panel.py
"""

from __future__ import annotations

import base64
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

APP = "iptv-panel-check"
IMAGE = "iptv-control-center:acceptance"
PORT = 18082
PASSWORD = "correct-horse-battery"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def docker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], capture_output=True, text=True, check=False)


def wait_health(timeout: float = 120.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(1)
    raise SystemExit(f"not healthy\n{docker('logs', APP).stderr[-1200:]}")


def main() -> int:
    docker("rm", "-f", APP)
    started = docker(
        "run",
        "-d",
        "--name",
        APP,
        "-p",
        f"{PORT}:8000",
        "-e",
        "IPTV_APP_ENV=production",
        "-e",
        f"IPTV_ENCRYPTION_KEY={base64.b64encode(os.urandom(32)).decode()}",
        "-e",
        "IPTV_DATABASE_URL=sqlite+aiosqlite:////tmp/panel.db",
        "-e",
        "IPTV_PUBLIC_BASE_URL=https://iptv.example.test",
        "-e",
        "IPTV_ARTIFACTS_DIR=/tmp/artifacts",
        "-e",
        "IPTV_SCHEDULER_ENABLED=false",
        "-e",
        "IPTV_TELEGRAM_ENABLED=false",
        IMAGE,
    )
    if started.returncode != 0:
        print(started.stderr)
        return 2
    try:
        wait_health()
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
        match = re.search(r"^\s{2}([A-Z2-7]{16,})\s*$", created.stdout, re.MULTILINE)
        if match is None:
            print(created.stdout, created.stderr)
            return 3

        import httpx
        import pyotp

        client = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", trust_env=False, timeout=30)
        login = client.post(
            "/api/v1/auth/login",
            json={
                "email": "admin@local",
                "password": PASSWORD,
                "totp": pyotp.TOTP(match.group(1)).now(),
            },
        )
        login.raise_for_status()
        # production + https base URL means a Secure cookie, which httpx will not send over plain
        # HTTP — expected, and exactly why this script passes it explicitly.
        cookie = "; ".join(f"{name}={value}" for name, value in login.cookies.items())
        headers = {"X-CSRF-Token": login.json()["csrf_token"], "Cookie": cookie}

        raw = client.get("/api/v1/system/info", headers=headers)
        if raw.status_code != 200 or "version" not in raw.text:
            print("system/info returned", raw.status_code, raw.text[:300])
            return 4
        info = raw.json()
        print(f"version={info['version']} db={info['database']} migrations={info['migrations']}")
        checks = client.get("/api/v1/system/check", headers=headers).json()
        for check in checks:
            print(f"  [{check['status']:<4}] {check['name']:<16} {check['detail'][:70]}")

        failures = [row for row in checks if row["status"] == "fail"]
        warnings = [row for row in checks if row["status"] == "warn"]
        migrations = next(row for row in checks if row["name"] == "migrations")

        # In production the interactive API documentation must not be reachable.
        for path in ("/docs", "/redoc", "/openapi.json"):
            status = client.get(path, follow_redirects=False).status_code
            print(f"  [{('ok' if status == 404 else 'fail'):<4}] {path:<16} {status}")
            if status != 404:
                failures.append({"name": path, "status": "fail", "detail": str(status)})

        if info["migrations"] is None:
            print("RESULT: FAILED - no applied migration reported")
            return 4
        if migrations["status"] != "ok":
            print(f"RESULT: FAILED - migrations check is {migrations['status']}")
            return 5
        if failures or warnings:
            print(f"RESULT: FAILED - {len(failures)} fail, {len(warnings)} warn")
            return 6
        print(f"RESULT: PANEL CHECK PASSED in the container ({len(checks)} checks)")
        return 0
    finally:
        docker("rm", "-f", APP)


if __name__ == "__main__":
    raise SystemExit(main())
