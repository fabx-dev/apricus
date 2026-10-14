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
    monkeypatch.setattr("apricus.utils.fetch_public", lambda *a, **k: (png, 200, "image/png", "ok"))
    monkeypatch.setattr("apricus.utils.public_http_url", lambda url: url)
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
    monkeypatch.setattr("apricus.utils.public_http_url", lambda url: url)
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
    monkeypatch.setattr("apricus.utils.public_http_url", lambda url: url)
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


def _window_stub(**attrs):
    from apricus.player.video_player import PlayerWindow

    class _Cover:
        def __init__(self):
            self.hidden = False

        def hide(self):
            self.hidden = True

    obj = PlayerWindow.__new__(PlayerWindow)
    obj.name = "test-window"
    obj._mode = "MODE_STREAM"
    obj._play_error = None
    obj._first_frame_reported = False
    object.__setattr__(obj, "_PlayerWindow__cover_image", _Cover())
    for key, value in attrs.items():
        setattr(obj, key, value)
    return obj


class _FakeVlcPlayer:
    def __init__(self):
        self.media = None
        self.stopped = False

    def set_media(self, media):
        self.media = media

    def stop(self):
        self.stopped = True


def test_playback_error_none_when_healthy():
    assert _window_stub().playback_error() is None


def test_playback_error_reports_failure_before_first_frame():
    obj = _window_stub()
    obj._on_media_error(object())
    msg = obj.playback_error()
    assert msg == "Playback failed before the first frame was shown", msg


def test_playback_error_after_frame_drops_timing_claim():
    obj = _window_stub()
    obj._on_first_time_changed(object())
    assert obj.playback_error() is None
    obj._on_media_error(object())
    assert obj.playback_error() == "Playback failed"


def test_playback_error_never_contains_urls_or_tokens():
    secret = "https://host.example/v?expire=1&sig=SECRET-TOKEN-123"
    obj = _window_stub()
    obj._on_media_error(object())
    assert "SECRET-TOKEN-123" not in obj.playback_error()
    assert "?" not in obj.playback_error()
    assert secret not in obj.playback_error()


def test_set_media_clears_previous_error_and_frame_flag():
    from apricus.player.video_player import PlayerWindow

    obj = _window_stub()
    obj._on_media_error(object())
    obj._on_first_time_changed(object())
    fake = _FakeVlcPlayer()
    object.__setattr__(obj, "_PlayerWindow__vlc_widget", type("W", (), {"player": fake})())
    PlayerWindow.set_media(obj, object())
    assert obj.playback_error() is None
    assert obj._first_frame_reported is False
    assert fake.media is not None


def test_voluntary_stop_sets_no_error():
    from apricus.player.video_player import PlayerWindow

    obj = _window_stub()
    fake = _FakeVlcPlayer()
    object.__setattr__(obj, "_PlayerWindow__vlc_widget", type("W", (), {"player": fake})())
    PlayerWindow.stop(obj)
    assert obj.playback_error() is None
    assert fake.stopped is True


def test_player_aggregates_first_window_error():
    good = _window_stub()
    bad = _window_stub()
    bad._on_media_error(object())
    player = VideoPlayer.__new__(VideoPlayer)
    player.windows = {"m1": good, "m2": bad}
    assert player.playback_error() == "Playback failed before the first frame was shown"
    player.windows = {"m1": good}
    assert player.playback_error() is None


def test_error_handler_attached_in_window_init():
    """PlayerWindow must listen for EncounteredError, verified by AST so
    the check runs headless (same pattern as the legacy call-site test)."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/apricus/player/video_player.py").read_text())
    inits = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    ]
    dump = "\n".join(ast.dump(n) for n in inits)
    assert "MediaPlayerEncounteredError" in dump, "error event not wired in __init__"
    assert "_on_media_error" in dump
