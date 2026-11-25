"""Acceptance test against the built container image.

Everything here talks to the real artifact over HTTP: the same image Coolify would deploy. It is
opt-in because it needs a Docker daemon and a build:

    $env:IPTV_CONTAINER_TESTS = "1"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_container.py -q

The flow is the product's core path end to end: create the admin inside the container, import a
playlist, build a profile that hides a channel, create a device, fetch its playlist and guide,
resolve a stream, discover a source from a local feed, accept it, restart the container and check
that everything survived.
"""

from __future__ import annotations

import base64
import os
import re
import socket
import subprocess
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest

pytestmark = [
    pytest.mark.container,
    pytest.mark.skipif(
        os.environ.get("IPTV_CONTAINER_TESTS") != "1",
        reason="set IPTV_CONTAINER_TESTS=1 to run the container acceptance test",
    ),
]

ROOT = Path(__file__).resolve().parent.parent
IMAGE = os.environ.get("IPTV_TEST_IMAGE", "iptv-control-center:acceptance")
SKIP_BUILD = os.environ.get("IPTV_TEST_IMAGE_SKIP_BUILD") == "1"
CONTAINER = "iptv-acceptance"
PASSWORD = "correct-horse-battery"

M3U = (
    "#EXTM3U\n"
    '#EXTINF:-1 tvg-id="bbc1.uk" group-title="News",BBC One HD\nhttp://provider.test/bbc.ts\n'
    '#EXTINF:-1 tvg-id="cnn.us" group-title="News",CNN\nhttp://provider.test/cnn.ts\n'
    '#EXTINF:-1 group-title="Sport",Eurosport 1\nhttp://provider.test/euro.ts\n'
)

XMLTV = """<?xml version="1.0" encoding="UTF-8"?>
<tv>
  <channel id="bbc1.uk"><display-name>BBC One HD</display-name></channel>
  <programme start="20261125180000 +0000" stop="20261125190000 +0000" channel="bbc1.uk">
    <title>Evening News</title>
  </programme>
</tv>
"""

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Forum</title>
<item><title>Panel access</title><link>http://forum.test/t/2</link>
<description>http://panel.test/get.php?username=bob&amp;password=secret</description></item>
</channel></rss>
"""


# --- helpers -----------------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _docker(*args: str, **kwargs: object) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args],
        capture_output=True,
        text=True,
        check=False,
        **kwargs,  # type: ignore[arg-type]
    )


def _wait_healthy(base: str, timeout: float = 90.0) -> None:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            response = httpx.get(f"{base}/healthz", timeout=3, trust_env=False)
            if response.status_code == 200:
                return
            last = f"HTTP {response.status_code}"
        except httpx.HTTPError as exc:
            last = str(exc)
        time.sleep(1)
    raise AssertionError(f"container did not become healthy: {last}")


def _login(client: httpx.Client, secret: str) -> dict[str, str]:
    import pyotp

    response = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@local", "password": PASSWORD, "totp": pyotp.TOTP(secret).now()},
    )
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf_token"]}


# --- fixtures ----------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def image() -> None:
    if _docker("info", "--format", "{{.ServerVersion}}").returncode != 0:
        pytest.skip("docker daemon is not available")
    if SKIP_BUILD:
        present = _docker("image", "inspect", IMAGE)
        assert present.returncode == 0, f"image {IMAGE} is not present"
        return
    built = subprocess.run(
        ["docker", "build", "-t", IMAGE, str(ROOT)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert built.returncode == 0, built.stderr[-3000:]


@pytest.fixture(scope="module")
def container(image: None, tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    data_dir = tmp_path_factory.mktemp("iptv-data")
    # The app runs as uid 10001; a bind mount must be writable for it.
    _docker(
        "run",
        "--rm",
        "--user",
        "root",
        "-v",
        f"{data_dir}:/data",
        IMAGE,
        "chmod",
        "-R",
        "777",
        "/data",
    )

    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    key = base64.b64encode(b"c" * 32).decode()
    started = _docker(
        "run",
        "-d",
        "--name",
        CONTAINER,
        "-p",
        f"{port}:8000",
        "-v",
        f"{data_dir}:/data",
        "-e",
        "IPTV_APP_ENV=test",
        "-e",
        f"IPTV_ENCRYPTION_KEY={key}",
        "-e",
        "IPTV_DATABASE_URL=sqlite+aiosqlite:////data/iptv.db",
        "-e",
        f"IPTV_PUBLIC_BASE_URL={base}",
        "-e",
        "IPTV_SCHEDULER_ENABLED=false",
        "-e",
        "IPTV_TELEGRAM_ENABLED=false",
        "-e",
        "IPTV_ALLOW_PRIVATE_NETWORK_TARGETS=1",
        "-e",
        "IPTV_ARTIFACTS_DIR=/data/artifacts",
        IMAGE,
    )
    assert started.returncode == 0, started.stderr
    try:
        _wait_healthy(base)

        created = _docker(
            "exec",
            CONTAINER,
            "python",
            "-m",
            "iptvcc.cli",
            "create-admin",
            "--email",
            "admin@local",
            "--password",
            PASSWORD,
        )
        assert created.returncode == 0, created.stdout + created.stderr
        match = re.search(r"^\s{2}([A-Z2-7]{16,})\s*$", created.stdout, re.MULTILINE)
        assert match, created.stdout
        yield base, match.group(1)
    finally:
        _docker("rm", "-f", CONTAINER)


@pytest.fixture
def client(container: tuple[str, str]) -> Iterator[httpx.Client]:
    base, _secret = container
    with httpx.Client(base_url=base, trust_env=False, timeout=30) as http:
        yield http


# --- the acceptance run ------------------------------------------------------------------------


def test_container_runs_as_uid_10001(image: None, container: tuple[str, str]) -> None:
    _base, _secret = container
    identity = _docker("exec", CONTAINER, "id", "-u")
    assert identity.stdout.strip() == "10001"


def test_ui_and_health_are_served(client: httpx.Client) -> None:
    root = client.get("/")
    assert root.status_code == 200
    assert 'id="root"' in root.text
    assert client.get("/healthz").json()["status"] == "ok"


def test_full_flow_and_persistence(
    container: tuple[str, str], client: httpx.Client, tmp_path: Path
) -> None:
    _base, secret = container
    headers = _login(client, secret)

    # 1. import a playlist
    source = client.post(
        "/api/v1/sources",
        json={"name": "Acceptance", "url": "http://provider.test/l.m3u"},
        headers=headers,
    )
    assert source.status_code == 201, source.text
    source_id = source.json()["id"]
    imported = client.post(
        f"/api/v1/sources/{source_id}/import", json={"m3u": M3U}, headers=headers
    )
    assert imported.status_code == 200, imported.text
    assert imported.json()["stats"]["channels_created"] == 3

    # 2. a profile that hides CNN
    profile = client.post("/api/v1/profiles", json={"name": "No CNN"}, headers=headers).json()
    channels = client.get(f"/api/v1/profiles/{profile['id']}/channels", headers=headers).json()
    cnn = next(row for row in channels if row["name"] == "CNN")
    hidden = client.put(
        f"/api/v1/profiles/{profile['id']}/channels/{cnn['public_key']}",
        json={"visible": False},
        headers=headers,
    )
    assert hidden.status_code == 200, hidden.text

    # 3. a device on that profile
    device = client.post(
        "/api/v1/devices",
        json={"name": "Acceptance TV", "profile_id": profile["id"]},
        headers=headers,
    ).json()
    token = device["token"]

    playlist = client.get(f"/p/{token}/live.m3u")
    assert playlist.status_code == 200
    body = playlist.text
    assert "BBC One HD" in body
    assert "Eurosport 1" in body
    assert "CNN" not in body
    assert 'url-tvg="' in body
    assert 'x-tvg-url="' in body
    assert 'tvg-url="' in body

    # 4. the resolver answers with a 302 to the provider, never a proxy
    first_url = next(
        line for line in body.splitlines() if line.startswith("http") and "/s/" in line
    )
    resolved = client.get(first_url, follow_redirects=False)
    assert resolved.status_code in {302, 307}, resolved.text
    assert resolved.headers["location"].startswith("http://provider.test/")

    # 5. EPG import and a per-device guide
    epg = client.post(
        "/api/v1/epg/sources", json={"name": "Guide", "default_timezone": "UTC"}, headers=headers
    ).json()
    assert (
        client.post(
            f"/api/v1/epg/sources/{epg['id']}/import", json={"xml": XMLTV}, headers=headers
        ).status_code
        == 200
    )
    guide = client.get(f"/p/{token}/epg.xml")
    assert guide.status_code == 200
    assert b'id="bbc1.uk"' in guide.content
    assert b"Evening News" in guide.content

    # 6. discovery against a feed served by the test process (reachable as host.docker.internal)
    server, feed_url = _feed_server()
    try:
        run = client.post(
            "/api/v1/discovery/run",
            json={"adapter": "rss", "url": feed_url, "limit": 10},
            headers=headers,
        )
        assert run.status_code == 200, run.text
        assert run.json()["created"] == 1
        candidate = client.get("/api/v1/discovery/candidates", headers=headers).json()["items"][0]
        assert "secret" not in candidate["url_masked"]
        accepted = client.post(
            f"/api/v1/discovery/candidates/{candidate['id']}/accept",
            json={"name": "From forum"},
            headers=headers,
        )
        assert accepted.status_code == 200, accepted.text
    finally:
        server.shutdown()
        server.server_close()

    # 7. restart the container: the SQLite file lives on the mounted volume
    assert _docker("restart", CONTAINER).returncode == 0
    _wait_healthy(_base)

    headers = _login(client, secret)
    total = client.get("/api/v1/channels", headers=headers).json()["total"]
    assert total == 3, total
    sources = {row["name"] for row in client.get("/api/v1/sources", headers=headers).json()}
    assert {"Acceptance", "From forum"} <= sources
    assert client.get(f"/p/{token}/live.m3u").status_code == 200
    assert client.get(f"/p/{token}/live.m3u").text.count("#EXTINF") == 2


def _feed_server() -> tuple[object, str]:
    """Serve the RSS feed from the test process, addressed from inside the container."""
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = FEED.encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/rss+xml")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("0.0.0.0", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_port
    # Docker Desktop resolves this to the host; it is a private address, hence the SSRF switch.
    return server, f"http://host.docker.internal:{port}/feed.xml"
