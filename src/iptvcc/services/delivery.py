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
from .selector import load_qualities, rank_links, select_stream_id

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
    """Pick the best stream of a channel for the requested container.

    Never redirects across containers: a ``.ts`` URL that answers with an HLS manifest breaks
    ExoPlayer and loops mpv (documented in OwnTV's source).
    """
    wanted = _EXT_TO_CONTAINER.get(ext.lower())
    if wanted is None:
        return None

    channel = await session.scalar(select(Channel).where(Channel.public_key == public_key))
    if channel is None:
        return None

    stream_id = await select_stream_id(session, channel, wanted)
    if stream_id is None:
        return None
    stream = await session.get(Stream, stream_id)
    if stream is None:
        return None
    return ResolvedStream(url=decrypt(stream.url_enc), container=stream.container)


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

    qualities = await load_qualities(session)
    xmltv_ids = dict(
        (
            await session.execute(
                select(ChannelEpgMap.channel_id, EpgChannel.source_tvg_id).join(
                    EpgChannel, EpgChannel.id == ChannelEpgMap.epg_channel_id
                )
            )
        ).all()
    )

    rendered: list[RenderedChannel] = []
    for channel in channels:
        ranked = rank_links(channel, qualities, None)
        if not ranked:
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
                ext=ext_for(ranked[0].container),
            )
        )

    _ = profile  # profile filtering (visibility/order) lands with Playlist Studio
    return render_m3u(rendered, base_url=base_url, token=token)


__all__ = [
    "ROLE_DISABLED",
    "ResolvedStream",
    "build_playlist",
    "ext_for",
    "get_active_assignment",
    "resolve_stream",
]
