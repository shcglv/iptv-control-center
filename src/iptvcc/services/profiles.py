"""Playlist profiles: what a device actually sees, and in which order.

Profiles store overrides only. A channel without a ``profile_channel`` row is visible with its
catalog number; a hidden category hides its channels unless a channel is explicitly re-enabled.
Ordering uses ``sort_index`` when set, otherwise the channel number.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..models import Category, Channel, ChannelStream, ProfileCategory, ProfileChannel

_UNSET = 10**9


@dataclass(frozen=True)
class VisibleChannel:
    channel: Channel
    pinned_stream_id: int | None
    sort_index: int
    group_name: str | None


async def load_channel_overrides(
    session: AsyncSession, profile_id: int
) -> dict[str, ProfileChannel]:
    rows = (
        await session.scalars(select(ProfileChannel).where(ProfileChannel.profile_id == profile_id))
    ).all()
    return {row.channel_id: row for row in rows}


async def load_category_overrides(
    session: AsyncSession, profile_id: int
) -> dict[int, ProfileCategory]:
    rows = (
        await session.scalars(
            select(ProfileCategory).where(ProfileCategory.profile_id == profile_id)
        )
    ).all()
    return {row.category_id: row for row in rows}


async def visible_channels(session: AsyncSession, profile_id: int) -> list[VisibleChannel]:
    """Resolve the profile's effective lineup, ordered for output."""
    channels = (
        await session.scalars(
            select(Channel)
            .where(Channel.is_hidden.is_(False))
            .options(selectinload(Channel.links).selectinload(ChannelStream.stream))
            .options(selectinload(Channel.category))
        )
    ).all()
    channel_overrides = await load_channel_overrides(session, profile_id)
    category_overrides = await load_category_overrides(session, profile_id)

    selected: list[VisibleChannel] = []
    for channel in channels:
        override = channel_overrides.get(channel.id)
        if override is not None and not override.visible:
            continue
        if override is None and channel.category_id is not None:
            category_override = category_overrides.get(channel.category_id)
            if category_override is not None and not category_override.visible:
                continue

        order = _UNSET
        if override is not None and override.sort_index is not None:
            order = override.sort_index
        elif channel.number is not None:
            order = channel.number

        group = None
        if channel.category is not None:
            category_override = category_overrides.get(channel.category.id)
            group = (
                category_override.display_name
                if category_override is not None and category_override.display_name
                else channel.category.name
            )

        selected.append(
            VisibleChannel(
                channel=channel,
                pinned_stream_id=override.pinned_stream_id if override else None,
                sort_index=order,
                group_name=group,
            )
        )

    selected.sort(
        key=lambda item: (item.sort_index, item.channel.number or _UNSET, item.channel.id)
    )
    return selected


async def pinned_stream_for(session: AsyncSession, profile_id: int, channel_id: str) -> int | None:
    row = await session.get(ProfileChannel, (profile_id, channel_id))
    return row.pinned_stream_id if row is not None else None


async def category_of(session: AsyncSession, channel_id: str) -> Category | None:
    channel = await session.get(Channel, channel_id)
    if channel is None or channel.category_id is None:
        return None
    return await session.get(Category, channel.category_id)
