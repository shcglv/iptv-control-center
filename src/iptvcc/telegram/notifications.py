"""Push notifications.

A scan compares the current set of problems with what was last delivered. Each problem has a stable
``dedupe_key``; a problem is sent at most once per dedupe window, and when it disappears a recovery
message is sent once.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import Channel, ChannelEpgMap, Notification, SchedulerState, Source, TelegramUser
from .client import TelegramClient, TelegramError

EXPIRY_WARNING_DAYS = 7


@dataclass(frozen=True)
class Problem:
    key: str
    severity: str  # info | warning | error
    text: str


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


async def collect_problems(session: AsyncSession) -> list[Problem]:
    problems: list[Problem] = []
    now = datetime.now(UTC)

    for source in (await session.scalars(select(Source))).all():
        if source.last_status == "error":
            problems.append(
                Problem(
                    key=f"source:{source.id}:error",
                    severity="error",
                    text=f"🔴 Источник «{source.name}»: {source.last_error or 'ошибка импорта'}",
                )
            )
        if source.subscription_expires_at is not None:
            expires = _aware(source.subscription_expires_at)
            days_left = (expires - now).days
            if 0 <= days_left <= EXPIRY_WARNING_DAYS:
                problems.append(
                    Problem(
                        key=f"source:{source.id}:expiry",
                        severity="warning",
                        text=f"🟡 Подписка «{source.name}» истекает через {days_left} дн.",
                    )
                )

    state = await session.get(SchedulerState, 1)
    tick = get_settings().scheduler_tick_seconds
    stale_after = max(3 * tick, 300)
    if state is None or state.last_tick_epoch is None:
        problems.append(
            Problem("scheduler:never", "warning", "🟡 Планировщик проверок ещё не запускался")
        )
    elif int(time.time()) - state.last_tick_epoch > stale_after:
        problems.append(
            Problem(
                "scheduler:stale",
                "warning",
                "🟡 Планировщик проверок не отчитывается дольше ожидаемого",
            )
        )
    if state is not None and state.last_error:
        problems.append(
            Problem("scheduler:error", "error", f"🔴 Ошибка планировщика: {state.last_error[:200]}")
        )

    channels = await session.scalar(select(func.count()).select_from(Channel)) or 0
    mapped = len((await session.scalars(select(ChannelEpgMap.channel_id))).all())
    if channels and mapped == 0:
        problems.append(Problem("epg:none", "info", "🔵 Ни один канал не привязан к EPG"))

    return problems


async def notification_chat_ids(session: AsyncSession) -> list[int]:
    now = datetime.now(UTC)
    rows = (await session.scalars(select(TelegramUser))).all()
    return [
        row.telegram_id for row in rows if row.muted_until is None or _aware(row.muted_until) <= now
    ]


async def run_notification_scan(
    session: AsyncSession,
    client: TelegramClient,
    *,
    dedupe_hours: int = 6,
) -> dict[str, int]:
    """Deliver new problems and recoveries. Returns counters."""
    problems = await collect_problems(session)
    current = {problem.key: problem for problem in problems}
    chats = await notification_chat_ids(session)
    if not chats:
        return {"sent": 0, "recovered": 0, "chats": 0}

    recent = (
        await session.scalars(select(Notification).order_by(Notification.id.desc()).limit(200))
    ).all()
    latest: dict[str, Notification] = {}
    for row in recent:
        latest.setdefault(row.dedupe_key, row)

    cutoff = datetime.now(UTC) - timedelta(hours=dedupe_hours)
    sent = 0
    recovered = 0

    for key, problem in current.items():
        previous = latest.get(key)
        if (
            previous is not None
            and previous.kind == "problem"
            and _aware(previous.created_at) >= cutoff
        ):
            continue
        delivered = await _broadcast(client, chats, problem.text)
        session.add(
            Notification(
                kind="problem",
                severity=problem.severity,
                dedupe_key=key,
                text=problem.text,
                delivered_at=datetime.now(UTC) if delivered else None,
                error=None if delivered else "delivery failed",
            )
        )
        if delivered:
            sent += 1

    for key, previous in latest.items():
        if previous.kind != "problem" or key in current:
            continue
        text = f"🟢 Восстановлено: {previous.text.lstrip('🔴🟡🔵 ')}"
        delivered = await _broadcast(client, chats, text)
        session.add(
            Notification(
                kind="recovery",
                severity="info",
                dedupe_key=key,
                text=text,
                delivered_at=datetime.now(UTC) if delivered else None,
                error=None if delivered else "delivery failed",
            )
        )
        if delivered:
            recovered += 1

    await session.flush()
    return {"sent": sent, "recovered": recovered, "chats": len(chats)}


async def _broadcast(client: TelegramClient, chats: list[int], text: str) -> bool:
    delivered = False
    for chat_id in chats:
        try:
            await client.send_message(chat_id, text)
            delivered = True
        except TelegramError:
            continue
    return delivered
