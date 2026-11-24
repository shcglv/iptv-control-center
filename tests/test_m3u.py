"""M3U parser and renderer unit tests."""

from __future__ import annotations

from iptvcc.m3u import (
    CONTAINER_HLS,
    CONTAINER_TS,
    RenderedChannel,
    container_for,
    iter_entries,
    parse_attributes,
    render_m3u,
    sanitize_name,
    split_extinf,
)


def test_parse_attributes_quoted_and_unquoted() -> None:
    attrs = parse_attributes('tvg-id="bbc1.uk" group-title=News tvg-logo="http://l/x.png"')
    assert attrs["tvg-id"] == "bbc1.uk"
    assert attrs["group-title"] == "News"
    assert attrs["tvg-logo"] == "http://l/x.png"


def test_split_extinf_keeps_commas_inside_quotes() -> None:
    duration, attrs, name = split_extinf('-1 tvg-name="BBC, One" group-title="News",BBC One HD')
    assert duration == "-1"
    assert attrs["tvg-name"] == "BBC, One"
    assert name == "BBC One HD"


def test_split_extinf_without_attributes() -> None:
    duration, attrs, name = split_extinf("-1,Plain Channel")
    assert duration == "-1"
    assert attrs == {}
    assert name == "Plain Channel"


def test_iter_entries_handles_extgrp_and_vlcopt() -> None:
    lines = [
        "#EXTM3U",
        "#EXTINF:-1 tvg-id=one,First",
        "#EXTVLCOPT:http-user-agent=VLC/3.0.20",
        "#EXTGRP:Group A",
        "http://p/1.ts",
        "",
        "#EXTINF:-1 tvg-id=two,Second",
        "http://p/2.m3u8",
    ]
    entries = list(iter_entries(lines))
    assert len(entries) == 2
    assert entries[0].tvg_id == "one"
    assert entries[0].group_title == "Group A"
    assert entries[0].vlc_opts == ("http-user-agent=VLC/3.0.20",)
    assert entries[0].container == CONTAINER_TS
    assert entries[1].container == CONTAINER_HLS


def test_url_without_extinf_still_yields_an_entry() -> None:
    entries = list(iter_entries(["http://p/loose_stream.ts"]))
    assert len(entries) == 1
    assert entries[0].name == "loose stream"


def test_container_detection() -> None:
    assert container_for("http://p/a.ts") == CONTAINER_TS
    assert container_for("http://p/a.m3u8?token=1") == CONTAINER_HLS
    assert container_for("http://p/a.mpd") == "mpd"
    assert container_for("http://p/noext") == CONTAINER_TS


def test_malformed_lines_are_skipped_not_raised() -> None:
    lines = ["#EXTINF:", "http://p/x.ts", "#EXTINF:-1", ""]
    assert [entry.url for entry in iter_entries(lines)] == ["http://p/x.ts"]


def test_sanitize_name_removes_dangerous_characters() -> None:
    assert sanitize_name('BBC "One", HD; | x') == "BBC One HD x"
    assert sanitize_name("  A\nB\tC  ") == "A B C"


def test_render_m3u_is_deterministic_and_complete() -> None:
    channels = [
        RenderedChannel(
            public_key="abc123",
            display_name="BBC One HD",
            epg_id="bbc1.uk",
            logo_url="http://logo/1.png",
            number=1,
            group="News",
            ext="ts",
        ),
        RenderedChannel(
            public_key="def456",
            display_name="Eurosport, 1",
            epg_id="eurosport",
            number=2,
            group="Sport",
            ext="m3u8",
        ),
    ]
    first = render_m3u(channels, base_url="http://base", token="tok")
    second = render_m3u(channels, base_url="http://base", token="tok")
    assert first == second

    header = first.splitlines()[0]
    assert header.startswith("#EXTM3U")
    assert "url-tvg=" in header
    assert "x-tvg-url=" in header
    assert "tvg-url=" in header

    assert 'tvg-id="bbc1.uk"' in first
    assert 'tvg-chno="1"' in first
    assert 'channel-id="abc123"' in first
    assert 'group-title="News",BBC One HD' in first
    assert "http://base/p/tok/s/abc123.ts" in first
    assert "http://base/p/tok/s/def456.m3u8" in first
    # commas are stripped from names so the #EXTINF separator stays unambiguous
    assert "Eurosport 1" in first


def test_render_m3u_empty_catalog() -> None:
    out = render_m3u([], base_url="http://base", token="tok")
    assert out.endswith("\n")
    assert out.count("\n") == 1
