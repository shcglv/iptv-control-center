"""Data model: sources, streams, catalog, devices and delivery tokens.

Portable across SQLite (local dev/tests) and PostgreSQL (production): UUIDs are stored as
36-char strings, JSON uses ``JSONB`` on PostgreSQL via ``with_variant`` and plain JSON elsewhere.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> str:
    return str(uuid.uuid4())


# --- enums as strings (portable, migration-friendly) -------------------------------------------

SOURCE_M3U = "m3u"
SOURCE_XTREAM = "xtream"
SOURCE_MANUAL = "manual"
SOURCE_REST = "rest"
SOURCE_CATALOG = "catalog"
SOURCE_FORUM = "forum"

CONTAINER_TS = "ts"
CONTAINER_HLS = "m3u8"
CONTAINER_DASH = "mpd"
CONTAINER_OTHER = "other"

ROLE_PRIMARY = "primary"
ROLE_BACKUP = "backup"
ROLE_DISABLED = "disabled"


# --- catalog -----------------------------------------------------------------------------------


class Category(Base):
    __tablename__ = "category"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    sort_index: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(16), default="live")

    channels: Mapped[list[Channel]] = relationship(back_populates="category")


class Source(Base):
    __tablename__ = "source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    type: Mapped[str] = mapped_column(String(16), default=SOURCE_M3U)
    url: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    stream_count: Mapped[int] = mapped_column(Integer, default=0)

    last_import_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    streams: Mapped[list[Stream]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class Stream(Base):
    """One provider stream: a single URL plus the metadata that came with it."""

    __tablename__ = "stream"
    __table_args__ = (UniqueConstraint("source_id", "stream_key", name="uq_stream_source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("source.id", ondelete="CASCADE"), index=True)

    stream_key: Mapped[str] = mapped_column(String(64))
    stream_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)

    name: Mapped[str] = mapped_column(String(512), default="")
    tvg_id: Mapped[str | None] = mapped_column(String(255))
    tvg_name: Mapped[str | None] = mapped_column(String(255))
    logo_url: Mapped[str | None] = mapped_column(Text)
    group_title: Mapped[str | None] = mapped_column(String(255))

    url_enc: Mapped[str] = mapped_column(Text)
    container: Mapped[str] = mapped_column(String(16), default=CONTAINER_TS)

    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    source: Mapped[Source] = relationship(back_populates="streams")
    links: Mapped[list[ChannelStream]] = relationship(back_populates="stream")


class Channel(Base):
    """The catalog entry a device actually sees. Identity is permanent."""

    __tablename__ = "channel"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    public_key: Mapped[str] = mapped_column(String(24), unique=True, index=True)

    name: Mapped[str] = mapped_column(String(512))
    display_name: Mapped[str] = mapped_column(String(512))
    number: Mapped[int | None] = mapped_column(Integer, index=True)
    category_id: Mapped[int | None] = mapped_column(ForeignKey("category.id", ondelete="SET NULL"))
    logo_url: Mapped[str | None] = mapped_column(Text)
    tvg_id: Mapped[str | None] = mapped_column(String(255), index=True)
    epg_id: Mapped[str | None] = mapped_column(String(255))
    match_key: Mapped[str | None] = mapped_column(String(512), index=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, default=False)

    # Selection memory: lets the selector keep its current source instead of flapping between
    # two streams whose scores are within the hysteresis band.
    active_stream_id: Mapped[int | None] = mapped_column(Integer)
    active_since_epoch: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )

    category: Mapped[Category | None] = relationship(back_populates="channels")
    links: Mapped[list[ChannelStream]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class ChannelStream(Base):
    """Ordered stream list of a channel. ``priority`` decides failover order."""

    __tablename__ = "channel_stream"
    __table_args__ = (UniqueConstraint("channel_id", "stream_id", name="uq_channel_stream"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channel.id", ondelete="CASCADE"), index=True
    )
    stream_id: Mapped[int] = mapped_column(ForeignKey("stream.id", ondelete="CASCADE"), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    role: Mapped[str] = mapped_column(String(16), default=ROLE_PRIMARY)

    channel: Mapped[Channel] = relationship(back_populates="links")
    stream: Mapped[Stream] = relationship(back_populates="links")


# --- clients and delivery ----------------------------------------------------------------------


class ClientProfile(Base):
    """Technical capabilities of an IPTV application (OwnTV, VLC, ...)."""

    __tablename__ = "client_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    platform: Mapped[str] = mapped_column(String(32), default="generic")
    engine: Mapped[str] = mapped_column(String(32), default="generic")
    capabilities: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


class PlaylistProfile(Base):
    """Channel selection and delivery preferences shared by one or more clients."""

    __tablename__ = "playlist_profile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    strategy: Mapped[str] = mapped_column(String(24), default="balanced")
    client_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("client_profile.id", ondelete="SET NULL")
    )
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Device(Base):
    __tablename__ = "device"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(24), default="android_tv")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Assignment(Base):
    """A device's access grant: one token bound to one playlist profile."""

    __tablename__ = "assignment"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    device_id: Mapped[str] = mapped_column(ForeignKey("device.id", ondelete="CASCADE"), index=True)
    playlist_profile_id: Mapped[int] = mapped_column(
        ForeignKey("playlist_profile.id", ondelete="CASCADE")
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_prefix: Mapped[str] = mapped_column(String(12), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    device: Mapped[Device] = relationship()
    playlist_profile: Mapped[PlaylistProfile] = relationship()


class AppSetting(Base):
    __tablename__ = "app_setting"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(JsonType, default=dict)


# --- EPG ---------------------------------------------------------------------------------------
#
# Programme times are stored as UTC epoch seconds: unambiguous on both SQLite and PostgreSQL,
# directly comparable in SQL, and trivial to format back to XMLTV's "+0000".


class EpgSource(Base):
    __tablename__ = "epg_source"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    retention_days: Mapped[int] = mapped_column(Integer, default=3)

    # Applied only when a programme has no explicit UTC offset.
    default_timezone: Mapped[str] = mapped_column(String(64), default="UTC")

    last_fetch_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(32))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    channels: Mapped[list[EpgChannel]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )


class EpgChannel(Base):
    __tablename__ = "epg_channel"
    __table_args__ = (
        UniqueConstraint("epg_source_id", "source_tvg_id", name="uq_epg_channel_source_tvg"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epg_source_id: Mapped[int] = mapped_column(
        ForeignKey("epg_source.id", ondelete="CASCADE"), index=True
    )
    source_tvg_id: Mapped[str] = mapped_column(String(255), index=True)
    display_name: Mapped[str] = mapped_column(String(255), default="")
    icon_url: Mapped[str | None] = mapped_column(Text)

    source: Mapped[EpgSource] = relationship(back_populates="channels")
    programmes: Mapped[list[Programme]] = relationship(
        back_populates="channel", cascade="all, delete-orphan"
    )


class Programme(Base):
    __tablename__ = "programme"
    __table_args__ = (Index("ix_programme_channel_start", "epg_channel_id", "start_epoch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    epg_channel_id: Mapped[int] = mapped_column(
        ForeignKey("epg_channel.id", ondelete="CASCADE"), index=True
    )
    start_epoch: Mapped[int] = mapped_column(Integer)
    stop_epoch: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(512), default="")
    subtitle: Mapped[str | None] = mapped_column(String(512))
    description: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(128))

    channel: Mapped[EpgChannel] = relationship(back_populates="programmes")


class ChannelEpgMap(Base):
    __tablename__ = "channel_epg_map"

    channel_id: Mapped[str] = mapped_column(
        ForeignKey("channel.id", ondelete="CASCADE"), primary_key=True
    )
    epg_channel_id: Mapped[int] = mapped_column(
        ForeignKey("epg_channel.id", ondelete="CASCADE"), index=True
    )
    method: Mapped[str] = mapped_column(String(24), default="tvg_id")
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False)

    epg_channel: Mapped[EpgChannel] = relationship()


# --- health and quality ------------------------------------------------------------------------


class HealthCheck(Base):
    """One probe result. Pruned after a week; aggregates live in ``stream_quality``."""

    __tablename__ = "health_check"
    __table_args__ = (Index("ix_health_check_stream_time", "stream_id", "checked_epoch"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    stream_id: Mapped[int] = mapped_column(ForeignKey("stream.id", ondelete="CASCADE"), index=True)
    checked_epoch: Mapped[int] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean)
    dns_ms: Mapped[float | None] = mapped_column(Float)
    ttfb_ms: Mapped[float | None] = mapped_column(Float)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_class: Mapped[str | None] = mapped_column(String(32))


class StreamQuality(Base):
    """Materialised aggregate for one stream: uptime, latency and last observed format."""

    __tablename__ = "stream_quality"

    stream_id: Mapped[int] = mapped_column(
        ForeignKey("stream.id", ondelete="CASCADE"), primary_key=True
    )
    checks: Mapped[int] = mapped_column(Integer, default=0)
    failures: Mapped[int] = mapped_column(Integer, default=0)
    uptime_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    avg_ttfb_ms: Mapped[float | None] = mapped_column(Float)
    p90_ttfb_ms: Mapped[float | None] = mapped_column(Float)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    last_ok_epoch: Mapped[int | None] = mapped_column(Integer)
    last_checked_epoch: Mapped[int | None] = mapped_column(Integer)
    last_error_class: Mapped[str | None] = mapped_column(String(32))

    resolution: Mapped[str | None] = mapped_column(String(24))
    video_codec: Mapped[str | None] = mapped_column(String(32))
    audio_codec: Mapped[str | None] = mapped_column(String(32))
    bandwidth_bps: Mapped[int | None] = mapped_column(Integer)


class SchedulerState(Base):
    """Single-row coordination record: ownership lease plus watchdog timestamps.

    A row works as an advisory lock on both SQLite and PostgreSQL: ``acquire`` is a conditional
    UPDATE, so two processes cannot both win.
    """

    __tablename__ = "scheduler_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    owner: Mapped[str | None] = mapped_column(String(120))
    locked_until: Mapped[int | None] = mapped_column(Integer)
    last_tick_epoch: Mapped[int | None] = mapped_column(Integer)
    last_round_epoch: Mapped[int | None] = mapped_column(Integer)
    rounds: Mapped[int] = mapped_column(Integer, default=0)
    probed_total: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)


# --- admin identity ----------------------------------------------------------------------------


class AdminUser(Base):
    __tablename__ = "admin_user"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    totp_secret_enc: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AdminSession(Base):
    __tablename__ = "admin_session"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("admin_user.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    csrf_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
