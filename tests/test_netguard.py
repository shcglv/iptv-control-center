"""SSRF guard: schemes, credentials, private ranges, metadata endpoints and the LAN switch."""

from __future__ import annotations

import pytest

from iptvcc.config import get_settings
from iptvcc.netguard import UnsafeUrlError, resolve_and_check, validate_url


@pytest.fixture(autouse=True)
def private_targets_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite enables LAN targets globally for its own local servers; these tests need the
    production default."""
    monkeypatch.setattr(get_settings(), "allow_private_network_targets", False, raising=False)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.test/list.m3u",
        "gopher://example.test/",
        "://missing-scheme",
        "http://user:secret@example.test/list.m3u",
        "https://user@example.test/list.m3u",
        "http:///no-host",
        "http://metadata.google.internal/computeMetadata/v1/",
        "http://metadata/computeMetadata/v1/",
        "http://instance-data/latest/",
    ],
)
def test_validate_url_rejects_dangerous_shapes(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.test/list.m3u",
        "https://provider.example/live/1.ts",
        "https://provider.example:8443/panel_api.php?username=u",
        "http://[2606:4700::1111]/x.m3u",
    ],
)
def test_validate_url_accepts_public_shapes(url: str) -> None:
    validate_url(url)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/",
        "http://localhost/",
        "http://[::1]/",
        "http://0.0.0.0/",
        "http://10.0.0.5/live.m3u",
        "http://192.168.1.10/live.m3u",
        "http://172.16.0.1/live.m3u",
        "http://100.64.0.1/live.m3u",  # CGNAT
    ],
)
async def test_private_targets_are_refused_by_default(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        await resolve_and_check(url)


@pytest.mark.asyncio
async def test_metadata_address_stays_blocked_even_with_lan_targets_allowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "allow_private_network_targets", True, raising=False)
    # A LAN address is the whole point of the switch...
    await resolve_and_check("http://10.0.0.5/live.m3u")
    # ...but the cloud metadata endpoint is never a legitimate LAN source.
    with pytest.raises(UnsafeUrlError):
        await resolve_and_check("http://169.254.169.254/latest/meta-data/")


@pytest.mark.asyncio
async def test_unresolvable_host_is_refused() -> None:
    with pytest.raises(UnsafeUrlError):
        await resolve_and_check("http://does-not-exist.invalid/list.m3u")


@pytest.mark.asyncio
async def test_provider_error_status_is_a_domain_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 403 from a provider must be a clean import error, not an unhandled server error.

    Before this, fetch_text called raise_for_status(), the httpx.HTTPStatusError escaped the import
    endpoint, and four sources in a real bulk import produced HTTP 500.
    """
    import httpx

    from iptvcc import netguard

    async def _allow(_url: str) -> None:
        return None

    monkeypatch.setattr(netguard, "resolve_and_check", _allow)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="denied")

    real_client = httpx.AsyncClient

    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(netguard.httpx, "AsyncClient", patched)
    with pytest.raises(UnsafeUrlError, match="HTTP 403"):
        await netguard.fetch_text("http://provider.test/list.m3u")
