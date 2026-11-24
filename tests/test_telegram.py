"""Telegram bot: adapter errors, allowlist, commands, polling and notification dedupe."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx  # noqa: F401 - ensures the dependency is present for the bot client
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from iptvcc.models import (
    Assignment,
    Channel,
    Device,
    PlaylistProfile,
    Source,
    TelegramUser,
)
from iptvcc.telegram import TelegramBot, TelegramClient, collect_problems, run_notification_scan
from iptvcc.telegram.client import TelegramError

OWNER = 7


class FakeApi(ThreadingHTTPServer):
    """Minimal Bot API stand-in that records every call."""

    calls: list[tuple[str, dict[str, Any]]]
    updates: list[list[dict[str, Any]]]
    fail_methods: set[str]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.calls = []
        self.updates = []
        self.fail_methods = set()

    def record(self, method: str, payload: dict[str, Any]) -> Any:
        self.calls.append((method, payload))
        if method == "getUpdates":
            return self.updates.pop(0) if self.updates else []
        if method == "sendMessage":
            return {"message_id": len(self.calls)}
        if method == "getMe":
            return {"id": 1, "is_bot": True, "first_name": "bot", "username": "test_bot"}
        return True


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("content-length", "0") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        method = self.path.rsplit("/", 1)[-1]
        api: FakeApi = self.server  # type: ignore[assignment]
        failing = method in api.fail_methods
        result = None if failing else api.record(method, payload)
        body = json.dumps(
            {"ok": False, "description": "boom"} if failing else {"ok": True, "result": result}
        ).encode()
        self.send_response(400 if failing else 200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def fake_api() -> Iterator[FakeApi]:
    server = FakeApi(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _client(api: FakeApi) -> TelegramClient:
    return TelegramClient("TEST", base_url=f"http://127.0.0.1:{api.server_port}", timeout=5)


def _bot(
    api: FakeApi, factory: async_sessionmaker[AsyncSession], *, allow: set[int] | None = None
) -> TelegramBot:
    return TelegramBot(_client(api), factory, allow_ids=allow, poll_timeout=1, dedupe_hours=6)


def _update(text: str, *, user: int = OWNER, update_id: int = 1) -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"chat": {"id": user}, "from": {"id": user, "username": "owner"}, "text": text},
    }


async def _seed(session_factory: async_sessionmaker[AsyncSession]) -> None:
    async with session_factory() as session:
        session.add(
            Source(
                name="Provider A", url="http://p.test/a.m3u", last_status="success", stream_count=3
            )
        )
        profile = PlaylistProfile(name="Default", is_default=True)
        session.add(profile)
        await session.flush()
        session.add(Channel(public_key="pk-bbc", name="BBC", display_name="BBC One HD", number=1))
        device = Device(name="Living Room", kind="android_tv")
        session.add(device)
        await session.flush()
        session.add(
            Assignment(
                device_id=device.id,
                playlist_profile_id=profile.id,
                token_hash="hash-1",
                token_prefix="abc12345",
            )
        )
        session.add(TelegramUser(telegram_id=OWNER))
        await session.commit()


# --- adapter -----------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_surfaces_api_errors(fake_api: FakeApi) -> None:
    fake_api.fail_methods.add("getMe")
    with pytest.raises(TelegramError, match="boom"):
        await _client(fake_api).get_me()


@pytest.mark.asyncio
async def test_send_message_payload(fake_api: FakeApi) -> None:
    await _client(fake_api).send_message(11, "hello")
    method, payload = fake_api.calls[-1]
    assert method == "sendMessage"
    assert payload["chat_id"] == 11
    assert payload["text"] == "hello"


# --- access ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_chat_is_denied(
    session_factory: async_sessionmaker[AsyncSession], fake_api: FakeApi
) -> None:
    bot = _bot(fake_api, session_factory, allow={OWNER})
    handled = await bot.handle_update(_update("/status", user=99))
    assert handled is not None
    assert "Нет доступа" in handled[1]


@pytest.mark.asyncio
async def test_owner_bootstrapped_from_settings(
    session_factory: async_sessionmaker[AsyncSession], fake_api: FakeApi, monkeypatch
) -> None:
    from iptvcc.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "telegram_owner_id", OWNER, raising=False)
    bot = _bot(fake_api, session_factory)  # no explicit allowlist
    handled = await bot.handle_update(_update("/start"))
    assert handled is not None
    assert "IPTV Control Center" in handled[1]


# --- commands ----------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("/start", "IPTV Control Center"),
        ("/help", "Команды"),
        ("/status", "Состояние"),
        ("/channels", "BBC One HD"),
        ("/sources", "Provider A"),
        ("/devices", "Living Room"),
        ("/problems", "Проблем"),
        ("/report", "Отчёт"),
        ("/subscriptions", "подпис"),
        ("/check", "Проверено"),
        ("/nonsense", "Неизвестная команда"),
    ],
)
async def test_commands_reply(
    session_factory: async_sessionmaker[AsyncSession],
    fake_api: FakeApi,
    command: str,
    expected: str,
) -> None:
    await _seed(session_factory)
    bot = _bot(fake_api, session_factory, allow={OWNER})
    handled = await bot.handle_update(_update(command))
    assert handled is not None
    assert expected in handled[1]


@pytest.mark.asyncio
async def test_poll_once_delivers_the_reply(
    session_factory: async_sessionmaker[AsyncSession], fake_api: FakeApi
) -> None:
    await _seed(session_factory)
    fake_api.updates = [[_update("/status")]]
    bot = _bot(fake_api, session_factory, allow={OWNER})

    assert await bot.poll_once() == 1
    sends = [payload for method, payload in fake_api.calls if method == "sendMessage"]
    assert sends
    assert sends[0]["chat_id"] == OWNER
    assert "Состояние" in sends[0]["text"]


# --- notifications -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_problem_alert_is_deduped_then_recovered(
    session_factory: async_sessionmaker[AsyncSession], fake_api: FakeApi
) -> None:
    async with session_factory() as session:
        session.add(
            Source(
                name="Broken", url="http://p.test/b.m3u", last_status="error", last_error="timeout"
            )
        )
        session.add(TelegramUser(telegram_id=OWNER))
        await session.commit()

    client = _client(fake_api)
    async with session_factory() as session:
        first = await run_notification_scan(session, client, dedupe_hours=6)
        await session.commit()
    assert first["sent"] >= 1
    texts = [payload["text"] for method, payload in fake_api.calls if method == "sendMessage"]
    assert any("Broken" in text for text in texts)

    async with session_factory() as session:
        second = await run_notification_scan(session, client, dedupe_hours=6)
        await session.commit()
    assert second["sent"] == 0  # nothing new within the dedupe window

    async with session_factory() as session:
        row = await session.scalar(select(Source).limit(1))
        assert row is not None
        row.last_status = "success"
        await session.commit()

    async with session_factory() as session:
        third = await run_notification_scan(session, client, dedupe_hours=6)
        await session.commit()
    assert third["recovered"] >= 1
    recoveries = [payload["text"] for method, payload in fake_api.calls if method == "sendMessage"]
    assert any("Восстановлено" in text for text in recoveries)


@pytest.mark.asyncio
async def test_subscription_expiry_is_a_problem(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        session.add(
            Source(
                name="Soon",
                url="http://p.test/s.m3u",
                last_status="success",
                subscription_expires_at=datetime.now(UTC) + timedelta(days=3),
            )
        )
        await session.commit()

    async with session_factory() as session:
        problems = await collect_problems(session)
    assert any("истекает" in problem.text for problem in problems)


@pytest.mark.asyncio
async def test_no_chats_means_no_delivery(
    session_factory: async_sessionmaker[AsyncSession], fake_api: FakeApi
) -> None:
    async with session_factory() as session:
        session.add(Source(name="Alone", url="http://p.test/a.m3u", last_status="error"))
        await session.commit()

    async with session_factory() as session:
        stats = await run_notification_scan(session, _client(fake_api), dedupe_hours=6)
        await session.commit()
    assert stats == {"sent": 0, "recovered": 0, "chats": 0}
