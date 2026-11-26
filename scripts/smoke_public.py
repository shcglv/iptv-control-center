"""Post-deployment smoke check for a public base URL.

Answers "is this deployment actually serving?" without credentials and without touching the server:

    .\\.venv\\Scripts\\python.exe scripts\\smoke_public.py --base https://iptv.joxit.ru

Checks the health endpoint, the login page, a built asset, security headers, response times and the
TLS certificate's remaining life. Exit code 0 means everything answered as expected.
"""

from __future__ import annotations

import argparse
import json
import socket
import ssl
import time
from datetime import UTC, datetime
from urllib.parse import urljoin, urlparse

import httpx

OK = "ok"
WARN = "warn"
FAIL = "fail"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def add(self, name: str, status: str, detail: str) -> None:
        self.rows.append((name, status, detail))
        print(f"  [{status:<4}] {name:<22} {detail}")

    def failed(self) -> bool:
        return any(status == FAIL for _name, status, _detail in self.rows)


def check_health(client: httpx.Client, base: str, report: Report) -> None:
    started = time.perf_counter()
    try:
        response = client.get(urljoin(base, "/healthz"))
        elapsed = (time.perf_counter() - started) * 1000
    except httpx.HTTPError as exc:
        report.add("healthz", FAIL, f"{type(exc).__name__}: {exc}")
        return
    if response.status_code != 200:
        report.add("healthz", FAIL, f"HTTP {response.status_code}")
        return
    try:
        body = response.json()
    except json.JSONDecodeError:
        report.add("healthz", FAIL, "not JSON")
        return
    if body.get("status") != "ok":
        report.add("healthz", FAIL, f"status={body.get('status')!r}")
        return
    status = OK if elapsed < 1000 else WARN
    report.add("healthz", status, f"200 in {elapsed:.0f} ms")


def check_login_page(client: httpx.Client, base: str, report: Report) -> str | None:
    try:
        response = client.get(urljoin(base, "/"))
    except httpx.HTTPError as exc:
        report.add("login page", FAIL, f"{type(exc).__name__}: {exc}")
        return None
    if response.status_code != 200:
        report.add("login page", FAIL, f"HTTP {response.status_code}")
        return None
    html = response.text
    if '<div id="root">' not in html and 'id="root"' not in html:
        report.add("login page", FAIL, "no application mount point")
        return None
    report.add("login page", OK, f"{len(html)} bytes")
    return html


def check_assets(client: httpx.Client, base: str, html: str | None, report: Report) -> None:
    if not html:
        report.add("assets", WARN, "skipped, no page")
        return
    index = html.find("/assets/")
    if index == -1:
        report.add("assets", WARN, "no /assets/ reference in the page")
        return
    end = index
    while end < len(html) and html[end] not in "\"' ":
        end += 1
    asset = html[index:end]
    try:
        response = client.get(urljoin(base, asset))
    except httpx.HTTPError as exc:
        report.add("assets", FAIL, f"{type(exc).__name__}: {exc}")
        return
    if response.status_code != 200 or not response.content:
        report.add("assets", FAIL, f"{asset} → HTTP {response.status_code}")
        return
    report.add("assets", OK, f"{asset} ({len(response.content) // 1024} KiB)")


def check_headers(client: httpx.Client, base: str, report: Report) -> None:
    try:
        response = client.get(urljoin(base, "/healthz"))
    except httpx.HTTPError as exc:
        report.add("security headers", FAIL, f"{type(exc).__name__}: {exc}")
        return
    expected = ["x-content-type-options", "x-frame-options", "referrer-policy"]
    missing = [name for name in expected if name not in response.headers]
    if missing:
        report.add("security headers", WARN, "missing " + ", ".join(missing))
    else:
        report.add("security headers", OK, ", ".join(expected))


def check_tls(base: str, report: Report) -> None:
    parsed = urlparse(base)
    if parsed.scheme != "https":
        report.add("tls", WARN, "not an https base URL")
        return
    host = parsed.hostname or ""
    port = parsed.port or 443
    context = ssl.create_default_context()
    try:
        with (
            socket.create_connection((host, port), timeout=10) as raw,
            context.wrap_socket(raw, server_hostname=host) as tls,
        ):
            cert = tls.getpeercert()
    except (OSError, ssl.SSLError) as exc:
        report.add("tls", FAIL, f"{type(exc).__name__}: {exc}")
        return
    not_after = cert.get("notAfter", "")
    try:
        expires = datetime.strptime(not_after, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=UTC)
    except ValueError:
        report.add("tls", WARN, f"cannot parse expiry {not_after!r}")
        return
    days = (expires - datetime.now(UTC)).days
    subject = dict(item[0] for item in cert.get("subject", ()))
    common_name = subject.get("commonName", host)
    status = OK if days > 21 else WARN
    report.add("tls", status, f"{common_name}, {days} days left")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True, help="e.g. https://iptv.joxit.ru")
    parser.add_argument("--timeout", type=float, default=15.0)
    args = parser.parse_args()
    base = args.base.rstrip("/") + "/"

    print(f"smoke check against {base}")
    report = Report()
    with httpx.Client(timeout=args.timeout, trust_env=False, follow_redirects=True) as client:
        check_health(client, base, report)
        html = check_login_page(client, base, report)
        check_assets(client, base, html, report)
        check_headers(client, base, report)
    check_tls(base, report)

    failures = [row for row in report.rows if row[1] == FAIL]
    warnings = [row for row in report.rows if row[1] == WARN]
    if failures:
        print(f"\nRESULT: {len(failures)} FAILED, {len(warnings)} warning(s)")
        return 1
    print(f"\nRESULT: SMOKE PASSED ({len(report.rows)} checks, {len(warnings)} warning(s))")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
