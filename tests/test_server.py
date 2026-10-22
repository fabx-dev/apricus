# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for ApricusServer.feeling_lucky (no display required)."""

import pathlib
from unittest.mock import patch

from apricus import server as srv


def _server_stub():
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.config = {"mode": "MODE_VIDEO", "data_source": {"M1": "/a.mp4", "Default": "/a.mp4"}}
    calls = []
    s.video = lambda path, monitor=None: calls.append(path)
    s._save_config = lambda: None
    s._load_config = lambda: None
    return s, calls


def _run(monitors, files):
    s, calls = _server_stub()
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_video_paths", return_value=list(files)),
    ):
        MM.return_value.get_monitors.return_value = {m: object() for m in monitors}
        s.feeling_lucky()
    return calls


def test_empty_library_does_not_crash_nor_play():
    # Regression: UnboundLocalError writing data_source.Default = null (#272)
    assert _run(["M1"], []) == []


def test_current_video_is_excluded():
    assert _run(["M1"], ["/a.mp4"]) == []


def test_picks_the_other_video():
    assert _run(["M1"], ["/a.mp4", "/b.mp4"]) == ["/b.mp4"]


def test_single_other_video_plays():
    assert _run(["M1"], ["/b.mp4"]) == ["/b.mp4"]


def test_preview_static_forwards_stream_url_to_player():
    """Streams must pre-apply static like videos (regression: streams
    pruned the previous static and pinned nothing -> blue fallback)."""
    from unittest.mock import patch

    calls = []

    class _FakePlayer:
        def preview_static(self, video):
            calls.append(video)

    with patch.object(srv, "get_instance", return_value=_FakePlayer()):
        srv.ApricusServer._preview_static("MODE_STREAM", "https://example.com/live")
        assert calls == ["https://example.com/live"], calls
        srv.ApricusServer._preview_static("MODE_VIDEO", "/v/a.mp4")
        assert calls[-1] == "/v/a.mp4"
        srv.ApricusServer._preview_static("MODE_STREAM", "")
        assert len(calls) == 2, "empty source must not touch the player"


def _setup_stub(monitors):
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.config = {
        "mode": "MODE_NULL",
        "data_source": {
            "M1": "/old.mp4",
            "M2": "/old.mp4",
            "M9-stale": "/old.mp4",
            "Default": "/old.mp4",
        },
        "is_show_systray": False,
    }
    s._load_config = lambda: None
    s._save_config = lambda: None
    s._try_switch_media = lambda mode: True
    s._refresh_systray = lambda mode: None
    return s


def test_video_all_refreshes_every_monitor_key():
    """Single-video apply to all monitors must not leave stale per-monitor
    entries shadowing Default in the player's key→model→Default priority."""
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {
            "M1": object(),
            "M2": object(),
            "M3-live": object(),
        }
        s._setup_player("MODE_VIDEO", "/new.mp4", "")
    assert s.config["data_source"] == {
        "M1": "/new.mp4",
        "M2": "/new.mp4",
        "M9-stale": "/new.mp4",
        "M3-live": "/new.mp4",
        "Default": "/new.mp4",
    }


def test_video_single_monitor_keeps_other_keys():
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {"M1": object(), "M2": object()}
        s._setup_player("MODE_VIDEO", "/new.mp4", "M1")
    assert s.config["data_source"] == {
        "M1": "/new.mp4",
        "M2": "/old.mp4",
        "M9-stale": "/old.mp4",
        "Default": "/new.mp4",
    }


def test_stream_all_refreshes_every_monitor_key():
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {
            "M1": object(),
            "M2": object(),
            "M3-live": object(),
        }
        s._setup_player("MODE_STREAM", "https://example.com/v", "")
    assert s.config["data_source"] == {
        "M1": "https://example.com/v",
        "M2": "https://example.com/v",
        "M9-stale": "https://example.com/v",
        "M3-live": "https://example.com/v",
        "Default": "https://example.com/v",
    }


def test_webpage_all_refreshes_every_monitor_key():
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {
            "M1": object(),
            "M2": object(),
        }
        s._setup_player("MODE_WEBPAGE", "https://example.com/", "")
    assert s.config["data_source"] == {
        "M1": "https://example.com/",
        "M2": "https://example.com/",
        "M9-stale": "https://example.com/",
        "Default": "https://example.com/",
    }


def test_webpage_single_monitor_keeps_other_keys():
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {"M1": object(), "M2": object()}
        s._setup_player("MODE_WEBPAGE", "https://example.com/", "M2")
    assert s.config["data_source"] == {
        "M1": "/old.mp4",
        "M2": "https://example.com/",
        "M9-stale": "/old.mp4",
        "Default": "https://example.com/",
    }


def test_gui_callsites_pass_monitor_to_dbus():
    """Every GUI .stream()/.webpage() call must pass the monitor arg.

    Regression (RC1): the server gained a monitor parameter in 1B but
    five call-sites still called with one arg. D-Bus signatures are
    strict, so those calls failed dispatch — including the default
    GTK3 panel paths.
    """
    import ast
    import pathlib

    bad = []
    for name in ("src/apricus/gui/gtk4app.py",):
        tree = ast.parse(pathlib.Path(name).read_text())
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("stream", "webpage", "video", "playlist")
            ):
                if len(node.args) + len(node.keywords) < 2:
                    bad.append(f"{name}:{node.lineno} .{node.func.attr}()")
    assert not bad, f"call-sites missing the monitor arg: {bad}"


def test_server_import_leaves_gtk_namespaces_free(tmp_path):
    """P0 regression (RC2): importing the server must not pin any Gtk/Gdk
    major, or GTK4 children of the forkserver can never load Gtk 4.0
    (gi allows a single major per process).

    Runs in a FRESH subprocess: importing server, then gtk4app with
    Gtk 4.0, must both succeed — real execution, not AST/grep.
    """
    import subprocess
    import sys

    code = (
        "import sys; sys.path.insert(0, 'src'); "
        "import apricus.server as srv; "
        "bad = [m for m in sys.modules "
        "if m.split('.')[-1] in ('Gtk', 'Gdk', 'Adw', 'WebKit2', 'AppIndicator3')]; "
        "assert not bad, f'server pinned GTK namespaces: {bad}'; "
        "import apricus.gui.gtk4app as g4; "
        "import gi; assert gi.version_info; "
        "from gi.repository import Gtk; "
        "assert Gtk._version == '4.0', Gtk._version; "
        "print('GTK-ISOLATION-OK')"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
        env={**dict(__import__("os").environ), "PYTHONPATH": "src"},
        timeout=120,
    )
    assert "GTK-ISOLATION-OK" in proc.stdout, f"stdout={proc.stdout!r} stderr={proc.stderr[-2000:]}"


def test_show_gui_selects_toolkit_without_spawning(monkeypatch):
    """show_gui must always launch the GTK4 trampoline via the 'spawn'
    multiprocessing context — without starting any real process."""
    seen = []
    methods = []

    class _FakeProcess:
        def __init__(self, name=None, target=None, args=()):
            seen.append((name, target))

        def start(self):
            pass

        def is_alive(self):
            return False

    class _FakeCtx:
        Process = _FakeProcess

    def _get_ctx(method):
        methods.append(method)
        return _FakeCtx()

    monkeypatch.setattr(srv, "Process", _FakeProcess)
    monkeypatch.setattr(srv.mp, "get_context", _get_ctx)
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.version = "test"
    s.pkgdatadir = ""
    s.localedir = ""
    s.gui_process = None
    s.show_gui()
    assert methods == ["spawn"], methods
    assert seen and seen[-1][1] is srv._gui_gtk4_target, seen


def test_gui_trampolines_import_only_inside_child():
    """The server module itself must expose no Gtk symbols: trampolines
    defer their toolkit imports to call time (AST check, no execution)."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/apricus/server.py").read_text())
    top_imports = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                top_imports.append(alias.name)
    banned = [m for m in top_imports if m.split(".")[-1] in ("control", "menu")]
    assert not banned, f"GTK3 modules at server top-level: {banned}"
    banned = [m for m in top_imports if "player" in m]
    assert not banned, f"player modules at server top-level: {banned}"


def test_server_playback_error_proxies_player_last_error(monkeypatch):
    """ApricusServer.playback_error returns player's last_error string (or '')."""

    class _FakePlayer:
        mode = srv.MODE_STREAM
        last_error = "Playback failed before the first frame was shown"

    monkeypatch.setattr(srv, "get_instance", lambda _name: _FakePlayer())
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    assert s.playback_error == "Playback failed before the first frame was shown"

    _FakePlayer.last_error = ""
    assert s.playback_error == ""
    monkeypatch.setattr(srv, "get_instance", lambda _name: None)
    assert s.playback_error == ""


def test_web_player_proxy_without_vlc_methods_never_breaks_server_transitions(monkeypatch):
    """When WebPlayer is active, its D-Bus proxy lacks preview_static,
    switch_media, is_paused_by_user and last_error. Switching to video or
    stream must never raise AttributeError, and is_paused_by_user must
    always return a bool (never None, which breaks D-Bus 'b' marshalling)."""

    class _WebPlayerProxy:
        mode = srv.MODE_WEBPAGE

    monkeypatch.setattr(srv, "get_instance", lambda _name: _WebPlayerProxy())
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.player_process = type("P", (), {"is_alive": lambda self: True})()
    srv.ApricusServer._preview_static(srv.MODE_VIDEO, "/tmp/v.mp4")
    srv.ApricusServer._preview_static(srv.MODE_STREAM, "https://example.com/v")
    assert s._try_switch_media(srv.MODE_VIDEO) is False
    assert s.is_paused_by_user is None
    assert s.playback_error == ""


def test_snap_ipc_roundtrip_methods_and_properties(tmp_path, monkeypatch):
    """snap_ipc must expose properties (get/set) and methods over a Unix
    socket without touching the host D-Bus session bus."""
    from gi.repository import GLib

    from apricus import snap_ipc

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    class _FakeServer:
        def __init__(self):
            self.mode = "MODE_VIDEO"
            self.volume = 50
            self.seen = []

        def video(self, path, monitor):
            self.seen.append((path, monitor))
            return "ok"

    obj = _FakeServer()
    sock = snap_ipc.publish_service("io.github.fabx_dev.Apricus.test", obj)
    ctx = GLib.MainContext.default()
    results = {}

    def _client():
        proxy = snap_ipc.get_service("io.github.fabx_dev.Apricus.test")
        assert proxy is not None
        results["mode"] = proxy.mode
        proxy.volume = 80
        results["vol"] = proxy.volume
        results["call"] = proxy.video("/a.mp4", "HDMI-1")

    import threading
    import time

    t = threading.Thread(target=_client)
    t.start()
    deadline = time.monotonic() + 5
    while t.is_alive() and time.monotonic() < deadline:
        ctx.iteration(False)
        time.sleep(0.01)
    t.join(timeout=1)
    sock.close()
    assert results == {"mode": "MODE_VIDEO", "vol": 80, "call": "ok"}
    assert obj.seen == [("/a.mp4", "HDMI-1")]


def test_pause_flag_roundtrips_in_playlist_mode():
    """In MODE_PLAYLIST the pause flag must round-trip like other modes.

    Regression: the getter/setter only accepted VIDEO/STREAM, so in
    playlist mode the getter returned None (breaking D-Bus bool
    marshalling and freezing the panel toggle) and the setter silently
    dropped the request — Play could never resume.
    """
    from unittest.mock import patch

    s = srv.ApricusServer.__new__(srv.ApricusServer)

    class _Player:
        mode = "MODE_PLAYLIST"
        is_paused_by_user = False

    player = _Player()
    with patch.object(srv, "get_instance", return_value=player):
        assert s.is_paused_by_user is False
        s.is_paused_by_user = True
        assert player.is_paused_by_user is True
        assert s.is_paused_by_user is True
        s.is_paused_by_user = False
        assert player.is_paused_by_user is False


def test_pause_flag_still_none_without_player():
    from unittest.mock import patch

    s = srv.ApricusServer.__new__(srv.ApricusServer)
    with patch.object(srv, "get_instance", return_value=None):
        assert s.is_paused_by_user is None
        s.is_paused_by_user = True  # must not raise


def test_server_property_setters_persist_to_disk(monkeypatch):
    """Toggling mute/volume/flags on ApricusServer (e.g. from the desktop
    context menu or systray) must save the updated config to disk."""
    saved = []
    monkeypatch.setattr(srv, "get_instance", lambda _name: None)
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.config = {
        srv.CONFIG_KEY_VOLUME: 50,
        srv.CONFIG_KEY_MUTE: False,
        srv.CONFIG_KEY_STATIC_WALLPAPER: True,
        srv.CONFIG_KEY_PAUSE_WHEN_MAXIMIZED: True,
        srv.CONFIG_KEY_MUTE_WHEN_MAXIMIZED: False,
    }
    s._save_config = lambda: saved.append(dict(s.config))

    s.is_mute = True
    s.volume = 75
    s.is_static_wallpaper = False
    assert len(saved) == 3
    assert saved[-1][srv.CONFIG_KEY_MUTE] is True
    assert saved[-1][srv.CONFIG_KEY_VOLUME] == 75
    assert saved[-1][srv.CONFIG_KEY_STATIC_WALLPAPER] is False


def test_respawn_player_forces_fresh_process():
    """respawn_player() must rebuild the player even for the same mode."""
    from unittest.mock import patch

    calls = []

    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.config = {"mode": "MODE_VIDEO", "data_source": {"Default": "/a.mp4"}}
    s._load_config = lambda: None

    def fake_setup(mode, data_source=None, monitor=None, force_respawn=False):
        calls.append((mode, force_respawn))

    s._setup_player = fake_setup
    assert s.respawn_player() is True
    assert calls == [("MODE_VIDEO", True)]


def test_respawn_player_noop_without_media_mode():
    """respawn_player() does nothing when no player can run."""
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.config = {"mode": "MODE_NULL"}
    s._load_config = lambda: None
    called = []
    s._setup_player = lambda *a, **k: called.append((a, k))
    assert s.respawn_player() is False
    assert called == []


def test_respawn_player_rejects_unknown_mode():
    """respawn_player() refuses invalid modes instead of spawning."""
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.config = {"mode": "BOGUS"}
    s._load_config = lambda: None
    called = []
    s._setup_player = lambda *a, **k: called.append((a, k))
    assert s.respawn_player() is False
    assert called == []
