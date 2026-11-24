"""Outbound network guard.

Any URL that comes from a user or a forum is untrusted. ``validate_url`` refuses non-HTTP
schemes, credentials in the URL, and any host that resolves to a non-global address
(loopback, RFC1918, link-local, CGNAT, multicast, reserved, metadata endpoints).
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx

MAX_IMPORT_BYTES = 50 * 1024 * 1024
DEFAULT_TIMEOUT = 20.0

_BLOCKED_HOSTNAMES = {
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
    "metadata",
}


class UnsafeUrlError(ValueError):
    """Raised when a URL must not be fetched."""


def _is_blocked_ip(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in ipaddress.ip_network("100.64.0.0/10")  # CGNAT
    )


def validate_url(url: str) -> None:
    """Validate scheme, shape and host *before* any connection is made."""
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise UnsafeUrlError("only http(s) URLs are allowed")
    if not parts.hostname:
        raise UnsafeUrlError("URL has no host")
    if parts.username or parts.password:
        raise UnsafeUrlError("credentials in the URL are not allowed here")
    if parts.hostname.lower() in _BLOCKED_HOSTNAMES:
        raise UnsafeUrlError("metadata hostnames are never allowed")


async def resolve_and_check(url: str) -> None:
    """Resolve the host and refuse if any resolved address is non-global.

    ``IPTV_ALLOW_PRIVATE_NETWORK_TARGETS`` relaxes the address check for LAN sources; metadata
    hostnames stay forbidden either way.
    """
    validate_url(url)
    from .config import get_settings

    allow_private = get_settings().allow_private_network_targets
    host = urlsplit(url).hostname or ""
    loop = asyncio.get_running_loop()
    try:
        infos = await loop.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"cannot resolve host: {host}") from exc
    if not infos:
        raise UnsafeUrlError("host did not resolve")
    for info in infos:
        address = str(info[4][0])
        if _is_blocked_ip(address) and not allow_private:
            raise UnsafeUrlError(f"host resolves to a non-global address: {address}")


async def fetch_text(
    url: str, *, max_bytes: int = MAX_IMPORT_BYTES, timeout: float = DEFAULT_TIMEOUT
) -> str:
    """Fetch a playlist/EPG document with SSRF checks, size limit and redirect re-check."""
    await resolve_and_check(url)
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
        trust_env=False,  # explicit proxy only; ambient HTTP_PROXY is not part of the contract
        headers={"User-Agent": "iptv-control-center/0.1 (+self-hosted)"},
    ) as client:
        current = url
        for _hop in range(4):
            response = await client.get(current)
            if response.is_redirect:
                location = response.headers.get("location", "")
                if not location:
                    raise UnsafeUrlError("redirect without a location")
                current = str(response.url.join(location))
                await resolve_and_check(current)
                continue
            response.raise_for_status()
            content_length = response.headers.get("content-length")
            if content_length and int(content_length) > max_bytes:
                raise UnsafeUrlError("document is too large")
            body = response.content
            if len(body) > max_bytes:
                raise UnsafeUrlError("document is too large")
            return body.decode(response.encoding or "utf-8", errors="replace")
    raise UnsafeUrlError("too many redirects")
