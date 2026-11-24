"""Serving the built SPA, and not shadowing the API."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

import iptvcc

STATIC = Path(iptvcc.__file__).parent / "static"
BUILT = (STATIC / "index.html").is_file()

pytestmark = pytest.mark.skipif(
    not BUILT, reason="web UI is not built (run: cd web && pnpm install && pnpm build)"
)


@pytest.mark.asyncio
async def test_root_serves_the_spa(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="root"' in response.text


@pytest.mark.asyncio
async def test_unknown_path_falls_back_to_the_spa(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.get("/devices")
    assert response.status_code == 200
    assert 'id="root"' in response.text


@pytest.mark.asyncio
async def test_hashed_assets_are_served(anon_client: httpx.AsyncClient) -> None:
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    assets = re.findall(r'(/assets/[^"]+)', index)
    assert assets, "index.html references no built assets"
    for path in assets:
        response = await anon_client.get(path)
        assert response.status_code == 200, path
        assert len(response.content) > 0


@pytest.mark.asyncio
async def test_api_paths_are_not_shadowed(anon_client: httpx.AsyncClient) -> None:
    # An unknown API path must stay a JSON 404, not the SPA shell.
    response = await anon_client.get("/api/v1/definitely-not-a-route")
    assert response.status_code == 404
    assert "text/html" not in response.headers.get("content-type", "")


@pytest.mark.asyncio
async def test_health_still_answers_alongside_the_spa(anon_client: httpx.AsyncClient) -> None:
    response = await anon_client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
