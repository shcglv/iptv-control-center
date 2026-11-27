"""End-to-end API tests: import -> catalog -> device -> playlist -> 302 resolver."""

from __future__ import annotations

import httpx
import pytest

from .conftest import ADMIN_HEADERS, SAMPLE_M3U


@pytest.mark.asyncio
async def test_health_reports_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["database"] == "ok"


@pytest.mark.asyncio
async def test_admin_endpoints_require_session(anon_client: httpx.AsyncClient) -> None:
    assert (await anon_client.get("/api/v1/sources")).status_code == 401
    assert (await anon_client.get("/api/v1/channels")).status_code == 401
    assert (await anon_client.get("/api/v1/devices")).status_code == 401


@pytest.mark.asyncio
async def test_import_and_delivery_flow(client: httpx.AsyncClient) -> None:
    created = await client.post(
        "/api/v1/sources",
        json={"name": "Demo", "url": "http://provider.test/list.m3u"},
        headers=ADMIN_HEADERS,
    )
    assert created.status_code == 201, created.text
    source = created.json()
    assert source["url_masked"] == "http://provider.test/…"
    source_id = source["id"]

    imported = await client.post(
        f"/api/v1/sources/{source_id}/import",
        json={"m3u": SAMPLE_M3U},
        headers=ADMIN_HEADERS,
    )
    assert imported.status_code == 200, imported.text
    stats = imported.json()["stats"]
    assert stats["parsed"] == 4
    assert stats["created"] == 4
    assert stats["channels_created"] == 4
    assert stats["channels_linked"] == 4

    page = (await client.get("/api/v1/channels", headers=ADMIN_HEADERS)).json()
    assert page["total"] == 4
    names = {item["name"] for item in page["items"]}
    assert {"BBC One HD", "CNN", "Eurosport 1 [HD]", "No Attributes Channel"} == names

    device = await client.post(
        "/api/v1/devices", json={"name": "Living Room"}, headers=ADMIN_HEADERS
    )
    assert device.status_code == 201, device.text
    token = device.json()["token"]
    assert token.startswith("ipt_")

    playlist = await client.get(f"/p/{token}/live.m3u")
    assert playlist.status_code == 200
    body = playlist.text
    assert body.startswith("#EXTM3U")
    assert 'tvg-id="bbc1.uk"' in body
    # provider URLs and credentials must never appear in a playlist
    assert "provider.test" not in body

    stream_lines = [line for line in body.splitlines() if line.startswith("http")]
    assert len(stream_lines) == 4
    stream_path = stream_lines[0].split("http://test", 1)[1]

    resolved = await client.get(stream_path)
    assert resolved.status_code == 302
    assert resolved.headers["location"].startswith("http://provider.test/live/")
    assert resolved.headers["cache-control"] == "no-store"

    head = await client.head(stream_path)
    assert head.status_code == 200


@pytest.mark.asyncio
async def test_reimport_is_idempotent(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "Twice", "url": "http://p.test/a.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()

    first = (
        await client.post(
            f"/api/v1/sources/{source['id']}/import",
            json={"m3u": SAMPLE_M3U},
            headers=ADMIN_HEADERS,
        )
    ).json()["stats"]
    second = (
        await client.post(
            f"/api/v1/sources/{source['id']}/import",
            json={"m3u": SAMPLE_M3U},
            headers=ADMIN_HEADERS,
        )
    ).json()["stats"]

    assert first["created"] == 4
    assert second["created"] == 0
    assert second["channels_created"] == 0
    assert second["channels_linked"] == 0
    assert second["unchanged"] == 4

    page = (await client.get("/api/v1/channels", headers=ADMIN_HEADERS)).json()
    assert page["total"] == 4


@pytest.mark.asyncio
async def test_removed_streams_are_marked_stale_not_deleted(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "Shrink", "url": "http://p.test/s.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )
    smaller = '#EXTM3U\n#EXTINF:-1 tvg-id="bbc1.uk",BBC One HD\nhttp://provider.test/live/bbc1.ts\n'
    stats = (
        await client.post(
            f"/api/v1/sources/{source['id']}/import", json={"m3u": smaller}, headers=ADMIN_HEADERS
        )
    ).json()["stats"]
    assert stats["stale"] == 3

    page = (await client.get("/api/v1/channels", headers=ADMIN_HEADERS)).json()
    assert page["total"] == 4  # channels survive a provider outage


@pytest.mark.asyncio
async def test_token_rotation_revokes_the_old_token(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "Rot", "url": "http://p.test/r.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )
    device = (
        await client.post("/api/v1/devices", json={"name": "Bedroom"}, headers=ADMIN_HEADERS)
    ).json()
    old_token = device["token"]
    assert (await client.get(f"/p/{old_token}/live.m3u")).status_code == 200

    rotated = (
        await client.post(f"/api/v1/devices/{device['id']}/rotate", headers=ADMIN_HEADERS)
    ).json()
    new_token = rotated["token"]
    assert new_token != old_token
    assert (await client.get(f"/p/{old_token}/live.m3u")).status_code == 404
    assert (await client.get(f"/p/{new_token}/live.m3u")).status_code == 200


@pytest.mark.asyncio
async def test_import_rejects_non_playlist_body(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "Bad", "url": "http://p.test/b.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    response = await client.post(
        f"/api/v1/sources/{source['id']}/import",
        json={"m3u": "<html>nope</html>"},
        headers=ADMIN_HEADERS,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_resolver_rejects_unknown_token(client: httpx.AsyncClient) -> None:
    assert (await client.get("/p/nope/live.m3u")).status_code == 404


@pytest.mark.asyncio
async def test_import_deduplicates_a_repeated_url(client: httpx.AsyncClient) -> None:
    """The same URL twice in one playlist must produce one stream, not two."""
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "Dup", "url": "http://p.test/d.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    playlist = (
        "#EXTM3U\n"
        '#EXTINF:-1 tvg-id="one.test",One\nhttp://provider.test/same.ts\n'
        '#EXTINF:-1 tvg-id="one.test",One\nhttp://provider.test/same.ts\n'
    )
    response = await client.post(
        f"/api/v1/sources/{source['id']}/import",
        json={"m3u": playlist},
        headers=ADMIN_HEADERS,
    )
    stats = response.json()["stats"]
    assert stats["parsed"] == 2
    assert stats["created"] == 1
    assert stats["channels_created"] == 1
    assert stats["channels_linked"] == 1


@pytest.mark.asyncio
async def test_channel_sources_endpoint(client: httpx.AsyncClient) -> None:
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "Src", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )
    page = (await client.get("/api/v1/channels", headers=ADMIN_HEADERS)).json()
    channel = page["items"][0]

    response = await client.get(
        f"/api/v1/channels/{channel['public_key']}/sources", headers=ADMIN_HEADERS
    )
    assert response.status_code == 200
    rows = response.json()
    assert len(rows) == 1
    assert rows[0]["stream_id"] > 0
    assert rows[0]["container"] in {"ts", "m3u8", "mpd", "other"}
    assert 0.0 <= rows[0]["score"] <= 100.0

    missing = await client.get("/api/v1/channels/pk-nope/sources", headers=ADMIN_HEADERS)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_health_reports_503_when_the_database_is_unreachable(
    client: httpx.AsyncClient,
) -> None:
    """A monitor judges by status code: 200 with 'degraded' would read as healthy."""
    from iptvcc.api import app
    from iptvcc.db import get_session

    class BrokenSession:
        async def execute(self, *args: object, **kwargs: object) -> object:
            raise RuntimeError("database is gone")

    async def broken_session():
        yield BrokenSession()

    app.dependency_overrides[get_session] = broken_session
    try:
        response = await client.get("/healthz")
        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "degraded"
        assert body["database"] == "error"
    finally:
        app.dependency_overrides.pop(get_session, None)


@pytest.mark.asyncio
async def test_two_devices_share_one_render_but_keep_their_own_tokens(
    client: httpx.AsyncClient,
) -> None:
    """The cache is keyed by profile, not by token, and substitutes the token per request."""
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "M3U", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )
    first = (
        await client.post("/api/v1/devices", json={"name": "TV A"}, headers=ADMIN_HEADERS)
    ).json()
    second = (
        await client.post("/api/v1/devices", json={"name": "TV B"}, headers=ADMIN_HEADERS)
    ).json()

    a = (await client.get(f"/p/{first['token']}/live.m3u")).text
    b = (await client.get(f"/p/{second['token']}/live.m3u")).text

    assert first["token"] in a
    assert second["token"] in b
    assert first["token"] not in b
    # Same channels, same order: only the token differs.
    assert a.replace(first["token"], "TOKEN") == b.replace(second["token"], "TOKEN")


@pytest.mark.asyncio
async def test_the_cache_can_be_disabled(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from iptvcc.config import get_settings

    monkeypatch.setattr(get_settings(), "playlist_cache_seconds", 0, raising=False)
    source = (
        await client.post(
            "/api/v1/sources",
            json={"name": "M3U", "url": "http://p.test/l.m3u"},
            headers=ADMIN_HEADERS,
        )
    ).json()
    await client.post(
        f"/api/v1/sources/{source['id']}/import", json={"m3u": SAMPLE_M3U}, headers=ADMIN_HEADERS
    )
    device = (
        await client.post("/api/v1/devices", json={"name": "TV"}, headers=ADMIN_HEADERS)
    ).json()
    playlist = (await client.get(f"/p/{device['token']}/live.m3u")).text
    assert device["token"] in playlist
    assert "__IPTVCC_TOKEN__" not in playlist
