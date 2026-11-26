"""Real decoder check: ffmpeg must decode a stream fetched through our 302.

VLC could not be driven headlessly (see docs/REAL-WORLD.md), but ffmpeg is the demuxer/decoder
under most players and is scriptable. This runs it in a container on the same Docker network as
the app, so the whole chain stays inside Docker and no host firewall is involved:

    player -> /p/<token>/s/<key>.m3u8 -> 302 -> provider -> decoded frames

    .\\.venv\\Scripts\\python.exe data\\live_ffmpeg_decode.py
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

APP_IMAGE = "iptv-control-center:acceptance"
PLAYER_IMAGE = "iptv-control-center:ffmpeg"
NETWORK = "iptv-decode"
APP = "iptv-decode-app"
BASE = "http://127.0.0.1:18081"
INTERNAL = "http://iptv-decode-app:8000"
PASSWORD = "correct-horse-battery"

PLAYLIST = (
    "#EXTM3U\n"
    '#EXTINF:-1 tvg-id="bbb.test" group-title="Test",Big Buck Bunny\n'
    "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8\n"
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
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{BASE}/healthz", timeout=3) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(1)
    logs = docker("logs", APP).stderr[-1500:]
    raise SystemExit(f"app did not become healthy\n{logs}")


def main() -> int:
    docker("network", "create", NETWORK)
    docker("rm", "-f", APP)
    started = docker(
        "run",
        "-d",
        "--name",
        APP,
        "--network",
        NETWORK,
        "-p",
        "18081:8000",
        "-e",
        "IPTV_APP_ENV=test",
        "-e",
        f"IPTV_ENCRYPTION_KEY={base64.b64encode(os.urandom(32)).decode()}",
        "-e",
        "IPTV_DATABASE_URL=sqlite+aiosqlite:////tmp/decode.db",
        "-e",
        f"IPTV_PUBLIC_BASE_URL={INTERNAL}",
        "-e",
        "IPTV_ARTIFACTS_DIR=/tmp/artifacts",
        "-e",
        "IPTV_SCHEDULER_ENABLED=false",
        "-e",
        "IPTV_TELEGRAM_ENABLED=false",
        APP_IMAGE,
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
        secret = match.group(1)

        import httpx
        import pyotp

        client = httpx.Client(base_url=BASE, trust_env=False, timeout=30)
        login = client.post(
            "/api/v1/auth/login",
            json={"email": "admin@local", "password": PASSWORD, "totp": pyotp.TOTP(secret).now()},
        )
        login.raise_for_status()
        headers = {"X-CSRF-Token": login.json()["csrf_token"]}

        source = client.post(
            "/api/v1/sources",
            json={"name": "Public", "url": "http://p.test/l.m3u"},
            headers=headers,
        ).json()
        client.post(
            f"/api/v1/sources/{source['id']}/import", json={"m3u": PLAYLIST}, headers=headers
        ).raise_for_status()

        profile = client.post("/api/v1/profiles", json={"name": "Decode"}, headers=headers).json()
        device = client.post(
            "/api/v1/devices",
            json={"name": "Decoder", "kind": "desktop", "profile_id": profile["id"]},
            headers=headers,
        ).json()
        token = device["token"]

        channels = client.get("/api/v1/channels", headers=headers).json()["items"]
        channel = channels[0]
        resolver = f"{INTERNAL}/p/{token}/s/{channel['public_key']}.m3u8"
        print("resolver:", resolver)
        print("playlist:", f"{INTERNAL}/p/{token}/live.m3u")

        # Sanity: the resolver really redirects before we hand it to the decoder.
        direct = client.get(f"/p/{token}/s/{channel['public_key']}.m3u8", follow_redirects=False)
        print(
            "host-side resolver status:",
            direct.status_code,
            direct.headers.get("location", "")[:70],
        )

        print("decoding through the resolver with ffmpeg")
        player = docker(
            "run",
            "--rm",
            "--network",
            NETWORK,
            "--entrypoint",
            "ffmpeg",
            PLAYER_IMAGE,
            "-v",
            "error",
            "-i",
            resolver,
            "-t",
            "3",
            "-f",
            "null",
            "-",
        )
        stderr = player.stderr.strip()
        print("ffmpeg exit:", player.returncode)
        if stderr:
            print("ffmpeg stderr:", stderr[-800:])

        logs = docker("logs", APP).stdout
        resolved = logs.count("route=/p/{token}/s/{public_key}.{ext} status=302")
        print(f"server saw {resolved} resolver redirect(s)")

        if player.returncode != 0:
            print("RESULT: DECODER CHECK FAILED")
            return 4
        if resolved == 0:
            print("RESULT: ffmpeg decoded but the server log shows no 302 — investigate")
            return 5
        print("RESULT: DECODER CHECK PASSED (ffmpeg decoded through our 302)")
        return 0
    finally:
        docker("rm", "-f", APP)


if __name__ == "__main__":
    raise SystemExit(main())
