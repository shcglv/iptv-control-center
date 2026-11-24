"""Delivery: device playlists and the 302 source resolver.

The resolver is the only component that touches provider URLs at playback time. It returns a
redirect and never reads a media byte, so the server stays out of the data path.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..m3u import CONTAINER_DASH, CONTAINER_HLS, CONTAINER_TS, RenderedChannel, render_m3u
from ..models import (
    ROLE_DISABLED,
    Assignment,
    Channel,
    ChannelEpgMap,
    ChannelStream,
    EpgChannel,
    PlaylistProfile,
    Stream,
)
from ..secrets import decrypt
from ..security import hash_token

_EXT_TO_CONTAINER = {
    "ts": CONTAINER_TS,
    "m3u8": CONTAINER_HLS,
    "mpd": CONTAINER_DASH,
}


@dataclass(frozen=True)
class ResolvedStream:
    url: str
    container: str


def ext_for(container: str) -> str:
    if container == CONTAINER_HLS:
        return "m3u8"
    if container == CONTAINER_DASH:
        return "mpd"
    return "ts"


async def get_active_assignment(session: AsyncSession, token: str) -> Assignment | None:
    """Resolve a plaintext token to a live assignment (revoked tokens are rejected)."""
    assignment = await session.scalar(
        select(Assignment)
        .where(Assignment.token_hash == hash_token(token))
        .options(selectinload(Assignment.playlist_profile))
    )
    if assignment is None or assignment.revoked_at is not None:
        return None
    return assignment


async def resolve_stream(session: AsyncSession, public_key: str, ext: str) -> ResolvedStream | None:
    """Pick the best stream of a channel that matches the requested container.

    Never redirects across containers: a ``.ts`` URL that answers with an HLS manifest breaks
    ExoPlayer and loops mpv (documented in OwnTV's source).
    """
    wanted = _EXT_TO_CONTAINER.get(ext.lower())
    if wanted is None:
        return None

    channel = await session.scalar(select(Channel).where(Channel.public_key == public_key))
    if channel is None:
        return None

    links = (
        await session.scalars(
            select(ChannelStream)
            .where(ChannelStream.channel_id == channel.id)
            .options(selectinload(ChannelStream.stream))
            .order_by(ChannelStream.priority, ChannelStream.id)
        )
    ).all()

    for link in links:
        stream = link.stream
        if link.role == ROLE_DISABLED or stream.is_stale:
            continue
        if stream.container != wanted:
            continue
        return ResolvedStream(url=decrypt(stream.url_enc), container=stream.container)
    return None


async def build_playlist(session: AsyncSession, token: str, base_url: str) -> str | None:
    """Render the playlist for a device token, or ``None`` when the token is invalid."""
    assignment = await get_active_assignment(session, token)
    if assignment is None:
        return None

    profile: PlaylistProfile = assignment.playlist_profile
    channels = (
        await session.scalars(
            select(Channel)
            .where(Channel.is_hidden.is_(False))
            .options(selectinload(Channel.links).selectinload(ChannelStream.stream))
            .options(selectinload(Channel.category))
            .order_by(Channel.number, Channel.id)
        )
    ).all()

    rendered: list[RenderedChannel] = []
    xmltv_ids = dict(
        (
            await session.execute(
                select(ChannelEpgMap.channel_id, EpgChannel.source_tvg_id).join(
                    EpgChannel, EpgChannel.id == ChannelEpgMap.epg_channel_id
                )
            )
        ).all()
    )
    for channel in channels:
        best = _primary_stream(channel)
        if best is None:
            continue
        rendered.append(
            RenderedChannel(
                public_key=channel.public_key,
                display_name=channel.display_name,
                epg_id=(
                    xmltv_ids.get(channel.id)
                    or channel.epg_id
                    or channel.tvg_id
                    or channel.public_key
                ),
                logo_url=channel.logo_url,
                number=channel.number,
                group=channel.category.name if channel.category else None,
                ext=ext_for(best.container),
            )
        )

    _ = profile  # profile filtering (visibility/order) lands with Playlist Studio
    return render_m3u(rendered, base_url=base_url, token=token)


def _primary_stream(channel: Channel) -> Stream | None:
    links = sorted(channel.links, key=lambda link: (link.priority, link.id))
    for link in links:
        if link.role == ROLE_DISABLED or link.stream.is_stale:
            continue
        return link.stream
    return None
