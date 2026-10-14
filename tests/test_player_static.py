# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for static-wallpaper behavior (headless, all external I/O mocked)."""

import os
import time

from apricus.player import video_player as player_mod
from apricus.player.video_player import VideoPlayer


def _blank_png_bytes():
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 8, 8)
    _ok, data = pixbuf.save_to_bufferv("png", [], [])
    return data


def _player(config=None, wallpaper=None):
    obj = VideoPlayer.__new__(VideoPlayer)
    obj.config = dict(config or {})
    obj.wallpaper = wallpaper
    return obj


class _Wallpaper:
    supports_static = True

    def __init__(self):
        self.applied = []

    def apply_static(self, path):
        self.applied.append(path)


def _wait_for(predicate, timeout=10.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_live_cache_paths_always_keeps_applied_static(tmp_path):
    wb = _Wallpaper()
    obj = _player({"data_source": {"Default": ""}}, wb)
    assert isinstance(obj._live_cache_paths(), set)
    marker = str(tmp_path / "static-current.png")
    obj._last_static_path = marker
    assert os.path.abspath(marker) in obj._live_cache_paths()


def test_preview_static_applies_cached_file(monkeypatch, tmp_path):
    monkeypatch.setattr(player_mod, "CONFIG_DIR", str(tmp_path))
    wb = _Wallpaper()
    obj = _player({}, wb)
    cached = VideoPlayer._static_cache_path("/v/a.mp4")
    assert cached.startswith(str(tmp_path))
    with open(cached, "wb") as f:
        f.write(_blank_png_bytes())
    assert obj.preview_static("/v/a.mp4") is True
    assert wb.applied == [cached]


def test_preview_static_fetches_thumbnail_for_unknown_url(monkeypatch, tmp_path):
    png = _blank_png_bytes()
    wb = _Wallpaper()
    obj = _player({}, wb)
    monkeypatch.setattr(player_mod, "CONFIG_DIR", str(tmp_path))

    class _FakeYdl:
        def __init__(self, params):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"thumbnail": "https://img.example/t.jpg"}

    class _Resp:
        headers = {"Content-Type": "image/jpeg"}

        def iter_content(self, size):
            yield png

        def close(self):
            pass

    monkeypatch.setattr(player_mod.yt_dlp, "YoutubeDL", _FakeYdl)
    monkeypatch.setattr(player_mod.requests, "get", lambda *a, **k: _Resp())
    from gi.repository import GLib

    monkeypatch.setattr(GLib, "idle_add", lambda func, *args: func(*args))
    assert obj.preview_static("https://stream.example/live") is False
    assert _wait_for(lambda: len(wb.applied) == 1)
    assert wb.applied[0].endswith(".png")


def test_preview_static_ignores_unresolvable_url(monkeypatch, tmp_path):
    monkeypatch.setattr(player_mod, "CONFIG_DIR", str(tmp_path))
    wb = _Wallpaper()
    obj = _player({}, wb)

    class _FakeYdl:
        def __init__(self, params):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {}

    monkeypatch.setattr(player_mod.yt_dlp, "YoutubeDL", _FakeYdl)
    # Deterministic: run the generator directly instead of the thread.
    obj._generate_stream_static("https://stream.example/live")
    assert wb.applied == []


def test_extract_remote_frame_uses_forced_8bit_cmd(monkeypatch, tmp_path):
    calls = []

    class _Result:
        returncode = 0

    out = str(tmp_path / "frame.png")
    with open(out, "wb") as f:
        f.write(_blank_png_bytes())

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        with open(out, "wb") as f:
            f.write(_blank_png_bytes())
        return _Result()

    monkeypatch.setattr(player_mod.subprocess, "run", fake_run)
    real_mkstemp = player_mod.tempfile.mkstemp
    monkeypatch.setattr(
        player_mod.tempfile, "mkstemp", lambda **k: (os.open(out, os.O_CREAT | os.O_WRONLY), out)
    )
    try:
        data = VideoPlayer._extract_remote_frame("https://cdn.example/v.mp4")
    finally:
        monkeypatch.setattr(player_mod.tempfile, "mkstemp", real_mkstemp)
    assert data and data.startswith(b"\x89PNG")
    assert "-pix_fmt" in calls[0] and "rgb24" in calls[0]

    class _Fail:
        returncode = 1

    def _fail_run(cmd, **kwargs):
        calls.append(cmd)
        return _Fail()

    monkeypatch.setattr(player_mod.subprocess, "run", _fail_run)
    assert VideoPlayer._extract_remote_frame("https://example.com/page") is None
