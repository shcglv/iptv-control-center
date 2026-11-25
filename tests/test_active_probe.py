"""Active probe mode: ffprobe inspection behind a toggle, with the binary faked in tests."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from iptvcc.config import get_settings
from iptvcc.models import Stream
from iptvcc.secrets import encrypt
from iptvcc.services import health

FFPROBE_JSON = json.dumps(
    {
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"bit_rate": "5000000"},
    }
)


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = b"\x47" * 4096
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: object) -> None:
        return


@pytest.fixture
def media_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/live.ts"
    finally:
        server.shutdown()
        server.server_close()


def _stream(url: str) -> Stream:
    return Stream(
        source_id=1,
        stream_key="k",
        stream_hash="h",
        name="S",
        url_enc=encrypt(url),
        container="ts",
    )


def _fake_ffprobe(tmp_path: Path, *, stdout: str = "", stderr: str = "", code: int = 0) -> Path:
    script = tmp_path / f"ffprobe_{code}_{len(stdout)}_{len(stderr)}.py"
    script.write_text(
        "import sys\n"
        f"sys.stdout.write({stdout!r})\n"
        f"sys.stderr.write({stderr!r})\n"
        f"sys.exit({code})\n",
        encoding="utf-8",
    )
    return script


# --- command and parsing -----------------------------------------------------------------------


def test_ffprobe_command_reads_streams_not_the_whole_file() -> None:
    command = health.ffprobe_command("ffprobe", "http://p.test/x.ts", 12.0)
    assert command[0] == "ffprobe"
    assert "-show_streams" in command
    assert "-show_format" in command
    assert command[-1] == "http://p.test/x.ts"
    assert "12000000" in command  # -rw_timeout in microseconds


def test_parse_ffprobe_json_extracts_format() -> None:
    info = health.parse_ffprobe_json(FFPROBE_JSON)
    assert info.error is None
    assert info.resolution == "1920x1080"
    assert info.video_codec == "h264"
    assert info.audio_codec == "aac"
    assert info.bandwidth_bps == 5_000_000


def test_parse_ffprobe_json_rejects_garbage() -> None:
    assert health.parse_ffprobe_json("not json").error
    assert health.parse_ffprobe_json("{}").resolution is None


# --- running the binary ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_media_parses_the_binary_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, media_server: str
) -> None:
    script = _fake_ffprobe(tmp_path, stdout=FFPROBE_JSON)
    monkeypatch.setattr(
        health, "ffprobe_command", lambda binary, url, timeout: [sys.executable, str(script), url]
    )
    info = await health.probe_media(media_server, binary="ffprobe")
    assert info.error is None
    assert info.resolution == "1920x1080"
    assert info.video_codec == "h264"


@pytest.mark.asyncio
async def test_probe_media_reports_a_missing_binary(media_server: str) -> None:
    info = await health.probe_media(media_server, binary="/nonexistent/ffprobe")
    assert info.error is not None
    assert "not installed" in info.error


@pytest.mark.asyncio
async def test_probe_media_reports_a_nonzero_exit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, media_server: str
) -> None:
    script = _fake_ffprobe(tmp_path, stderr="Invalid data found when processing input\n", code=2)
    monkeypatch.setattr(
        health, "ffprobe_command", lambda binary, url, timeout: [sys.executable, str(script), url]
    )
    info = await health.probe_media(media_server, binary="ffprobe")
    assert info.error is not None
    assert "Invalid data found" in info.error


# --- modes -------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_passive_mode_never_runs_ffprobe(
    media_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "probe_mode", "passive", raising=False)

    async def boom(*_args: object, **_kwargs: object) -> health.MediaInfo:
        raise AssertionError("ffprobe must not run in passive mode")

    monkeypatch.setattr(health, "probe_media", boom)
    result = await health.probe_stream(_stream(media_server))
    assert result.ok is True


@pytest.mark.asyncio
async def test_active_mode_enriches_the_result(
    tmp_path: Path, media_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _fake_ffprobe(tmp_path, stdout=FFPROBE_JSON)
    monkeypatch.setattr(
        health, "ffprobe_command", lambda binary, url, timeout: [sys.executable, str(script), url]
    )
    monkeypatch.setattr(get_settings(), "probe_mode", "active", raising=False)

    result = await health.probe_stream(_stream(media_server))
    assert result.ok is True
    assert result.resolution == "1920x1080"
    assert result.video_codec == "h264"
    assert result.audio_codec == "aac"


@pytest.mark.asyncio
async def test_active_mode_marks_an_undecodable_stream_as_failed(
    tmp_path: Path, media_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _fake_ffprobe(tmp_path, stderr="Invalid data found\n", code=1)
    monkeypatch.setattr(
        health, "ffprobe_command", lambda binary, url, timeout: [sys.executable, str(script), url]
    )
    monkeypatch.setattr(get_settings(), "probe_mode", "active", raising=False)

    result = await health.probe_stream(_stream(media_server))
    assert result.ok is False
    assert result.error_class == "ffprobe"


@pytest.mark.asyncio
async def test_active_mode_treats_a_streamless_manifest_as_failed(
    tmp_path: Path, media_server: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ffprobe exits 0 when a manifest's variants are gone; zero streams must not count as healthy."""
    script = _fake_ffprobe(
        tmp_path, stdout=json.dumps({"streams": [], "format": {"format_name": "hls"}})
    )
    monkeypatch.setattr(
        health, "ffprobe_command", lambda binary, url, timeout: [sys.executable, str(script), url]
    )
    monkeypatch.setattr(get_settings(), "probe_mode", "active", raising=False)

    result = await health.probe_stream(_stream(media_server))
    assert result.ok is False
    assert result.error_class == "ffprobe_no_streams"
