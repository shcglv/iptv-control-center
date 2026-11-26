"""Rate limiting on the public delivery endpoints."""

from __future__ import annotations

import httpx
import pytest

from iptvcc.config import get_settings
from iptvcc.ratelimit import reset_limiter

from .conftest import ADMIN_HEADERS

LIMIT = 3


@pytest.fixture(autouse=True)
def small_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "public_rate_limit_per_minute", LIMIT, raising=False)
    reset_limiter()
    yield
    reset_limiter()


async def _make_device(client: httpx.AsyncClient, name: str) -> str:
    response = await client.post(
        "/api/v1/devices", json={"name": name, "kind": "desktop"}, headers=ADMIN_HEADERS
    )
    assert response.status_code == 201, response.text
    return str(response.json()["token"])


@pytest.mark.asyncio
async def test_playlist_is_rate_limited_per_token(client: httpx.AsyncClient) -> None:
    token = await _make_device(client, "Limited TV")

    for _ in range(LIMIT):
        response = await client.get(f"/p/{token}/live.m3u")
        assert response.status_code in {200, 404}, response.text

    blocked = await client.get(f"/p/{token}/live.m3u")
    assert blocked.status_code == 429
    assert blocked.headers["retry-after"]
    assert blocked.headers["x-ratelimit-remaining"] == "0"
    assert "too many requests" in blocked.json()["detail"]


@pytest.mark.asyncio
async def test_other_tokens_are_not_affected(client: httpx.AsyncClient) -> None:
    first = await _make_device(client, "First TV")
    second = await _make_device(client, "Second TV")

    for _ in range(LIMIT):
        await client.get(f"/p/{first}/live.m3u")
    assert (await client.get(f"/p/{first}/live.m3u")).status_code == 429

    # A different device keeps its own bucket.
    assert (await client.get(f"/p/{second}/live.m3u")).status_code in {200, 404}


@pytest.mark.asyncio
async def test_resolver_is_limited_too(client: httpx.AsyncClient) -> None:
    token = await _make_device(client, "Resolver TV")
    for _ in range(LIMIT):
        await client.get(f"/p/{token}/s/pk-whatever.ts")
    assert (await client.get(f"/p/{token}/s/pk-whatever.ts")).status_code == 429


@pytest.mark.asyncio
async def test_limit_can_be_disabled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "public_rate_limit_per_minute", 0, raising=False)
    reset_limiter()
    token = await _make_device(client, "Unlimited TV")
    for _ in range(LIMIT * 3):
        response = await client.get(f"/p/{token}/live.m3u")
        assert response.status_code != 429


@pytest.mark.asyncio
async def test_admin_api_is_not_rate_limited_by_the_public_window(
    client: httpx.AsyncClient,
) -> None:
    for _ in range(LIMIT * 2):
        assert (await client.get("/api/v1/sources", headers=ADMIN_HEADERS)).status_code == 200


# --- security headers --------------------------------------------------------------------------

BROWSER_HEADERS = (
    "x-content-type-options",
    "x-frame-options",
    "referrer-policy",
    "permissions-policy",
)


@pytest.mark.asyncio
async def test_every_response_carries_the_browser_headers(anon_client: httpx.AsyncClient) -> None:
    for path in ("/healthz", "/", "/api/v1/nope"):
        response = await anon_client.get(path)
        for header in BROWSER_HEADERS:
            assert header in response.headers, (path, header)
        assert response.headers["x-frame-options"] == "DENY"
        assert response.headers["x-content-type-options"] == "nosniff"


@pytest.mark.asyncio
async def test_hsts_only_when_the_base_url_is_https(
    anon_client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    plain = await anon_client.get("/healthz")
    assert "strict-transport-security" not in plain.headers

    monkeypatch.setattr(
        get_settings(), "public_base_url", "https://iptv.example.test", raising=False
    )
    secure = await anon_client.get("/healthz")
    assert secure.headers["strict-transport-security"].startswith("max-age=31536000")


# --- api documentation exposure ----------------------------------------------------------------


def test_docs_follow_the_environment() -> None:
    from iptvcc.config import Settings

    assert Settings(app_env="development").docs_enabled is True
    assert Settings(app_env="production").docs_enabled is False
    assert Settings(app_env="prod").docs_enabled is False
    assert Settings(app_env="production", enable_docs=True).docs_enabled is True
    assert Settings(app_env="development", enable_docs=False).docs_enabled is False


@pytest.mark.asyncio
async def test_docs_are_served_outside_production(anon_client: httpx.AsyncClient) -> None:
    # The test app runs with app_env=test, so the schema is available for developers.
    assert (await anon_client.get("/docs")).status_code == 200
    assert (await anon_client.get("/openapi.json")).status_code == 200
