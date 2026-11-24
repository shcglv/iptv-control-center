"""Browser smoke test: log in with TOTP, render the shell, navigate the sections.

Skipped unless ``IPTV_BROWSER_TESTS=1`` and the UI has been built, because it needs a Chromium
download. Run with:

    $env:IPTV_BROWSER_TESTS = "1"
    .\\.venv\\Scripts\\python.exe -m pytest tests/test_browser.py -q
"""

from __future__ import annotations

import base64
import os
import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from pathlib import Path

import pytest

playwright_api = pytest.importorskip("playwright.sync_api")
import pyotp  # noqa: E402

import iptvcc  # noqa: E402

STATIC = Path(iptvcc.__file__).parent / "static"
BUILT = (STATIC / "index.html").is_file()
ENABLED = os.environ.get("IPTV_BROWSER_TESTS") == "1"

pytestmark = [
    pytest.mark.browser,
    pytest.mark.skipif(not BUILT, reason="UI not built"),
    pytest.mark.skipif(not ENABLED, reason="set IPTV_BROWSER_TESTS=1 to run browser tests"),
]

PASSWORD = "correct-horse-battery"

M3U = (
    "#EXTM3U\n"
    '#EXTINF:-1 tvg-id="bbc1.uk" group-title="News",BBC One HD\nhttp://p.test/bbc.ts\n'
    '#EXTINF:-1 tvg-id="cnn.us" group-title="News",CNN\nhttp://p.test/cnn.m3u8\n'
    '#EXTINF:-1 group-title="Sport",Eurosport 1\nhttp://p.test/euro.ts\n'
)


def _seed_catalog(base: str, secret: str) -> None:
    """Create a source and import a playlist through the API, as an operator would."""
    import httpx

    client = httpx.Client(base_url=base, trust_env=False, timeout=20)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@local", "password": PASSWORD, "totp": pyotp.TOTP(secret).now()},
    )
    login.raise_for_status()
    headers = {"X-CSRF-Token": login.json()["csrf_token"]}
    source = client.post(
        "/api/v1/sources", json={"name": "Seed", "url": "http://p.test/l.m3u"}, headers=headers
    )
    source.raise_for_status()
    imported = client.post(
        f"/api/v1/sources/{source.json()['id']}/import", json={"m3u": M3U}, headers=headers
    )
    imported.raise_for_status()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for(url: str, timeout: float = 25.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.25)
    raise AssertionError(f"server did not become ready: {url}")


@pytest.fixture
def live_server(tmp_path: Path) -> Iterator[tuple[str, str]]:
    port = _free_port()
    database = tmp_path / "browser.db"
    env = {
        **os.environ,
        "IPTV_APP_ENV": "test",
        "IPTV_DATABASE_URL": f"sqlite+aiosqlite:///{database.as_posix()}",
        "IPTV_ENCRYPTION_KEY": base64.b64encode(b"b" * 32).decode(),
        "IPTV_PUBLIC_BASE_URL": f"http://127.0.0.1:{port}",
        "IPTV_SCHEDULER_ENABLED": "false",
    }
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        check=True,
        capture_output=True,
    )
    created = subprocess.run(
        [
            sys.executable,
            "-m",
            "iptvcc.cli",
            "create-admin",
            "--email",
            "admin@local",
            "--password",
            PASSWORD,
        ],
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    match = re.search(r"^\s{2}([A-Z2-7]{16,})\s*$", created.stdout, re.MULTILINE)
    assert match, created.stdout
    secret = match.group(1)

    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "iptvcc.api:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        _wait_for(f"http://127.0.0.1:{port}/healthz")
        yield f"http://127.0.0.1:{port}", secret
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


@pytest.mark.browser
def test_login_renders_dashboard_and_navigation(
    live_server: tuple[str, str], tmp_path: Path
) -> None:
    base, secret = live_server
    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)))

        page.goto(base, wait_until="domcontentloaded")

        # The login form is the first thing a new session sees.
        page.wait_for_selector("#email", timeout=15_000)
        assert "IPTV Control Center" in page.content()

        page.fill("#email", "admin@local")
        page.fill("#password", PASSWORD)
        page.fill("#totp", pyotp.TOTP(secret).now())
        page.click("button[type=submit]")

        # Overview renders after the session is accepted.
        page.wait_for_selector("text=Требует внимания", timeout=20_000)
        assert page.is_visible("text=Каналы")
        assert page.is_visible("text=Проверки")
        page.screenshot(path=str(tmp_path / "overview.png"), full_page=True)

        # Navigation works and each section renders its own heading.
        page.click("text=Источники")
        page.wait_for_selector("text=Новый источник", timeout=10_000)

        page.click("text=Плейлисты")
        page.wait_for_selector("text=Профили плейлистов", timeout=10_000)

        page.click("text=Устройства")
        page.wait_for_selector("text=Новое устройство", timeout=10_000)

        page.click("text=Качество")
        page.wait_for_selector("text=Проверки потоков", timeout=10_000)

        assert not errors, f"page errors: {errors}"
        browser.close()


@pytest.mark.browser
def test_profile_editor_hides_a_channel(live_server: tuple[str, str], tmp_path: Path) -> None:
    """The operator creates a profile and hides a channel; the UI reflects the override."""
    base, secret = live_server
    _seed_catalog(base, secret)

    with playwright_api.sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_selector("#email", timeout=15_000)
        page.fill("#email", "admin@local")
        page.fill("#password", PASSWORD)
        page.fill("#totp", pyotp.TOTP(secret).now())
        page.click("button[type=submit]")
        page.wait_for_selector("text=Требует внимания", timeout=20_000)

        page.get_by_role("button", name="Плейлисты").click()
        page.wait_for_selector("text=Профили плейлистов")

        page.get_by_placeholder("Название профиля").fill("Kids")
        page.get_by_role("button", name="Создать").click()
        page.get_by_role("row", name="Kids").wait_for(timeout=10_000)
        page.get_by_role("row", name="Kids").get_by_role("button", name="Редактировать").click()

        # The lineup appears; hide the first channel.
        checkbox = page.get_by_label("Видимость BBC One HD")
        checkbox.wait_for(timeout=10_000)
        assert checkbox.is_checked()
        checkbox.uncheck()

        page.get_by_text("изменён").first.wait_for(timeout=10_000)
        page.screenshot(path=str(tmp_path / "profiles-edited.png"), full_page=True)

        # The profile list reports one fewer visible channel.
        page.get_by_role("row", name="Kids").get_by_text("2 / 3").wait_for(timeout=10_000)
        browser.close()
