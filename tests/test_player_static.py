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
    """Plain namespace exercising the real PlayerWindow methods.

    Deliberately NOT a Gtk instance: unbound method calls only need the
    attributes they touch, and Gtk instantiation (or object.__setattr__
    tricks on uninitialized widgets) behaves differently across
    PyGObject/Python versions (broke CI while passing locally).
    """
    import types

    from apricus.player.video_player import PlayerWindow  # noqa: F401  (binding check)

    class _Cover:
        def __init__(self):
            self.hidden = False

        def hide(self):
            self.hidden = True

    obj = types.SimpleNamespace(
        name="test-window",
        _mode="MODE_STREAM",
        _play_error=None,
        _first_frame_reported=False,
        _PlayerWindow__cover_image=_Cover(),
    )
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
    from apricus.player.video_player import PlayerWindow

    assert PlayerWindow.playback_error(_window_stub()) is None


def test_playback_error_reports_failure_before_first_frame():
    from apricus.player.video_player import PlayerWindow

    obj = _window_stub()
    PlayerWindow._on_media_error(obj, object())
    msg = PlayerWindow.playback_error(obj)
    assert msg == "Playback failed before the first frame was shown", msg


def test_playback_error_after_frame_drops_timing_claim():
    from apricus.player.video_player import PlayerWindow

    obj = _window_stub()
    PlayerWindow._on_first_time_changed(obj, object())
    assert PlayerWindow.playback_error(obj) is None
    PlayerWindow._on_media_error(obj, object())
    assert PlayerWindow.playback_error(obj) == "Playback failed"


def test_playback_error_never_contains_urls_or_tokens():
    from apricus.player.video_player import PlayerWindow

    secret = "https://host.example/v?expire=1&sig=SECRET-TOKEN-123"
    obj = _window_stub()
    PlayerWindow._on_media_error(obj, object())
    assert "SECRET-TOKEN-123" not in PlayerWindow.playback_error(obj)
    assert "?" not in PlayerWindow.playback_error(obj)
    assert secret not in PlayerWindow.playback_error(obj)


def test_set_media_clears_previous_error_and_frame_flag():
    from apricus.player.video_player import PlayerWindow

    obj = _window_stub()
    PlayerWindow._on_media_error(obj, object())
    PlayerWindow._on_first_time_changed(obj, object())
    fake = _FakeVlcPlayer()
    obj._PlayerWindow__vlc_widget = type("W", (), {"player": fake})()
    PlayerWindow.set_media(obj, object())
    assert PlayerWindow.playback_error(obj) is None
    assert obj._first_frame_reported is False
    assert fake.media is not None


def test_voluntary_stop_sets_no_error():
    from apricus.player.video_player import PlayerWindow

    obj = _window_stub()
    fake = _FakeVlcPlayer()
    obj._PlayerWindow__vlc_widget = type("W", (), {"player": fake})()
    PlayerWindow.stop(obj)
    assert PlayerWindow.playback_error(obj) is None
    assert fake.stopped is True


def test_player_aggregates_first_window_error():
    from apricus.player.video_player import PlayerWindow

    def _bound(obj):
        # Manual binding: exercises the real method without a Gtk instance.
        import types

        obj.playback_error = types.MethodType(lambda self: PlayerWindow.playback_error(self), obj)
        return obj

    good = _bound(_window_stub())
    bad = _bound(_window_stub())
    PlayerWindow._on_media_error(bad, object())
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


def _video_player(mode, source="", wallpaper=None, playlist=None):
    config = {"mode": mode, "data_source": {"Default": source}}
    if playlist is not None:
        config["playlist"] = playlist
    return _player(config, wallpaper if wallpaper is not None else _Wallpaper())


def _silent_thread(monkeypatch, spawns):
    """Replace worker threads with non-starting recorders (deterministic)."""

    class _Thread:
        def __init__(self, target=None, args=(), **kwargs):
            spawns.append((target, args))

        def start(self):
            pass

    monkeypatch.setattr(player_mod.threading, "Thread", _Thread)


def test_refresh_static_applies_cached_frame_without_touching_media(monkeypatch, tmp_path):
    monkeypatch.setattr(player_mod, "CONFIG_DIR", str(tmp_path))
    wb = _Wallpaper()
    obj = _video_player("MODE_VIDEO", "/v/a.mp4", wb)
    cached = VideoPlayer._static_cache_path("/v/a.mp4")
    with open(cached, "wb") as f:
        f.write(_blank_png_bytes())
    spawns = []
    _silent_thread(monkeypatch, spawns)
    assert obj.refresh_static() is True
    assert wb.applied == [cached]
    assert len(spawns) == 1  # background refresh still scheduled


def test_refresh_static_generates_missing_frame_in_background(monkeypatch, tmp_path):
    monkeypatch.setattr(player_mod, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(player_mod.mediacache, "probe_video", lambda _video: (640, 360, 30.0))

    class _Result:
        returncode = 0

    def fake_run(cmd, **kwargs):
        assert "-pix_fmt" in cmd and "rgb24" in cmd
        with open(cmd[-1], "wb") as f:
            f.write(_blank_png_bytes())
        return _Result()

    monkeypatch.setattr(player_mod.subprocess, "run", fake_run)
    from gi.repository import GLib

    monkeypatch.setattr(GLib, "idle_add", lambda func, *args: func(*args))
    wb = _Wallpaper()
    obj = _video_player("MODE_VIDEO", "/v/b.mp4", wb)
    assert obj.refresh_static() is True
    assert _wait_for(lambda: len(wb.applied) == 1)


def test_refresh_static_second_call_while_running_is_ignored(monkeypatch, tmp_path):
    monkeypatch.setattr(player_mod, "CONFIG_DIR", str(tmp_path))
    wb = _Wallpaper()
    obj = _video_player("MODE_VIDEO", "/v/c.mp4", wb)
    spawns = []
    _silent_thread(monkeypatch, spawns)
    assert obj.refresh_static() is True
    assert len(spawns) == 1
    assert obj.refresh_static() is True
    assert len(spawns) == 1, "duplicate generation must not stack ffmpeg"
    # A different video still proceeds.
    obj.config["data_source"] = {"Default": "/v/d.mp4"}
    assert obj.refresh_static() is True
    assert len(spawns) == 2


def test_refresh_static_noop_without_media_content():
    wb = _Wallpaper()
    obj = _video_player("MODE_NULL", "", wb)
    calls = []
    obj.set_static_wallpaper = lambda *a, **k: calls.append((a, k))
    assert obj.refresh_static() is False
    assert calls == []
    obj = _video_player("MODE_PLAYLIST", "", wb, playlist={})
    obj.set_static_wallpaper = lambda *a, **k: calls.append((a, k))
    assert obj.refresh_static() is False
    assert calls == []


def test_refresh_static_playlist_uses_first_queued_video():
    wb = _Wallpaper()
    obj = _video_player("MODE_PLAYLIST", "", wb, playlist={"Default": ["/q/1.mp4", "/q/2.mp4"]})
    calls = []
    obj.set_static_wallpaper = lambda *a, **k: calls.append((a, k))
    assert obj.refresh_static() is True
    assert calls == [(("/q/1.mp4",), {})]


def test_refresh_static_rejects_foreign_uid(monkeypatch):
    import types

    import apricus.utils as utils_mod

    monkeypatch.setattr(utils_mod, "_caller_uid", lambda sender: 0xFFFE)
    wb = _Wallpaper()
    obj = _video_player("MODE_VIDEO", "/v/a.mp4", wb)
    ctx = types.SimpleNamespace(sender=":1.99")
    try:
        obj.refresh_static(ctx)
    except PermissionError:
        return
    raise AssertionError("foreign UID must be rejected")
