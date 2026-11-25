"""Client compatibility: what a device family may be offered."""

from __future__ import annotations

import httpx
import pytest

from iptvcc.services.capabilities import (
    CONTAINER_DASH,
    CONTAINER_HLS,
    CONTAINER_TS,
    effective_guide_cap,
    for_device,
    known_kinds,
)

from .conftest import ADMIN_HEADERS, SAMPLE_M3U

# --- registry ----------------------------------------------------------------------------------


def test_android_tv_plays_ts_and_hls_but_not_dash() -> None:
    caps = for_device("android_tv")
    assert caps.supports(CONTAINER_TS)
    assert caps.supports(CONTAINER_HLS)
    assert not caps.supports(CONTAINER_DASH)


def test_apple_tv_is_hls_only() -> None:
    caps = for_device("apple_tv")
    assert caps.supports(CONTAINER_HLS)
    assert not caps.supports(CONTAINER_TS)


def test_desktop_plays_everything() -> None:
    caps = for_device("desktop")
    assert {CONTAINER_TS, CONTAINER_HLS, CONTAINER_DASH} <= caps.containers


def test_unknown_kind_falls_back_to_the_conservative_default() -> None:
    assert for_device("toaster").containers == for_device("other").containers
    assert for_device(None).supports(CONTAINER_TS)
    assert "other" in known_kinds()


def test_guide_cap_is_the_smaller_of_operator_and_client() -> None:
    assert effective_guide_cap("desktop", 5_000_000) == 5_000_000
    assert effective_guide_cap("mobile", 5_000_000) == 4_000_000
    assert effective_guide_cap("apple_tv", 1_000_000) == 1_000_000
    # configured 0 means "no operator cap", the client cap still applies
    assert effective_guide_cap("mobile", 0) == 4_000_000
    assert effective_guide_cap("desktop", 0) == 0


# --- delivery ----------------------------------------------------------------------------------


async def _import_and_devices(client: httpx.AsyncClient) -> dict[str, str]:
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
    tokens: dict[str, str] = {}
    for kind in ("desktop", "android_tv", "apple_tv", "mobile"):
        device = await client.post(
            "/api/v1/devices", json={"name": f"TV {kind}", "kind": kind}, headers=ADMIN_HEADERS
        )
        tokens[kind] = device.json()["token"]
    return tokens


@pytest.mark.asyncio
async def test_playlist_only_advertises_playable_containers(
    client: httpx.AsyncClient,
) -> None:
    tokens = await _import_and_devices(client)

    def channel_count(playlist: str) -> int:
        return playlist.count("#EXTINF")

    # SAMPLE_M3U: three .ts channels and one .m3u8 channel.
    desktop = (await client.get(f"/p/{tokens['desktop']}/live.m3u")).text
    android = (await client.get(f"/p/{tokens['android_tv']}/live.m3u")).text
    apple = (await client.get(f"/p/{tokens['apple_tv']}/live.m3u")).text
    mobile = (await client.get(f"/p/{tokens['mobile']}/live.m3u")).text

    assert channel_count(desktop) == 4
    assert channel_count(android) == 4
    # AVPlayer and phone players get only the HLS channel.
    assert channel_count(apple) == 1
    assert channel_count(mobile) == 1
    assert ".m3u8" in apple


@pytest.mark.asyncio
async def test_resolver_refuses_a_container_the_client_cannot_play(
    client: httpx.AsyncClient,
) -> None:
    tokens = await _import_and_devices(client)
    channels = (await client.get("/api/v1/channels", headers=ADMIN_HEADERS)).json()["items"]
    by_name = {row["name"]: row for row in channels}

    ts_key = by_name["BBC One HD"]["public_key"]
    hls_key = by_name["Eurosport 1 [HD]"]["public_key"]

    refused = await client.get(f"/p/{tokens['apple_tv']}/s/{ts_key}.ts")
    assert refused.status_code == 503

    allowed = await client.get(f"/p/{tokens['apple_tv']}/s/{hls_key}.m3u8")
    assert allowed.status_code in {302, 307}

    # The same TS channel is fine for a desktop and for Android TV.
    assert (await client.get(f"/p/{tokens['desktop']}/s/{ts_key}.ts")).status_code in {302, 307}
    assert (await client.get(f"/p/{tokens['android_tv']}/s/{ts_key}.ts")).status_code in {302, 307}
