"""Diagnostics: request correlation and the channel diagnosis endpoint."""

from __future__ import annotations

import httpx
import pytest

from .conftest import ADMIN_HEADERS, SAMPLE_M3U

CATALOG = [{"id": "BBCOne.uk", "name": "BBC One", "country": "GB"}]


@pytest.mark.asyncio
async def test_request_id_header_is_returned(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.get("/healthz")
    assert response.status_code == 200
    assert response.headers.get("x-request-id")


@pytest.mark.asyncio
async def test_incoming_request_id_is_propagated(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.get("/healthz", headers={"X-Request-ID": "abc123"})
    assert response.headers.get("x-request-id") == "abc123"


@pytest.mark.asyncio
async def test_logs_use_the_route_template_not_the_device_token(
    client: httpx.AsyncClient, caplog: pytest.LogCaptureFixture
) -> None:
    device = (
        await client.post(
            "/api/v1/devices", json={"name": "Log TV", "kind": "desktop"}, headers=ADMIN_HEADERS
        )
    ).json()
    token = device["token"]

    with caplog.at_level("INFO", logger="iptvcc.request"):
        response = await client.get(f"/p/{token}/live.m3u")
    assert response.status_code in {200, 404}

    messages = [record.getMessage() for record in caplog.records if record.name == "iptvcc.request"]
    assert messages, "the request was not logged"
    joined = "\n".join(messages)
    assert token not in joined
    assert "/p/{token}/live.m3u" in joined


# --- channel diagnosis -------------------------------------------------------------------------


async def _import_channel(client: httpx.AsyncClient) -> dict[str, object]:
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
    # A profile must exist for the visibility section to have anything to report.
    await client.post("/api/v1/profiles", json={"name": "Default"}, headers=ADMIN_HEADERS)
    page = (await client.get("/api/v1/channels", headers=ADMIN_HEADERS)).json()
    return next(row for row in page["items"] if row["name"] == "BBC One HD")


@pytest.mark.asyncio
async def test_diagnose_explains_each_source(client: httpx.AsyncClient) -> None:
    channel = await _import_channel(client)
    response = await client.get(
        f"/api/v1/channels/{channel['public_key']}/diagnose", headers=ADMIN_HEADERS
    )
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["name"] == "BBC One HD"
    assert body["hidden"] is False
    assert body["blocked_by"] is None
    assert len(body["streams"]) == 1

    stream = body["streams"][0]
    assert stream["container"] == "ts"
    assert stream["eligible"]["ts"] is True
    assert stream["eligible"]["m3u8"] is False
    assert any("ещё не проверялся" in reason for reason in stream["reasons"])

    # A .ts channel must only resolve for .ts, never for a manifest.
    assert body["resolution"]["ts"] == stream["stream_id"]
    assert body["resolution"]["m3u8"] is None
    assert body["profiles"], "the profile visibility section must be present"


@pytest.mark.asyncio
async def test_diagnose_reports_a_take_down_block(client: httpx.AsyncClient) -> None:
    channel = await _import_channel(client)
    await client.post("/api/v1/catalog/import", json={"channels": CATALOG}, headers=ADMIN_HEADERS)
    await client.post("/api/v1/catalog/match", headers=ADMIN_HEADERS)
    await client.post(
        "/api/v1/blocklist",
        json={"kind": "catalog_id", "value": "BBCOne.uk"},
        headers=ADMIN_HEADERS,
    )

    body = (
        await client.get(
            f"/api/v1/channels/{channel['public_key']}/diagnose", headers=ADMIN_HEADERS
        )
    ).json()
    assert body["hidden"] is True
    assert body["blocked_by"] == "catalog_id=BBCOne.uk"
    assert all(profile["visible"] is False for profile in body["profiles"])


@pytest.mark.asyncio
async def test_diagnose_unknown_channel(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/v1/channels/pk-nope/diagnose", headers=ADMIN_HEADERS)
    assert response.status_code == 404
