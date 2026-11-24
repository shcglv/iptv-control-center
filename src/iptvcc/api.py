"""HTTP API: admin surface, device playlists and the 302 resolver."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from . import __version__
from .config import get_settings
from .db import create_schema, dispose_engine, get_session
from .models import Assignment, Channel, ChannelStream, Device, PlaylistProfile, Source
from .netguard import UnsafeUrlError, fetch_text
from .secrets import mask_url
from .security import constant_time_equals, generate_device_token, hash_token, token_prefix
from .services.delivery import build_playlist, get_active_assignment, resolve_stream
from .services.importing import ImportStats, import_playlist

logger = logging.getLogger(__name__)

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    if not settings.is_production:
        # Production schema changes go through Alembic; see docs/ROADMAP.md.
        await create_schema()
    yield
    await dispose_engine()


app = FastAPI(
    title="IPTV Control Center",
    version=__version__,
    summary="Control plane for IPTV: catalog, playlists, source resolution. No media bytes.",
    lifespan=lifespan,
)


# --- admin auth --------------------------------------------------------------------------------


async def require_admin(x_admin_token: Annotated[str, Header()] = "") -> None:
    """Temporary admin gate. Replaced by password + TOTP sessions in P1."""
    if not constant_time_equals(x_admin_token, get_settings().admin_api_key):
        raise HTTPException(status_code=401, detail="invalid admin token")


AdminDep = Annotated[None, Depends(require_admin)]


# --- schemas -----------------------------------------------------------------------------------


class SourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    url: str = ""
    type: Literal["m3u", "xtream", "manual", "rest"] = "m3u"


class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    type: str
    enabled: bool
    stream_count: int
    last_status: str | None
    last_import_at: datetime | None
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


@app.get("/", include_in_schema=False)
async def root() -> dict[str, str]:
    return {
        "name": "IPTV Control Center",
        "version": __version__,
        "docs": "/docs",
        "health": "/healthz",
    }


@app.post("/api/v1/sources", response_model=SourceOut, status_code=201, tags=["sources"])
async def create_source(payload: SourceCreate, session: SessionDep, _: AdminDep) -> SourceOut:
    existing = await session.scalar(select(Source).where(Source.name == payload.name))
    if existing is not None:
        raise HTTPException(status_code=409, detail="source name already exists")
    source = Source(name=payload.name, type=payload.type, url=payload.url)
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
    if await get_active_assignment(session, token) is None:
        raise HTTPException(status_code=404, detail="unknown or revoked token")
    stream = await resolve_stream(session, public_key, ext)
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
        url_masked=mask_url(source.url) if source.url else "",
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
