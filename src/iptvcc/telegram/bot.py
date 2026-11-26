"""Long-polling Telegram bot.

Commands are read-only except ``/check``, which runs one probe round. Access is limited to the
allowlist in ``telegram_user`` (bootstrapped from ``IPTV_TELEGRAM_OWNER_ID``). No inbound port and
no webhook are needed, which is what makes this work behind the existing infrastructure.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import get_settings
from ..models import (
    Assignment,
    Channel,
    ChannelEpgMap,
    Device,
    EpgSource,
    Programme,
    SchedulerState,
    Source,
    TelegramUser,
)
from ..services.health import run_probe_round
from .client import TelegramClient, TelegramError
from .notifications import Problem, collect_problems, run_notification_scan

logger = logging.getLogger(__name__)

COMMANDS: list[dict[str, str]] = [
    {"command": "start", "description": "Сводка и начало работы"},
    {"command": "status", "description": "Состояние системы"},
    {"command": "channels", "description": "Каналы"},
    {"command": "sources", "description": "Источники"},
    {"command": "problems", "description": "Проблемы"},
    {"command": "devices", "description": "Устройства"},
    {"command": "subscriptions", "description": "Подписки"},
    {"command": "report", "description": "Отчёт"},
    {"command": "check", "description": "Проверить потоки сейчас"},
    {"command": "help", "description": "Команды"},
]

MAX_TEXT = 3800


def _clip(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[: MAX_TEXT - 1] + "…"


def _when(epoch: int | None) -> str:
    if not epoch:
        return "—"
    return datetime.fromtimestamp(epoch, UTC).strftime("%d.%m %H:%M UTC")


class TelegramBot:
    def __init__(
        self,
        client: TelegramClient,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        allow_ids: set[int] | None = None,
        poll_timeout: int | None = None,
        notify_interval: int | None = None,
        dedupe_hours: int | None = None,
    ) -> None:
        settings = get_settings()
        self._client = client
        self._factory = session_factory
        self._allow_ids = allow_ids
        self._poll_timeout = poll_timeout or settings.telegram_poll_timeout
        self._notify_interval = notify_interval or settings.telegram_notify_interval_seconds
        self._dedupe_hours = dedupe_hours or settings.telegram_dedupe_hours
        self._offset: int | None = None
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.last_scan_at: float = 0.0

    # --- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self.run_forever(), name="telegram-bot")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    async def run_forever(self) -> None:
        logger.info("telegram bot started (long polling)")
        with contextlib.suppress(TelegramError):
            await self._client.set_my_commands(COMMANDS)
        while not self._stopping.is_set():
            try:
                await self.poll_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("telegram poll failed")
                await asyncio.sleep(5)
            if time.monotonic() - self.last_scan_at >= self._notify_interval:
                try:
                    await self.scan_notifications()
                except Exception:
                    logger.exception("telegram notification scan failed")
                self.last_scan_at = time.monotonic()

    # --- polling -----------------------------------------------------------------------------

    async def poll_once(self) -> int:
        updates = await self._client.get_updates(self._offset, self._poll_timeout)
        for update in updates:
            # The offset must advance even for an update we cannot handle, or one malformed element
            # wedges the poller: it re-fetches the same batch forever and never moves on.
            if isinstance(update, dict):
                try:
                    update_id = int(update.get("update_id") or 0)
                except (TypeError, ValueError):
                    update_id = 0
                if update_id:
                    self._offset = update_id + 1
            try:
                handled = await self.handle_update(update)
            except Exception:
                logger.exception("telegram update failed")
                continue
            if handled is None:
                continue
            chat_id, reply = handled
            try:
                await self._client.send_message(chat_id, reply)
            except TelegramError:
                logger.warning("could not deliver reply to chat %s", chat_id)
        return len(updates)

    async def handle_update(self, update: dict[str, Any]) -> tuple[int, str] | None:
        if not isinstance(update, dict):
            return None
        message = update.get("message") or {}
        if not isinstance(message, dict):
            return None
        chat_id = (message.get("chat") or {}).get("id")
        text = (message.get("text") or "").strip()
        sender = message.get("from") or {}
        if not chat_id or not text.startswith("/"):
            return None
        try:
            chat_id = int(chat_id)
            sender_id = int(sender.get("id", chat_id))
        except (TypeError, ValueError):
            # A chat id that is not a number cannot be replied to.
            return None

        async with self._factory() as session:
            if not await self._allowed(session, sender_id, sender.get("username")):
                await session.commit()
                return chat_id, "Нет доступа. Этот чат не в списке разрешённых."

            command, _, argument = text.partition(" ")
            command = command.split("@", 1)[0].lower()
            reply = await self.dispatch(command, argument.strip(), session)
            await session.commit()
        return chat_id, _clip(reply)

    async def _allowed(self, session: AsyncSession, user_id: int, username: str | None) -> bool:
        if self._allow_ids is not None:
            return user_id in self._allow_ids
        if await session.get(TelegramUser, user_id) is not None:
            return True
        owner = get_settings().telegram_owner_id
        if owner is not None and user_id == owner:
            session.add(TelegramUser(telegram_id=user_id, username=username, role="owner"))
            await session.flush()
            return True
        return False

    # --- commands ----------------------------------------------------------------------------

    async def dispatch(self, command: str, argument: str, session: AsyncSession) -> str:
        handlers = {
            "/start": self.cmd_start,
            "/help": self.cmd_help,
            "/status": self.cmd_status,
            "/channels": self.cmd_channels,
            "/sources": self.cmd_sources,
            "/problems": self.cmd_problems,
            "/devices": self.cmd_devices,
            "/subscriptions": self.cmd_subscriptions,
            "/report": self.cmd_report,
            "/check": self.cmd_check,
        }
        handler = handlers.get(command)
        if handler is None:
            return "Неизвестная команда. /help — список."
        return await handler(argument, session)

    async def cmd_start(self, _argument: str, session: AsyncSession) -> str:
        channels = await session.scalar(select(func.count()).select_from(Channel)) or 0
        sources = await session.scalar(select(func.count()).select_from(Source)) or 0
        devices = await session.scalar(select(func.count()).select_from(Device)) or 0
        return (
            "IPTV Control Center\n\n"
            f"Каналы: {channels} · Источники: {sources} · Устройства: {devices}\n\n"
            "/status — состояние, /problems — проблемы, /report — отчёт.\n"
            "/help — все команды."
        )

    async def cmd_help(self, _argument: str, _session: AsyncSession) -> str:
        lines = ["Команды:"]
        lines += [f"/{item['command']} — {item['description']}" for item in COMMANDS]
        lines.append("")
        lines.append(
            "Видео идёт напрямую от провайдера; сервер управляет только каталогом "
            "и выбором источника."
        )
        return "\n".join(lines)

    async def cmd_status(self, _argument: str, session: AsyncSession) -> str:
        channels = await session.scalar(select(func.count()).select_from(Channel)) or 0
        sources = (await session.scalars(select(Source))).all()
        devices = await session.scalar(select(func.count()).select_from(Device)) or 0
        state = await session.get(SchedulerState, 1)
        epg_sources = await session.scalar(select(func.count()).select_from(EpgSource)) or 0
        programmes = await session.scalar(select(func.count()).select_from(Programme)) or 0
        mapped = await session.scalar(select(func.count()).select_from(ChannelEpgMap)) or 0

        healthy = sum(1 for source in sources if source.last_status == "success")
        scheduler = "нет отчёта"
        if state is not None and state.last_tick_epoch:
            scheduler = (
                f"раундов {state.rounds}, проверок {state.probed_total}, "
                f"последний {_when(state.last_tick_epoch)}"
            )
        return (
            "📊 Состояние\n\n"
            f"Каналы: {channels}\n"
            f"Источники: {healthy}/{len(sources)} без ошибок\n"
            f"Устройства: {devices}\n"
            f"Планировщик: {scheduler}\n"
            f"EPG: источников {epg_sources}, программ {programmes}, привязано каналов {mapped}"
        )

    async def cmd_channels(self, argument: str, session: AsyncSession) -> str:
        limit = 10
        if argument.isdigit():
            limit = max(1, min(50, int(argument)))
        rows = (
            await session.scalars(
                select(Channel)
                .where(Channel.is_hidden.is_(False))
                .order_by(Channel.number, Channel.id)
                .limit(limit)
            )
        ).all()
        total = await session.scalar(select(func.count()).select_from(Channel)) or 0
        if not rows:
            return "Каналов нет. Добавьте источник и импортируйте плейлист."
        lines = [f"📺 Каналы ({len(rows)} из {total})", ""]
        for channel in rows:
            number = int(channel.number) if channel.number is not None else "—"
            lines.append(f"{number}. {channel.display_name}")
        return "\n".join(lines)

    async def cmd_sources(self, _argument: str, session: AsyncSession) -> str:
        rows = (await session.scalars(select(Source).order_by(Source.id))).all()
        if not rows:
            return "Источников нет."
        lines = ["🗂 Источники", ""]
        for source in rows:
            status = {"success": "🟢", "error": "🔴"}.get(source.last_status or "", "⚪")
            lines.append(f"{status} {source.name} · потоков {source.stream_count}")
        return "\n".join(lines)

    async def cmd_problems(self, _argument: str, session: AsyncSession) -> str:
        problems: list[Problem] = await collect_problems(session)
        if not problems:
            return "🟢 Проблем не обнаружено."
        lines = [f"⚠️ Проблемы ({len(problems)})", ""]
        lines += [problem.text for problem in problems]
        return "\n".join(lines)

    async def cmd_devices(self, _argument: str, session: AsyncSession) -> str:
        rows = (
            await session.execute(
                select(Device, Assignment).join(Assignment, Assignment.device_id == Device.id)
            )
        ).all()
        if not rows:
            return "Устройств нет."
        lines = ["📱 Устройства", ""]
        for device, assignment in rows:
            lines.append(f"{device.name} · {device.kind} · токен {assignment.token_prefix}…")
        return "\n".join(lines)

    async def cmd_subscriptions(self, _argument: str, session: AsyncSession) -> str:
        rows = (
            await session.scalars(select(Source).where(Source.subscription_expires_at.is_not(None)))
        ).all()
        if not rows:
            return "Данных о подписках нет: сроки не указаны ни у одного источника."
        soon: list[str] = []
        for source in rows:
            expires = source.subscription_expires_at
            if expires is None:
                continue
            aware = expires if expires.tzinfo is not None else expires.replace(tzinfo=UTC)
            days_left = (aware - datetime.now(UTC)).days
            soon.append(f"{source.name}: осталось {days_left} дн.")
        return "🗓 Подписки\n\n" + "\n".join(soon) if soon else "Подписок нет."

    async def cmd_report(self, _argument: str, session: AsyncSession) -> str:
        channels = await session.scalar(select(func.count()).select_from(Channel)) or 0
        sources = (await session.scalars(select(Source))).all()
        devices = await session.scalar(select(func.count()).select_from(Device)) or 0
        state = await session.get(SchedulerState, 1)
        problems = await collect_problems(session)
        healthy = sum(1 for source in sources if source.last_status == "success")

        lines = [
            "📊 Отчёт за сутки",
            "",
            f"Каналы: {channels}",
            f"Источники: {len(sources)} (без ошибок {healthy})",
            f"Устройства: {devices}",
        ]
        if state is not None:
            lines.append(f"Проверок всего: {state.probed_total}, раундов {state.rounds}")
            if state.last_round_epoch:
                lines.append(f"Последний раунд: {_when(state.last_round_epoch)}")
        lines.append(f"Проблем сейчас: {len(problems)}")
        if problems:
            lines.append("")
            lines += [problem.text for problem in problems[:3]]
        lines.append("")
        lines.append(f"Отчёт сформирован {datetime.now(UTC).strftime('%d.%m %H:%M UTC')}")
        return "\n".join(lines)

    async def cmd_check(self, _argument: str, session: AsyncSession) -> str:
        stats = await run_probe_round(session, limit=20)
        return (
            "🔎 Проверка запущена\n\n"
            f"Проверено: {stats['probed']}, успешно: {stats['ok']}, ошибок: {stats['failed']}"
        )

    # --- notifications -----------------------------------------------------------------------

    async def scan_notifications(self) -> dict[str, int]:
        async with self._factory() as session:
            stats = await run_notification_scan(
                session, self._client, dedupe_hours=self._dedupe_hours
            )
            await session.commit()
        return stats


__all__ = ["COMMANDS", "TelegramBot"]
