"""Autonomous probe scheduling.

A single in-process task claims the scheduler lease, asks which streams are due, and runs one probe
round. Due-ness is age based:

* **hot** - streams of channels that served a device within 24 h → every
  ``scheduler_hot_interval_seconds`` (default 5 min);
* **cold** - everything else → every ``scheduler_cold_interval_seconds`` (default 1 h).

Profile-based tiers (T0-T4 from the plan) arrive with Playlist Studio, once channel visibility is
per profile; the interval mechanism is already the same.

The lease is a conditional UPDATE on a single row, so it is atomic on SQLite and PostgreSQL without
relying on database-specific advisory-lock functions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Any, cast

from sqlalchemy import CursorResult, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import get_settings
from ..models import Channel, ChannelStream, SchedulerState, Stream, StreamQuality
from .health import run_probe_round
from .source_refresh import refresh_due_sources

logger = logging.getLogger(__name__)

STATE_ID = 1


def _owner_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:6]}"


async def get_state(session: AsyncSession) -> SchedulerState:
    state = await session.get(SchedulerState, STATE_ID)
    if state is None:
        state = SchedulerState(id=STATE_ID, rounds=0, probed_total=0)
        session.add(state)
        await session.flush()
    return state


async def acquire_lease(session: AsyncSession, owner: str, ttl_seconds: int) -> bool:
    """Try to become the scheduler. Returns False when another process holds a live lease."""
    await get_state(session)
    now = int(time.time())
    result = cast(
        CursorResult[Any],
        await session.execute(
            update(SchedulerState)
            .where(
                SchedulerState.id == STATE_ID,
                or_(
                    SchedulerState.locked_until.is_(None),
                    SchedulerState.locked_until < now,
                    SchedulerState.owner == owner,
                ),
            )
            .values(owner=owner, locked_until=now + ttl_seconds)
        ),
    )
    return bool(result.rowcount)


async def release_lease(session: AsyncSession, owner: str) -> None:
    """Give the lease back if we still hold it, so a replacement can start probing immediately."""
    await session.execute(
        update(SchedulerState)
        .where(SchedulerState.owner == owner)
        .values(owner=None, locked_until=0)
    )
    await session.commit()


async def due_stream_ids(
    session: AsyncSession, *, batch_size: int, hot_seconds: int, cold_seconds: int
) -> list[int]:
    """Pick streams whose last check is older than their tier interval."""
    now = int(time.time())
    statement = (
        select(
            Stream.id,
            StreamQuality.last_checked_epoch,
            func.max(Channel.active_since_epoch).label("active_since"),
        )
        .outerjoin(StreamQuality, StreamQuality.stream_id == Stream.id)
        .outerjoin(ChannelStream, ChannelStream.stream_id == Stream.id)
        .outerjoin(Channel, Channel.id == ChannelStream.channel_id)
        .where(Stream.is_stale.is_(False))
        .group_by(Stream.id, StreamQuality.last_checked_epoch)
        .order_by(StreamQuality.last_checked_epoch.asc().nullsfirst(), Stream.id)
        .limit(2000)
    )
    rows = (await session.execute(statement)).all()

    due: list[int] = []
    for stream_id, last_checked, active_since in rows:
        active_epoch = int(active_since or 0)
        hot = active_epoch > 0 and (now - active_epoch) <= 86_400
        interval = hot_seconds if hot else cold_seconds
        last_epoch = int(last_checked or 0)
        if last_checked is None or (now - last_epoch) >= interval:
            due.append(stream_id)
            if len(due) >= batch_size:
                break
    return due


class ProbeScheduler:
    """Runs one probe round per tick while it holds the lease."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        tick_seconds: int | None = None,
        batch_size: int | None = None,
        lock_ttl_seconds: int | None = None,
        hot_interval_seconds: int | None = None,
        cold_interval_seconds: int | None = None,
        owner: str | None = None,
    ) -> None:
        settings = get_settings()
        self._factory = session_factory
        self._tick = tick_seconds or settings.scheduler_tick_seconds
        self._batch = batch_size or settings.scheduler_batch_size
        self._ttl = lock_ttl_seconds or settings.scheduler_lock_ttl_seconds
        self._hot = hot_interval_seconds or settings.scheduler_hot_interval_seconds
        self._cold = cold_interval_seconds or settings.scheduler_cold_interval_seconds
        self.owner = owner or _owner_id()
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()

    # --- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopping.clear()
            self._task = asyncio.create_task(self.run_forever(), name="probe-scheduler")

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        # Hand the lease back instead of letting it expire. Without this, a redeploy leaves the
        # replacement instance unable to probe for the full TTL — 180 seconds of silence after
        # every deploy, which looks like a broken scheduler.
        try:
            async with self._factory() as session:
                await release_lease(session, self.owner)
        except Exception:
            logger.warning("could not release the scheduler lease on shutdown", exc_info=True)

    async def run_forever(self) -> None:
        logger.info("probe scheduler started (owner=%s, tick=%ss)", self.owner, self._tick)
        while not self._stopping.is_set():
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("probe scheduler tick failed")
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self._tick)
            except TimeoutError:
                continue

    # --- one round ---------------------------------------------------------------------------

    async def tick(self) -> dict[str, int | bool]:
        async with self._factory() as session:
            acquired = await acquire_lease(session, self.owner, self._ttl)
            state = await get_state(session)
            now = int(time.time())
            state.last_tick_epoch = now
            if not acquired:
                await session.commit()
                return {"probed": 0, "ok": 0, "failed": 0, "skipped": True}

            try:
                ids = await due_stream_ids(
                    session, batch_size=self._batch, hot_seconds=self._hot, cold_seconds=self._cold
                )
                stats = await run_probe_round(session, stream_ids=ids)
                # Sources drift from the provider the moment an import finishes; a few per tick keep
                # the catalog current without a manual click.
                refreshed = await refresh_due_sources(session, limit=2)
                state.rounds = (state.rounds or 0) + 1
                state.probed_total = (state.probed_total or 0) + int(stats["probed"])
                state.last_round_epoch = now
                state.last_error = None
                await session.commit()
                return {
                    **stats,
                    "refreshed": refreshed["refreshed"],
                    "refresh_failed": refreshed["failed"],
                    "skipped": False,
                }
            except Exception as exc:
                state.last_error = f"{type(exc).__name__}: {exc}"[:500]
                await session.commit()
                raise


@dataclass(frozen=True)
class SchedulerStatus:
    running: bool
    healthy: bool
    owner: str | None
    last_tick_epoch: int | None
    seconds_since_tick: int | None
    stale_after_seconds: int
    rounds: int
    probed_total: int
    last_error: str | None


async def scheduler_status(
    session: AsyncSession, *, tick_seconds: int | None = None
) -> SchedulerStatus:
    """Watchdog view: is the loop alive, and how stale is the newest tick?"""
    settings = get_settings()
    tick = tick_seconds or settings.scheduler_tick_seconds
    stale_after = max(3 * tick, 300)
    state = await session.get(SchedulerState, STATE_ID)
    now = int(time.time())

    if state is None or state.last_tick_epoch is None:
        return SchedulerStatus(
            running=False,
            healthy=False,
            owner=None,
            last_tick_epoch=None,
            seconds_since_tick=None,
            stale_after_seconds=stale_after,
            rounds=0,
            probed_total=0,
            last_error=None,
        )

    age = now - state.last_tick_epoch
    healthy = age <= stale_after
    return SchedulerStatus(
        running=healthy,
        healthy=healthy,
        owner=state.owner,
        last_tick_epoch=state.last_tick_epoch,
        seconds_since_tick=age,
        stale_after_seconds=stale_after,
        rounds=state.rounds,
        probed_total=state.probed_total,
        last_error=state.last_error,
    )


__all__ = [
    "ProbeScheduler",
    "SchedulerStatus",
    "acquire_lease",
    "due_stream_ids",
    "get_state",
    "scheduler_status",
]
