"""HTTP API: admin surface, device playlists and the 302 resolver."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import __version__
from .auth import (
    SESSION_COOKIE,
    SESSION_TTL,
    authenticate,
    clear_session_cookie,
    get_current_admin,
    open_session,
    revoke_session,
    set_session_cookie,
)
from .config import get_settings
from .db import create_schema, dispose_engine, get_session, get_session_factory
from .models import (
    AdminUser,
    Assignment,
    Category,
    Channel,
    ChannelEpgMap,
    ChannelStream,
    Device,
    DiscoveryCandidate,
    EpgChannel,
    EpgSource,
    PlaylistProfile,
    ProfileCategory,
    ProfileChannel,
    Programme,
    Source,
    Stream,
    StreamQuality,
)
from .netguard import UnsafeUrlError, fetch_text
from .quality import compute
from .secrets import decrypt, mask_url
from .security import generate_device_token, hash_token, token_prefix
from .services.delivery import build_playlist, get_active_assignment, resolve_stream
from .services.discovery import ADAPTERS, store_items
from .services.epg import (
    XmltvError,
    build_guide,
    guide_cache_key,
    ingest_xmltv,
    map_channels,
)
from .services.health import run_probe_round
from .services.importing import ImportStats, import_playlist
from .services.profiles import load_channel_overrides, visible_channels
from .services.scheduler import ProbeScheduler, scheduler_status
from .services.selector import rank_candidates
from .telegram import TelegramBot, TelegramClient

logger = logging.getLogger(__name__)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not settings.is_production:
        # Production schema changes go through Alembic; see docs/ROADMAP.md.
        await create_schema()

    scheduler: ProbeScheduler | None = None
    if settings.scheduler_enabled:
        factory = get_session_factory()
        app.state.session_factory = factory
        scheduler = ProbeScheduler(factory)
        scheduler.start()
        app.state.scheduler = scheduler

    telegram_bot: TelegramBot | None = None
    if settings.telegram_enabled and settings.telegram_bot_token:
        telegram_client = TelegramClient(
            settings.telegram_bot_token,
            base_url=settings.telegram_api_base,
            proxy=settings.telegram_proxy,
        )
        telegram_bot = TelegramBot(telegram_client, get_session_factory())
        telegram_bot.start()
        app.state.telegram_bot = telegram_bot

    try:
        yield
    finally:
        if telegram_bot is not None:
            await telegram_bot.stop()
        if scheduler is not None:
            await scheduler.stop()
        await dispose_engine()


app = FastAPI(
    title="IPTV Control Center",
    version=__version__,
    summary="Control plane for IPTV: catalog, playlists, source resolution. No media bytes.",
    lifespan=lifespan,
)


# --- admin auth --------------------------------------------------------------------------------

AdminDep = Annotated[AdminUser, Depends(get_current_admin)]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=1, max_length=256)
    totp: str = Field(default="", max_length=8)


class LoginResponse(BaseModel):
    email: str
    csrf_token: str
    expires_in: int


class MeOut(BaseModel):
    email: str
    last_login_at: datetime | None


@app.post("/api/v1/auth/login", response_model=LoginResponse, tags=["auth"])
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: SessionDep,
) -> LoginResponse:
    client_key = request.client.host if request.client else "unknown"
    user = await authenticate(
        session,
        email=payload.email,
        password=payload.password,
        code=payload.totp,
        client_key=client_key,
    )
    if user is None:
        raise HTTPException(status_code=401, detail="invalid credentials")
    token, csrf = await open_session(
        session, user=user, ip=client_key, user_agent=request.headers.get("user-agent")
    )
    set_session_cookie(response, token)
    return LoginResponse(
        email=user.email, csrf_token=csrf, expires_in=int(SESSION_TTL.total_seconds())
    )


@app.post("/api/v1/auth/logout", status_code=204, tags=["auth"])
async def logout(request: Request, session: SessionDep, _: AdminDep) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        await revoke_session(session, token)
    out = Response(status_code=204)
    clear_session_cookie(out)
    return out


@app.get("/api/v1/auth/me", response_model=MeOut, tags=["auth"])
async def me(admin: AdminDep) -> MeOut:
    return MeOut(email=admin.email, last_login_at=admin.last_login_at)


# --- schemas -----------------------------------------------------------------------------------


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = ""
    type: Literal["m3u", "xtream", "manual", "rest"] = "m3u"
    subscription_expires_at: datetime | None = None


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    enabled: bool
    stream_count: int
    last_status: str | None
    last_import_at: datetime | None
    subscription_expires_at: datetime | None
    url_masked: str


class ImportRequest(BaseModel):
    m3u: str | None = None
    url: str | None = None


class ImportResponse(BaseModel):
    source_id: int
    stats: dict[str, int]


class ChannelOut(BaseModel):
    id: str
    public_key: str
    name: str
    number: int | None
    group: str | None
    tvg_id: str | None
    sources: int


class ChannelPage(BaseModel):
    total: int
    items: list[ChannelOut]


class ChannelSourceOut(BaseModel):
    stream_id: int
    source: str
    name: str
    container: str
    score: float
    uptime_ratio: float
    resolution: str | None


class DeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    kind: str = "android_tv"
    profile_id: int | None = None


class DeviceOut(BaseModel):
    id: str
    name: str
    kind: str
    token_prefix: str
    m3u_path: str
    created_at: datetime


class DeviceWithToken(DeviceOut):
    token: str


class HealthOut(BaseModel):
    status: str
    version: str
    database: str


# --- catalog -----------------------------------------------------------------------------------


@app.get("/healthz", response_model=HealthOut, tags=["ops"])
async def healthz(session: SessionDep) -> HealthOut:
    database = "ok"
    try:
        await session.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        version=__version__,
        database=database,
    )


STATIC_DIR = Path(__file__).parent / "static"

if (STATIC_DIR / "assets").is_dir():
    app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")


@app.post("/api/v1/sources", response_model=SourceOut, status_code=201, tags=["sources"])
async def create_source(payload: SourceCreate, session: SessionDep, _: AdminDep) -> SourceOut:
    existing = await session.scalar(select(Source).where(Source.name == payload.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="source name already exists")
    source = Source(
        name=payload.name,
        type=payload.type,
        url=payload.url,
        subscription_expires_at=payload.subscription_expires_at,
    )
    session.add(source)
    await session.flush()
    return _source_out(source)


@app.get("/api/v1/sources", response_model=list[SourceOut], tags=["sources"])
async def list_sources(session: SessionDep, _: AdminDep) -> list[SourceOut]:
    rows = (await session.scalars(select(Source).order_by(Source.id))).all()
    return [_source_out(row) for row in rows]


@app.post("/api/v1/sources/{source_id}/import", response_model=ImportResponse, tags=["sources"])
async def import_source(
    source_id: int, payload: ImportRequest, session: SessionDep, _: AdminDep
) -> ImportResponse:
    source = await session.get(Source, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="source not found")
    if not payload.m3u and not payload.url:
        raise HTTPException(status_code=422, detail="provide either 'm3u' or 'url'")

    try:
        body = payload.m3u if payload.m3u else await fetch_text(payload.url or "")
    except UnsafeUrlError as exc:
        source.last_status = "error"
        source.last_error = str(exc)
        raise HTTPException(status_code=502, detail=f"import failed: {exc}") from exc

    if not body.lstrip("\ufeff").lstrip().upper().startswith("#EXTM3U"):
        source.last_status = "error"
        source.last_error = "not an M3U playlist"
        raise HTTPException(status_code=422, detail="body is not an M3U playlist")

    stats: ImportStats = await import_playlist(session, source, body)
    logger.info(
        "import source=%s parsed=%s created=%s channels=%s",
        source.id,
        stats.parsed,
        stats.created,
        stats.channels_created,
    )
    return ImportResponse(source_id=source.id, stats=stats.as_dict())


@app.get("/api/v1/channels", response_model=ChannelPage, tags=["catalog"])
async def list_channels(
    session: SessionDep,
    _: AdminDep,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    search: str | None = None,
) -> ChannelPage:
    base = select(Channel).where(Channel.is_hidden.is_(False))
    if search:
        base = base.where(Channel.display_name.ilike(f"%{search}%"))
    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = (
        await session.scalars(
            base.options(selectinload(Channel.category))
            .order_by(Channel.number, Channel.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()

    counts = dict(
        (
            await session.execute(
                select(ChannelStream.channel_id, func.count()).group_by(ChannelStream.channel_id)
            )
        ).all()
    )
    items = [
        ChannelOut(
            id=row.id,
            public_key=row.public_key,
            name=row.display_name,
            number=row.number,
            group=row.category.name if row.category else None,
            tvg_id=row.tvg_id,
            sources=counts.get(row.id, 0),
        )
        for row in rows
    ]
    return ChannelPage(total=total, items=items)


@app.get(
    "/api/v1/channels/{public_key}/sources",
    response_model=list[ChannelSourceOut],
    tags=["catalog"],
)
async def channel_sources(
    public_key: str, session: SessionDep, _: AdminDep
) -> list[ChannelSourceOut]:
    """Ranked sources of one channel, used by the profile editor to pin a stream."""
    channel = await session.scalar(select(Channel).where(Channel.public_key == public_key))
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")

    candidates = await rank_candidates(session, channel, None)
    if not candidates:
        return []

    rows = (
        await session.execute(
            select(Stream, StreamQuality, Source)
            .join(Source, Source.id == Stream.source_id)
            .outerjoin(StreamQuality, StreamQuality.stream_id == Stream.id)
            .where(Stream.id.in_([candidate.stream_id for candidate in candidates]))
        )
    ).all()
    details = {stream.id: (stream, quality, source) for stream, quality, source in rows}

    out: list[ChannelSourceOut] = []
    for candidate in candidates:
        found = details.get(candidate.stream_id)
        if found is None:
            continue
        stream, quality, source = found
        out.append(
            ChannelSourceOut(
                stream_id=stream.id,
                source=source.name,
                name=stream.name,
                container=stream.container,
                score=candidate.score,
                uptime_ratio=quality.uptime_ratio if quality else 0.0,
                resolution=quality.resolution if quality else None,
            )
        )
    return out


# --- devices -----------------------------------------------------------------------------------


@app.post("/api/v1/devices", response_model=DeviceWithToken, status_code=201, tags=["devices"])
async def create_device(payload: DeviceCreate, session: SessionDep, _: AdminDep) -> DeviceWithToken:
    profile = await _resolve_profile(session, payload.profile_id)
    device = Device(name=payload.name, kind=payload.kind)
    session.add(device)
    await session.flush()

    token = generate_device_token()
    assignment = Assignment(
        device_id=device.id,
        playlist_profile_id=profile.id,
        token_hash=hash_token(token),
        token_prefix=token_prefix(token),
    )
    session.add(assignment)
    await session.flush()
    out = _device_out(device, assignment)
    return DeviceWithToken(**out.model_dump(), token=token)


@app.get("/api/v1/devices", response_model=list[DeviceOut], tags=["devices"])
async def list_devices(session: SessionDep, _: AdminDep) -> list[DeviceOut]:
    rows = (
        await session.execute(
            select(Device, Assignment)
            .join(Assignment, Assignment.device_id == Device.id)
            .order_by(Device.created_at)
        )
    ).all()
    return [_device_out(device, assignment) for device, assignment in rows]


@app.post("/api/v1/devices/{device_id}/rotate", response_model=DeviceWithToken, tags=["devices"])
async def rotate_token(device_id: str, session: SessionDep, _: AdminDep) -> DeviceWithToken:
    device = await session.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="device not found")

    from datetime import UTC

    current = await session.scalar(
        select(Assignment).where(Assignment.device_id == device_id, Assignment.revoked_at.is_(None))
    )
    if current is not None:
        current.revoked_at = datetime.now(UTC)
        await session.flush()

    profile_id = (
        current.playlist_profile_id if current else (await _resolve_profile(session, None)).id
    )
    token = generate_device_token()
    assignment = Assignment(
        device_id=device.id,
        playlist_profile_id=profile_id,
        token_hash=hash_token(token),
        token_prefix=token_prefix(token),
    )
    session.add(assignment)
    await session.flush()
    out = _device_out(device, assignment)
    return DeviceWithToken(**out.model_dump(), token=token)


# --- EPG ---------------------------------------------------------------------------------------


class EpgSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = ""
    priority: int = 0
    retention_days: int = Field(default=3, ge=0, le=30)
    default_timezone: str = "UTC"


class EpgSourceOut(BaseModel):
    id: int
    name: str
    url_masked: str
    priority: int
    enabled: bool
    retention_days: int
    last_status: str | None
    last_fetch_at: datetime | None
    channels: int


class EpgImportRequest(BaseModel):
    xml: str | None = None
    url: str | None = None


class EpgImportResponse(BaseModel):
    source_id: int
    stats: dict[str, int]


class EpgStatusOut(BaseModel):
    sources: int
    epg_channels: int
    programmes: int
    mapped_channels: int
    unmapped_channels: int


@app.post("/api/v1/epg/sources", response_model=EpgSourceOut, status_code=201, tags=["epg"])
async def create_epg_source(
    payload: EpgSourceCreate, session: SessionDep, _: AdminDep
) -> EpgSourceOut:
    existing = await session.scalar(select(EpgSource).where(EpgSource.name == payload.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="EPG source name already exists")
    source = EpgSource(
        name=payload.name,
        url=payload.url,
        priority=payload.priority,
        retention_days=payload.retention_days,
        default_timezone=payload.default_timezone,
    )
    session.add(source)
    await session.flush()
    return _epg_source_out(source, 0)


@app.get("/api/v1/epg/sources", response_model=list[EpgSourceOut], tags=["epg"])
async def list_epg_sources(session: SessionDep, _: AdminDep) -> list[EpgSourceOut]:
    sources = (await session.scalars(select(EpgSource).order_by(EpgSource.id))).all()
    counts = dict(
        (
            await session.execute(
                select(EpgChannel.epg_source_id, func.count()).group_by(EpgChannel.epg_source_id)
            )
        ).all()
    )
    return [_epg_source_out(source, counts.get(source.id, 0)) for source in sources]


@app.post("/api/v1/epg/sources/{source_id}/import", response_model=EpgImportResponse, tags=["epg"])
async def import_epg(
    source_id: int, payload: EpgImportRequest, session: SessionDep, _: AdminDep
) -> EpgImportResponse:
    source = await session.get(EpgSource, source_id)
    if source is None:
        raise HTTPException(status_code=404, detail="EPG source not found")
    if not payload.xml and not payload.url:
        raise HTTPException(status_code=422, detail="provide either 'xml' or 'url'")

    try:
        if payload.xml:
            data = payload.xml.encode("utf-8")
        else:
            data = (await fetch_text(payload.url or "")).encode("utf-8")
        stats = await ingest_xmltv(session, source, data)
    except XmltvError as exc:
        source.last_status = "error"
        source.last_error = str(exc)
        raise HTTPException(status_code=422, detail=f"EPG import failed: {exc}") from exc
    except UnsafeUrlError as exc:
        source.last_status = "error"
        source.last_error = str(exc)
        raise HTTPException(status_code=502, detail=f"EPG import failed: {exc}") from exc

    return EpgImportResponse(source_id=source.id, stats=stats.as_dict())


@app.post("/api/v1/epg/map", tags=["epg"])
async def remap_epg(session: SessionDep, _: AdminDep) -> dict[str, int]:
    return {"mapped": await map_channels(session)}


@app.get("/api/v1/epg/status", response_model=EpgStatusOut, tags=["epg"])
async def epg_status(session: SessionDep, _: AdminDep) -> EpgStatusOut:
    sources = await session.scalar(select(func.count()).select_from(EpgSource)) or 0
    epg_channels = await session.scalar(select(func.count()).select_from(EpgChannel)) or 0
    programmes = await session.scalar(select(func.count()).select_from(Programme)) or 0
    mapped = await session.scalar(select(func.count()).select_from(ChannelEpgMap)) or 0
    total_channels = await session.scalar(select(func.count()).select_from(Channel)) or 0
    return EpgStatusOut(
        sources=sources,
        epg_channels=epg_channels,
        programmes=programmes,
        mapped_channels=mapped,
        unmapped_channels=max(0, total_channels - mapped),
    )


@app.get("/p/{token}/epg.xml", tags=["delivery"])
async def device_epg(token: str, session: SessionDep) -> FileResponse:
    return await _serve_epg(token, session, gzipped=False)


@app.get("/p/{token}/epg.xml.gz", tags=["delivery"])
async def device_epg_gzip(token: str, session: SessionDep) -> FileResponse:
    return await _serve_epg(token, session, gzipped=True)


async def _serve_epg(token: str, session: AsyncSession, *, gzipped: bool) -> FileResponse:
    out_dir = Path(get_settings().artifacts_dir) / "epg"
    key = guide_cache_key(token)
    xml_path = out_dir / f"{key}.xml"
    gz_path = out_dir / f"{key}.xml.gz"

    fresh = xml_path.exists() and (time.time() - xml_path.stat().st_mtime) < 300
    if not fresh:
        rendered = await build_guide(session, token, out_dir)
        if rendered is None:
            raise HTTPException(status_code=404, detail="unknown or revoked token")
        xml_path, gz_path = rendered

    path = gz_path if gzipped else xml_path
    return FileResponse(
        path,
        media_type="application/gzip" if gzipped else "application/xml",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


# --- playlist profiles -------------------------------------------------------------------------


class ProfileCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    strategy: str = "balanced"
    is_default: bool = False


class ProfileOut(BaseModel):
    id: int
    name: str
    strategy: str
    is_default: bool
    devices: int
    channels_visible: int
    channels_total: int


class ProfileChannelOut(BaseModel):
    public_key: str
    name: str
    number: int | None
    group: str | None
    visible: bool
    sort_index: int | None
    pinned_stream_id: int | None
    overridden: bool


class ChannelOverrideIn(BaseModel):
    """Full replacement of a channel override. Use DELETE to fall back to the catalog."""

    visible: bool = True
    sort_index: int | None = None
    pinned_stream_id: int | None = None


class CategoryOverrideIn(BaseModel):
    visible: bool = True
    sort_index: int | None = None
    display_name: str | None = None


@app.post("/api/v1/profiles", response_model=ProfileOut, status_code=201, tags=["profiles"])
async def create_profile(payload: ProfileCreate, session: SessionDep, _: AdminDep) -> ProfileOut:
    existing = await session.scalar(
        select(PlaylistProfile).where(PlaylistProfile.name == payload.name)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="profile name already exists")
    profile = PlaylistProfile(
        name=payload.name, strategy=payload.strategy, is_default=payload.is_default
    )
    session.add(profile)
    await session.flush()
    return await _profile_out(session, profile)


@app.get("/api/v1/profiles", response_model=list[ProfileOut], tags=["profiles"])
async def list_profiles(session: SessionDep, _: AdminDep) -> list[ProfileOut]:
    profiles = (await session.scalars(select(PlaylistProfile).order_by(PlaylistProfile.id))).all()
    return [await _profile_out(session, profile) for profile in profiles]


@app.get(
    "/api/v1/profiles/{profile_id}/channels",
    response_model=list[ProfileChannelOut],
    tags=["profiles"],
)
async def list_profile_channels(
    profile_id: int, session: SessionDep, _: AdminDep
) -> list[ProfileChannelOut]:
    profile = await session.get(PlaylistProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")

    overrides = await load_channel_overrides(session, profile_id)
    lineup = await visible_channels(session, profile_id)
    visible_ids = {item.channel.id for item in lineup}

    all_channels = (
        await session.scalars(
            select(Channel)
            .where(Channel.is_hidden.is_(False))
            .options(selectinload(Channel.category))
            .order_by(Channel.number, Channel.id)
        )
    ).all()

    result: list[ProfileChannelOut] = []
    for channel in all_channels:
        override = overrides.get(channel.id)
        result.append(
            ProfileChannelOut(
                public_key=channel.public_key,
                name=channel.display_name,
                number=channel.number,
                group=channel.category.name if channel.category else None,
                visible=channel.id in visible_ids,
                sort_index=override.sort_index if override else None,
                pinned_stream_id=override.pinned_stream_id if override else None,
                overridden=override is not None,
            )
        )
    return result


@app.put(
    "/api/v1/profiles/{profile_id}/channels/{public_key}",
    response_model=ProfileChannelOut,
    tags=["profiles"],
)
async def set_profile_channel(
    profile_id: int,
    public_key: str,
    payload: ChannelOverrideIn,
    session: SessionDep,
    _: AdminDep,
) -> ProfileChannelOut:
    profile = await session.get(PlaylistProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    channel = await session.scalar(
        select(Channel)
        .where(Channel.public_key == public_key)
        .options(selectinload(Channel.category))
    )
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    if payload.pinned_stream_id is not None:
        pinned = await session.get(Stream, payload.pinned_stream_id)
        if pinned is None or pinned.source_id is None:
            raise HTTPException(status_code=422, detail="pinned stream does not exist")

    override = await session.get(ProfileChannel, (profile_id, channel.id))
    if override is None:
        override = ProfileChannel(profile_id=profile_id, channel_id=channel.id)
        session.add(override)
    override.visible = payload.visible
    override.sort_index = payload.sort_index
    override.pinned_stream_id = payload.pinned_stream_id
    await session.flush()

    return ProfileChannelOut(
        public_key=channel.public_key,
        name=channel.display_name,
        number=channel.number,
        group=channel.category.name if channel.category else None,
        visible=payload.visible,
        sort_index=payload.sort_index,
        pinned_stream_id=payload.pinned_stream_id,
        overridden=True,
    )


@app.delete(
    "/api/v1/profiles/{profile_id}/channels/{public_key}",
    status_code=204,
    tags=["profiles"],
)
async def clear_profile_channel(
    profile_id: int, public_key: str, session: SessionDep, _: AdminDep
) -> Response:
    channel = await session.scalar(select(Channel).where(Channel.public_key == public_key))
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    override = await session.get(ProfileChannel, (profile_id, channel.id))
    if override is not None:
        await session.delete(override)
        await session.flush()
    return Response(status_code=204)


@app.put(
    "/api/v1/profiles/{profile_id}/categories/{category_id}",
    status_code=204,
    tags=["profiles"],
)
async def set_profile_category(
    profile_id: int,
    category_id: int,
    payload: CategoryOverrideIn,
    session: SessionDep,
    _: AdminDep,
) -> Response:
    profile = await session.get(PlaylistProfile, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail="profile not found")
    category = await session.get(Category, category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="category not found")

    override = await session.get(ProfileCategory, (profile_id, category_id))
    if override is None:
        override = ProfileCategory(profile_id=profile_id, category_id=category_id)
        session.add(override)
    override.visible = payload.visible
    override.sort_index = payload.sort_index
    override.display_name = payload.display_name
    await session.flush()
    return Response(status_code=204)


async def _profile_out(session: AsyncSession, profile: PlaylistProfile) -> ProfileOut:
    devices = (
        await session.scalar(
            select(func.count())
            .select_from(Assignment)
            .where(Assignment.playlist_profile_id == profile.id)
        )
        or 0
    )
    total = (
        await session.scalar(
            select(func.count()).select_from(Channel).where(Channel.is_hidden.is_(False))
        )
        or 0
    )
    visible = len(await visible_channels(session, profile.id))
    return ProfileOut(
        id=profile.id,
        name=profile.name,
        strategy=profile.strategy,
        is_default=profile.is_default,
        devices=devices,
        channels_visible=visible,
        channels_total=total,
    )


# --- discovery ---------------------------------------------------------------------------------


class DiscoveryRunIn(BaseModel):
    adapter: Literal["rss", "html"]
    url: str = Field(min_length=8, max_length=2000)
    limit: int = Field(default=50, ge=1, le=200)


class DiscoveryRunOut(BaseModel):
    adapter: str
    items: int
    created: int
    updated: int


class DiscoveryCandidateOut(BaseModel):
    id: int
    adapter: str
    kind: str
    url_masked: str
    host: str
    title: str | None
    author: str | None
    published_at: str | None
    confidence: float
    has_credentials: bool
    status: str
    created_at: datetime


class DiscoveryCandidatePage(BaseModel):
    total: int
    items: list[DiscoveryCandidateOut]


class CandidateAcceptIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)


class DiscoveryStatusOut(BaseModel):
    new: int
    accepted: int
    rejected: int
    adapters: list[str]


@app.post("/api/v1/discovery/run", response_model=DiscoveryRunOut, tags=["discovery"])
async def discovery_run(
    payload: DiscoveryRunIn, session: SessionDep, _: AdminDep
) -> DiscoveryRunOut:
    adapter = ADAPTERS[payload.adapter]()
    try:
        items = await adapter.discover(payload.url, limit=payload.limit)
    except UnsafeUrlError as exc:
        raise HTTPException(status_code=502, detail=f"discovery failed: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"discovery failed: {exc}") from exc

    stats = await store_items(session, adapter=payload.adapter, items=items)
    return DiscoveryRunOut(
        adapter=payload.adapter,
        items=len(items),
        created=stats.created,
        updated=stats.updated,
    )


@app.get("/api/v1/discovery/candidates", response_model=DiscoveryCandidatePage, tags=["discovery"])
async def list_candidates(
    session: SessionDep,
    _: AdminDep,
    status: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> DiscoveryCandidatePage:
    statement = select(DiscoveryCandidate)
    if status:
        statement = statement.where(DiscoveryCandidate.status == status)
    total = await session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = (
        await session.scalars(
            statement.order_by(DiscoveryCandidate.id.desc()).limit(limit).offset(offset)
        )
    ).all()
    return DiscoveryCandidatePage(
        total=total,
        items=[
            DiscoveryCandidateOut(
                id=row.id,
                adapter=row.adapter,
                kind=row.kind,
                url_masked=row.url_masked,
                host=row.host,
                title=row.title,
                author=row.author,
                published_at=row.published_at,
                confidence=row.confidence,
                has_credentials=row.has_credentials,
                status=row.status,
                created_at=row.created_at,
            )
            for row in rows
        ],
    )


@app.post(
    "/api/v1/discovery/candidates/{candidate_id}/accept",
    response_model=SourceOut,
    tags=["discovery"],
)
async def accept_candidate(
    candidate_id: int,
    payload: CandidateAcceptIn,
    session: SessionDep,
    _: AdminDep,
) -> SourceOut:
    candidate = await session.get(DiscoveryCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    if candidate.status == "accepted":
        raise HTTPException(status_code=409, detail="candidate already accepted")

    base = (payload.name or candidate.title or candidate.host or "Discovered source").strip()
    base = base[:200] or "Discovered source"
    name = base
    if await session.scalar(select(Source).where(Source.name == name)) is not None:
        name = f"{base[:188]} ({candidate.id})"

    source = Source(
        name=name,
        type="xtream" if candidate.kind == "xtream" else "m3u",
        url=decrypt(candidate.url_enc),
    )
    session.add(source)
    await session.flush()

    candidate.status = "accepted"
    candidate.promoted_source_id = source.id
    await session.flush()
    return _source_out(source)


@app.post(
    "/api/v1/discovery/candidates/{candidate_id}/reject",
    status_code=204,
    tags=["discovery"],
)
async def reject_candidate(candidate_id: int, session: SessionDep, _: AdminDep) -> Response:
    candidate = await session.get(DiscoveryCandidate, candidate_id)
    if candidate is None:
        raise HTTPException(status_code=404, detail="candidate not found")
    candidate.status = "rejected"
    await session.flush()
    return Response(status_code=204)


@app.get("/api/v1/discovery/status", response_model=DiscoveryStatusOut, tags=["discovery"])
async def discovery_status(session: SessionDep, _: AdminDep) -> DiscoveryStatusOut:
    counts: dict[str, int] = {}
    for status in ("new", "accepted", "rejected"):
        counts[status] = (
            await session.scalar(
                select(func.count())
                .select_from(DiscoveryCandidate)
                .where(DiscoveryCandidate.status == status)
            )
            or 0
        )
    return DiscoveryStatusOut(
        new=counts["new"],
        accepted=counts["accepted"],
        rejected=counts["rejected"],
        adapters=sorted(ADAPTERS),
    )


# --- health and quality ------------------------------------------------------------------------


class HealthRunRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)


class HealthRunResponse(BaseModel):
    probed: int
    ok: int
    failed: int


class StreamHealthOut(BaseModel):
    stream_id: int
    source: str
    name: str
    container: str
    score: float
    uptime_ratio: float
    checks: int
    failures: int
    p90_ttfb_ms: float | None
    resolution: str | None
    video_codec: str | None
    last_error: str | None


class CandidateOut(BaseModel):
    stream_id: int
    container: str
    score: float
    breakdown: dict[str, float]


class ChannelHealthOut(BaseModel):
    public_key: str
    name: str
    active_stream_id: int | None
    candidates: list[CandidateOut]


@app.post("/api/v1/health/run", response_model=HealthRunResponse, tags=["health"])
async def run_health(
    payload: HealthRunRequest, session: SessionDep, _: AdminDep
) -> HealthRunResponse:
    stats = await run_probe_round(session, limit=payload.limit)
    return HealthRunResponse(**stats)


@app.get("/api/v1/health/streams", response_model=list[StreamHealthOut], tags=["health"])
async def list_stream_health(
    session: SessionDep,
    _: AdminDep,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[StreamHealthOut]:
    rows = (
        await session.execute(
            select(Stream, StreamQuality, Source)
            .join(Source, Source.id == Stream.source_id)
            .outerjoin(StreamQuality, StreamQuality.stream_id == Stream.id)
            .order_by(StreamQuality.uptime_ratio.asc().nullsfirst(), Stream.id)
            .limit(limit)
        )
    ).all()

    out: list[StreamHealthOut] = []
    for stream, quality, source in rows:
        breakdown = compute(
            uptime_ratio=quality.uptime_ratio if quality else None,
            checks=quality.checks if quality else 0,
            failures=quality.failures if quality else 0,
            p90_ttfb_ms=quality.p90_ttfb_ms if quality else None,
            resolution=quality.resolution if quality else None,
            video_codec=quality.video_codec if quality else None,
            bandwidth_bps=quality.bandwidth_bps if quality else None,
            container=stream.container,
            requested_ext=stream.container,
        )
        out.append(
            StreamHealthOut(
                stream_id=stream.id,
                source=source.name,
                name=stream.name,
                container=stream.container,
                score=breakdown.total,
                uptime_ratio=quality.uptime_ratio if quality else 0.0,
                checks=quality.checks if quality else 0,
                failures=quality.failures if quality else 0,
                p90_ttfb_ms=quality.p90_ttfb_ms if quality else None,
                resolution=quality.resolution if quality else None,
                video_codec=quality.video_codec if quality else None,
                last_error=quality.last_error_class if quality else None,
            )
        )
    return out


@app.get("/api/v1/health/channels/{public_key}", response_model=ChannelHealthOut, tags=["health"])
async def channel_health(public_key: str, session: SessionDep, _: AdminDep) -> ChannelHealthOut:
    channel = await session.scalar(select(Channel).where(Channel.public_key == public_key))
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    candidates = await rank_candidates(session, channel, None)
    return ChannelHealthOut(
        public_key=channel.public_key,
        name=channel.display_name,
        active_stream_id=channel.active_stream_id,
        candidates=[
            CandidateOut(
                stream_id=candidate.stream_id,
                container=candidate.container,
                score=candidate.score,
                breakdown=candidate.breakdown.__dict__,
            )
            for candidate in candidates
        ],
    )


class SchedulerStatusOut(BaseModel):
    running: bool
    healthy: bool
    owner: str | None
    last_tick_epoch: int | None
    seconds_since_tick: int | None = None
    stale_after_seconds: int
    rounds: int
    probed_total: int
    last_error: str | None


class SchedulerTickOut(BaseModel):
    probed: int
    ok: int
    failed: int
    skipped: bool


@app.get("/api/v1/scheduler/status", response_model=SchedulerStatusOut, tags=["health"])
async def get_scheduler_status(session: SessionDep, _: AdminDep) -> SchedulerStatusOut:
    status = await scheduler_status(session)
    return SchedulerStatusOut(**asdict(status))


@app.post("/api/v1/scheduler/tick", response_model=SchedulerTickOut, tags=["health"])
async def run_scheduler_tick(request: Request, _: AdminDep) -> SchedulerTickOut:
    """Run one round immediately. Skipped when the background loop holds the lease."""
    factory = getattr(request.app.state, "session_factory", None) or get_session_factory()
    scheduler = ProbeScheduler(factory)
    stats = await scheduler.tick()
    return SchedulerTickOut(
        probed=int(stats["probed"]),
        ok=int(stats["ok"]),
        failed=int(stats["failed"]),
        skipped=bool(stats["skipped"]),
    )


# --- delivery ----------------------------------------------------------------------------------


@app.get("/p/{token}/live.m3u", response_class=PlainTextResponse, tags=["delivery"])
async def device_playlist(token: str, request: Request, session: SessionDep) -> PlainTextResponse:
    base_url = get_settings().public_base_url or str(request.base_url).rstrip("/")
    playlist = await build_playlist(session, token, base_url)
    if playlist is None:
        raise HTTPException(status_code=404, detail="unknown or revoked token")
    return PlainTextResponse(
        playlist,
        media_type="audio/x-mpegurl",
        headers={"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"},
    )


@app.get("/p/{token}/s/{public_key}.{ext}", tags=["delivery"])
async def resolve(token: str, public_key: str, ext: str, session: SessionDep) -> RedirectResponse:
    assignment = await get_active_assignment(session, token)
    if assignment is None:
        raise HTTPException(status_code=404, detail="unknown or revoked token")
    stream = await resolve_stream(
        session, public_key, ext, profile_id=assignment.playlist_profile_id
    )
    if stream is None:
        raise HTTPException(
            status_code=503, detail="no healthy source for this container right now"
        )
    return RedirectResponse(
        stream.url,
        status_code=302,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@app.head("/p/{token}/s/{public_key}.{ext}", tags=["delivery"])
async def resolve_head(token: str, public_key: str, ext: str, session: SessionDep) -> Response:
    """Cheap HEAD probe: never calls the provider (Jellyfin probes extension-less URLs)."""
    if await get_active_assignment(session, token) is None:
        raise HTTPException(status_code=404, detail="unknown or revoked token")
    return Response(status_code=200, headers={"Cache-Control": "no-store"})


# --- helpers -----------------------------------------------------------------------------------


def _source_out(source: Source) -> SourceOut:
    return SourceOut(
        id=source.id,
        name=source.name,
        type=source.type,
        enabled=source.enabled,
        stream_count=source.stream_count,
        last_status=source.last_status,
        last_import_at=source.last_import_at,
        subscription_expires_at=source.subscription_expires_at,
        url_masked=mask_url(source.url) if source.url else "",
    )


def _epg_source_out(source: EpgSource, channels: int) -> EpgSourceOut:
    return EpgSourceOut(
        id=source.id,
        name=source.name,
        url_masked=mask_url(source.url) if source.url else "",
        priority=source.priority,
        enabled=source.enabled,
        retention_days=source.retention_days,
        last_status=source.last_status,
        last_fetch_at=source.last_fetch_at,
        channels=channels,
    )


def _device_out(device: Device, assignment: Assignment) -> DeviceOut:
    return DeviceOut(
        id=device.id,
        name=device.name,
        kind=device.kind,
        token_prefix=assignment.token_prefix,
        m3u_path="/p/<token>/live.m3u",
        created_at=device.created_at,
    )


async def _resolve_profile(session: AsyncSession, profile_id: int | None) -> PlaylistProfile:
    if profile_id is not None:
        profile = await session.get(PlaylistProfile, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail="playlist profile not found")
        return profile
    profile = await session.scalar(select(PlaylistProfile).order_by(PlaylistProfile.id).limit(1))
    if profile is None:
        profile = PlaylistProfile(name="Default", is_default=True)
        session.add(profile)
        await session.flush()
    return profile


# --- frontend ----------------------------------------------------------------------------------
#
# Registered last on purpose: a catch-all declared earlier would shadow every API route above it.


@app.get("/{full_path:path}", include_in_schema=False)
async def frontend_or_info(full_path: str) -> Response:
    """Serve the built SPA, falling back to an API description when no build is present."""
    if full_path.startswith(("api/", "p/")) or full_path == "healthz":
        raise HTTPException(status_code=404, detail="not found")

    index = STATIC_DIR / "index.html"
    if index.is_file():
        if full_path:
            candidate = (STATIC_DIR / full_path).resolve()
            if candidate.is_file() and candidate.is_relative_to(STATIC_DIR.resolve()):
                return FileResponse(candidate)
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    return JSONResponse(
        {
            "name": "IPTV Control Center",
            "version": __version__,
            "docs": "/docs",
            "health": "/healthz",
            "ui": "not built (run: cd web && pnpm build)",
        }
    )
