# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""M1 invariants: all external desktop I/O mocked; private Unix IPC is real."""

import copy
import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from gi.repository import GLib

from apricus import server, snap_ipc, stop_support
from apricus.child_jobs import ChildJobs
from apricus.commons import CONFIG_TEMPLATE, MODE_PLAYLIST, MODE_STREAM, MODE_VIDEO, MODE_WEBPAGE


@pytest.fixture
def stopped_server(monkeypatch):
    obj = server.ApricusServer.__new__(server.ApricusServer)
    disk = copy.deepcopy(CONFIG_TEMPLATE)
    disk.update(
        mode=MODE_VIDEO,
        data_source={"Default": "/selected.mp4"},
        audio_volume=67,
        is_mute=True,
        playlist={"Default": ["/one.mp4", "/two.mp4"]},
    )
    obj.config = copy.deepcopy(disk)
    obj._load_config = lambda: setattr(obj, "config", copy.deepcopy(disk))
    obj._save_config = lambda **_: disk.update(copy.deepcopy(obj.config))
    obj._refresh_systray = Mock()
    obj._apply_stopped_background = Mock()
    obj.player_process = None
    obj._player_count = 0
    obj._try_switch_media = Mock(return_value=False)
    monkeypatch.setattr(server, "get_instance", lambda _: None)
    monkeypatch.setattr(server, "Monitors", lambda: SimpleNamespace(get_monitors=lambda: {}))
    return obj, disk


@pytest.mark.parametrize("mode", [MODE_VIDEO, MODE_PLAYLIST, MODE_STREAM, MODE_WEBPAGE])
def test_stop_resume_idempotent_for_each_mode(stopped_server, monkeypatch, mode):
    obj, disk = stopped_server
    disk["mode"] = mode
    before = copy.deepcopy(disk)
    obj.stop_playback()
    obj.stop_playback()
    assert disk["is_stopped"] is True
    assert obj.playback_state == "stopped"
    assert not obj.is_playing
    obj.start_playback()  # legacy automatic resume never clears STOP
    obj.reload()
    obj.respawn_player()
    assert disk["is_stopped"] is True
    assert obj.player_process is None
    proc = Mock()
    monkeypatch.setattr(server, "Process", Mock(return_value=proc))
    obj.resume_playback()
    obj.resume_playback()
    proc.start.assert_called_once()
    assert disk["is_stopped"] is False
    assert {k: v for k, v in disk.items() if k != "is_stopped"} == {
        k: v for k, v in before.items() if k != "is_stopped"
    }


def test_stopped_selection_changes_and_restart_do_not_spawn(stopped_server, monkeypatch):
    obj, disk = stopped_server
    spawn = Mock()
    monkeypatch.setattr(server, "Process", spawn)
    obj.stop_playback()
    for mode in [MODE_VIDEO, MODE_STREAM, MODE_WEBPAGE]:
        obj._setup_player(mode, "/new-selection")
        assert disk["data_source"]["Default"] == "/new-selection"
        assert obj.playback_state == "stopped"
    obj.playlist(["/other.mp4"], "all")
    obj.config = {}  # simulate a fresh server loading persisted state
    obj._load_config()
    obj.reload()
    spawn.assert_not_called()


def test_static_option_off_is_never_overridden(stopped_server, monkeypatch):
    obj, disk = stopped_server
    disk["is_static_wallpaper"] = False
    obj._load_config()
    backend = Mock()
    monkeypatch.setattr("apricus.backends.create_wallpaper_backend", backend)
    server.ApricusServer._apply_stopped_background(obj)
    backend.assert_not_called()


def test_stopped_background_cache_and_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(stop_support, "CONFIG_DIR", str(tmp_path))
    config = {"mode": MODE_VIDEO, "data_source": {"Default": "/selected.mp4"}}
    fallback = stop_support.stopped_background(config)
    from gi.repository import GdkPixbuf

    # Validate the actual fallback image, not only its extension/magic.
    assert GdkPixbuf.Pixbuf.new_from_file(fallback).get_width() == 1
    cached = stop_support.static_cache_path("/selected.mp4")
    with open(cached, "wb") as stream, open(fallback, "rb") as source:
        stream.write(source.read())
    assert stop_support.stopped_background(config) == cached


def test_queue_callback_and_automatic_resume_are_blocked_in_stop(monkeypatch):
    from apricus.player.video_player import VideoPlayer

    monkeypatch.setattr(
        "apricus.player.video_player.ConfigUtil",
        lambda: SimpleNamespace(load=lambda: {"is_stopped": True}),
    )
    obj = VideoPlayer.__new__(VideoPlayer)
    window = SimpleNamespace(_queue=["a", "b"], _queue_index=0)
    obj._play_queue_item = Mock()
    obj._on_media_end(window)
    assert window._queue_index == 0
    obj._play_queue_item.assert_not_called()
    obj._should_playback_start = Mock()
    obj.start_playback()
    obj._should_playback_start.assert_not_called()


def test_async_frame_cannot_apply_after_state_or_wallpaper_change(monkeypatch, tmp_path):
    from apricus.player import video_player as video

    obj = video.VideoPlayer.__new__(video.VideoPlayer)
    obj.config = {"is_static_wallpaper": True}
    obj.wallpaper = Mock()
    obj._static_token = 1
    queued = []
    monkeypatch.setattr(video.GLib, "idle_add", lambda f: queued.append(f))
    monkeypatch.setattr(video, "ConfigUtil", lambda: SimpleNamespace(load=lambda: {}))
    temporary, cached = tmp_path / "worker.tmp.png", tmp_path / "cached.png"
    temporary.write_bytes(b"obsolete")
    cached.write_bytes(b"current")
    obj._publish_generated(str(temporary), str(cached), 1)
    obj._static_token = 2  # media change / reload / disable / quit
    queued.pop()()
    assert cached.read_bytes() == b"current"
    assert not temporary.exists()
    obj.wallpaper.apply_static.assert_not_called()
    temporary.write_bytes(b"obsolete")
    obj._publish_generated(str(temporary), str(cached), 2)
    obj._closing = True
    queued.pop()()
    assert cached.read_bytes() == b"current"
    obj.wallpaper.apply_static.assert_not_called()
    obj._closing = False
    obj.config["is_static_wallpaper"] = False
    obj._apply_static_wallpaper("old.png")
    obj.wallpaper.apply_static.assert_not_called()


def test_stop_cancels_and_reaps_its_own_ffmpeg_like_job():
    import shutil

    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg is not installed")
    jobs = ChildJobs()
    errors = []

    def work():
        try:
            jobs.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-re",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=64x64:rate=10",
                    "-f",
                    "null",
                    "-",
                ],
                stdout=-3,
                stderr=-3,
            )
        except OSError as error:
            errors.append(error)

    thread = threading.Thread(target=work)
    thread.start()
    deadline = time.monotonic() + 5
    while not jobs.children and time.monotonic() < deadline:
        time.sleep(0.01)
    assert jobs.children
    child = next(iter(jobs.children))
    from unittest.mock import patch

    from apricus.player.base_player import BasePlayer
    from apricus.player.video_player import VideoPlayer

    player = VideoPlayer.__new__(VideoPlayer)
    player._static_jobs = jobs
    player.wallpaper = Mock()
    player.windows = {}
    player.config = {"data_source": {}}
    player.active_handler = player.window_state = None
    with patch.object(BasePlayer, "quit_player", lambda self: None):
        player.quit_player(False)
    assert player._closing
    thread.join(3)
    assert not thread.is_alive()
    assert child.poll() is not None
    assert not os.path.exists(f"/proc/{child.pid}")
    with pytest.raises(OSError):
        jobs.run(["must-not-start"])


@pytest.mark.parametrize("change", ["source", "disabled", "stop"])
def test_disk_change_invalidates_a_frame_before_reload_callback(monkeypatch, tmp_path, change):
    from apricus.player import video_player as video

    latest = {"mode": MODE_VIDEO, "data_source": {"Default": "/a.mp4"}, "is_static_wallpaper": True}
    monkeypatch.setattr(video, "ConfigUtil", lambda: SimpleNamespace(load=lambda: latest))
    obj = video.VideoPlayer.__new__(video.VideoPlayer)
    obj.config = copy.deepcopy(latest)
    obj._static_token = 1
    obj.wallpaper = Mock()
    callbacks = []
    monkeypatch.setattr(video.GLib, "idle_add", lambda callback: callbacks.append(callback))
    pending, cached = tmp_path / "pending.png", tmp_path / "cached.png"
    pending.write_bytes(b"obsolete")
    obj._publish_generated(str(pending), str(cached), 1, obj._static_selection())
    if change == "source":
        latest["data_source"]["Default"] = "/b.mp4"
    elif change == "disabled":
        latest["is_static_wallpaper"] = False
    else:
        latest["is_stopped"] = True
    # The server wrote config, but player's reload callback has not run yet.
    callbacks.pop()()
    assert not cached.exists()
    obj.wallpaper.apply_static.assert_not_called()


def _sleep_in_session():
    os.setsid()
    time.sleep(60)


def test_owned_process_is_reaped():
    # The exact finish_process used by server STOP, not a media.release mock.
    import multiprocessing as mp

    process = mp.get_context("spawn").Process(target=_sleep_in_session)
    process.start()
    deadline = time.monotonic() + 5
    while os.getpgid(process.pid) != process.pid and time.monotonic() < deadline:
        time.sleep(0.01)
    assert os.getpgid(process.pid) == process.pid
    stop_support.finish_process(process)
    assert not process.is_alive()
    assert not os.path.exists(f"/proc/{process.pid}")


def test_snap_ipc_stop_resume_roundtrip(stopped_server, monkeypatch, tmp_path):
    obj, disk = stopped_server
    obj.reload = Mock()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    sock = snap_ipc.publish_service("io.github.fabx_dev.Apricus.m1test", obj)
    result = []

    def client():
        proxy = snap_ipc.get_service("io.github.fabx_dev.Apricus.m1test")
        proxy.stop_playback()
        proxy.stop_playback()
        result.append(proxy.playback_state)
        proxy.resume_playback()
        proxy.resume_playback()

    thread = threading.Thread(target=client)
    thread.start()
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 5
    while thread.is_alive() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.005)
    thread.join(1)
    sock.close()
    assert result == ["stopped"]
    assert not disk["is_stopped"]
    obj.reload.assert_called_once()


def test_stale_config_writer_cannot_clear_or_reintroduce_stop(monkeypatch, tmp_path):
    from apricus import utils

    monkeypatch.setattr(utils, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(utils, "CONFIG_PATH", str(tmp_path / "config.json"))
    store = utils.ConfigUtil()
    selected = copy.deepcopy(CONFIG_TEMPLATE)
    selected["data_source"] = {"Default": "/selected.mp4"}
    store.save(selected, manual_stop=False)
    stale = copy.deepcopy(selected)
    store.save(selected, manual_stop=True)
    store.save(stale)  # playlist/GUI read before STOP, wrote after it
    assert store.load()["is_stopped"] is True
    old_stopped = copy.deepcopy(stale)
    store.save(selected, manual_stop=False)
    store.save(old_stopped)  # stale save after RESUME must not re-stop
    assert store.load()["is_stopped"] is False


def test_web_snapshot_after_stop_is_discarded(monkeypatch):
    # WebKit pins Gtk3, consistent with the video test process.
    from apricus.player import web_player as web

    monkeypatch.setattr(
        web, "ConfigUtil", lambda: SimpleNamespace(load=lambda: {"is_stopped": True})
    )
    player = web.WebPlayer.__new__(web.WebPlayer)
    snapshot = Mock()
    player._save_web_snapshot("/old.html", snapshot)
    snapshot.savev.assert_not_called()


def test_gui_explicit_commands_are_wired_without_toggle_api():
    import subprocess
    import sys

    code = """
from types import SimpleNamespace
from unittest.mock import Mock, patch
from apricus.gui.gtk4app import ControlPanel
import apricus.gui.gtk4app as gui
panel = ControlPanel.__new__(ControlPanel)
server = SimpleNamespace(playback_state="stopped", resume_playback=Mock(), stop_playback=Mock())
panel._server = lambda: server
panel._refresh_all = Mock()
panel.np_stop_btn = Mock()
panel.np_play_btn = Mock()
def thread(target, **kw):
    return SimpleNamespace(start=target)
with patch.object(gui.threading, "Thread", thread), patch.object(gui.GLib, "idle_add", lambda f, *a: f(*a)):
    panel.on_stop_resume()
    server.resume_playback.assert_called_once()
    server.playback_state = "playing"
    panel.on_stop_resume()
    server.stop_playback.assert_called_once()
assert panel._refresh_all.call_count == 2
"""
    subprocess.run(
        [sys.executable, "-c", code],
        check=True,
        timeout=30,
        env={**os.environ, "PYTHONPATH": "src"},
    )
