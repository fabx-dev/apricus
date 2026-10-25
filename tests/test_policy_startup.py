"""Restart/source replacement with delayed player activation, without a desktop."""

import copy
import json
import time
from types import SimpleNamespace

import pytest

from apricus import commons, server, utils
from apricus.player.video_player import PlayerWindow, VideoPlayer


class Event:
    def __init__(self):
        self.ready = False

    def set(self):
        self.ready = True

    def is_set(self):
        return self.ready


class Window:
    """Use the real fade/pause methods with a manually completed fade and VLC stub."""

    pause_fade = PlayerWindow.pause_fade
    play_fade = PlayerWindow.play_fade
    pause = PlayerWindow.pause
    is_playing = PlayerWindow.is_playing

    def __init__(self):
        self.playing = False
        self.completion = None
        self.pause_requests = 0
        self.play_requests = 0
        self._PlayerWindow__vlc_widget = SimpleNamespace(
            player=SimpleNamespace(is_playing=lambda: self.playing, pause=self._pause)
        )
        self.fade = SimpleNamespace(cancel=lambda: None, start=self._fade)

    def _pause(self):
        self.playing = False

    def _fade(self, **kwargs):
        if kwargs.get("complete_callback"):
            self.pause_requests += 1
        self.completion = kwargs.get("complete_callback")

    def complete(self):
        if self.completion:
            callback, self.completion = self.completion, None
            callback()

    def get_volume(self):
        return 50

    def set_volume(self, value):
        pass

    def set_mute(self, value):
        self.muted = value

    def play(self):
        self.play_requests += 1
        self.playing = True


class Monitor(str):
    def is_primary(self):
        return True


@pytest.fixture
def lifecycle(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"never decoded")
    for module in (commons, utils):
        monkeypatch.setattr(module, "CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(module, "CONFIG_PATH", str(path))
        monkeypatch.setattr(module, "LEGACY_CONFIG_PATH", str(tmp_path / "absent.json"))
    monkeypatch.setitem(commons.CONFIG_TEMPLATE, "data_source", {"Default": str(clip)})
    monkeypatch.setitem(commons.CONFIG_TEMPLATE, "playlist", {"Default": []})
    monkeypatch.setattr("apricus.monitor.MonitorInfo.monitors", lambda *args: [])
    monkeypatch.setattr(server.mp, "set_start_method", lambda *args: None)
    monkeypatch.setattr(server.mp, "Event", Event)
    monkeypatch.setattr(server.signal, "signal", lambda *args: None)
    monkeypatch.setattr(server.setproctitle, "setproctitle", lambda *args: None)
    monkeypatch.setattr(server, "EndSessionHandler", lambda *args: None)
    monkeypatch.setattr(server.ApricusServer, "_start_environment_sensors", lambda self: None)
    monkeypatch.setattr(server.ApricusServer, "_apply_stopped_background", lambda self: None)
    monkeypatch.setattr(server.ApricusServer, "_preview_static", lambda *args: None)
    monkeypatch.setattr(server, "Monitors", lambda: SimpleNamespace(get_monitors=lambda: []))
    tasks, processes = [], []
    registry = {"player": None}

    def schedule(_ms, callback, *args):
        tasks.append((callback, args))
        return len(tasks)

    monkeypatch.setattr(server.GLib, "timeout_add", schedule)
    monkeypatch.setattr(server, "get_instance", lambda name: registry["player"])

    class Process:
        def __init__(self, **kwargs):
            self.args = kwargs.get("args", ())
            self.pid = 900000 + len(processes)
            self.alive = False
            self.player = None
            processes.append(self)

        def start(self):
            self.alive = True
            self.player = VideoPlayer.__new__(VideoPlayer)
            self.player.config = utils.ConfigUtil().load()
            self.player.is_paused_by_user = self.args[1] if len(self.args) > 1 else False
            self.player.windows = {Monitor("Default"): Window()}

        def activate(self):
            # Source-start decision is the real player's local actuator veto.
            self.player.windows["Default"].playing = self.player._should_playback_start()
            registry["player"] = self.player
            if self.args:
                self.args[0].set()

        def is_alive(self):
            return self.alive

        def terminate(self):
            self.alive = False
            if registry["player"] is self.player:
                registry["player"] = None

        kill = terminate

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(server, "Process", Process)

    def pump():
        pending, tasks[:] = list(tasks), []
        for callback, args in pending:
            if callback(*args):
                tasks.append((callback, args))

    def seed(pause=False, stop=False, mute=False):
        cfg = copy.deepcopy(commons.CONFIG_TEMPLATE)
        cfg.update(
            mode="MODE_VIDEO",
            is_show_systray=False,
            is_stopped=stop,
            policy_manual_pause=pause,
            is_mute=mute,
        )
        utils.ConfigUtil().save(cfg, manual_stop=stop, manual_pause=pause)

    def start():
        return server.ApricusServer("test", "", "", SimpleNamespace(reset=False, background=True))

    def complete(p):
        p.player.windows["Default"].complete()

    return SimpleNamespace(
        seed=seed,
        start=start,
        processes=processes,
        pump=pump,
        complete=complete,
        registry=registry,
        path=path,
        tasks=tasks,
    )


def test_persisted_pause_restart_waits_for_new_player_and_actuates(lifecycle):
    x = lifecycle
    x.seed()
    old = x.start()
    p0 = x.processes[-1]
    p0.activate()
    x.pump()
    old.pause_playback()
    x.complete(p0)
    assert p0.player.playback_state == "paused"
    assert json.loads(x.path.read_text())["policy_manual_pause"] is True
    p0.terminate()
    del old
    new = x.start()
    p1 = x.processes[-1]
    assert p1 is not p0
    assert new.engine_snapshot["desired"] == "paused"
    x.pump()  # Not activated/published yet; must not mark it as applied.
    p1.activate()
    assert p1.player._should_playback_start() is False
    # Even a queued backend start must be corrected by the real pause/fade
    # actuator, rather than only changing a logical flag.
    p1.player.windows["Default"].playing = True
    x.pump()
    assert p1.player.playback_state == "playing"  # Fade not completed yet.
    x.complete(p1)
    assert p1.player.windows["Default"].pause_requests == 1
    assert p1.player.playback_state == "paused"
    p1.player.start_playback()  # A late source-autoplay call must remain vetoed.
    assert p1.player.playback_state == "paused"
    assert new._apply_policy_output(new.policy.compute()) is False
    assert p1.player.windows["Default"].pause_requests == 1
    new.start_playback()
    assert new.engine_snapshot["manual_pause"] is False
    assert json.loads(x.path.read_text())["policy_manual_pause"] is False
    assert p1.player._should_playback_start() is True
    assert p1.player.playback_state == "playing"
    assert p1.player.windows["Default"].play_requests == 1


def test_new_wallpaper_player_cannot_inherit_old_dedup(lifecycle):
    x = lifecycle
    x.seed(pause=True)
    obj = x.start()
    old = x.processes[-1]
    old.activate()
    x.pump()
    x.complete(old)
    assert obj._last_applied == ("paused", False)
    old.terminate()
    other = x.path.parent / "another.mp4"
    other.write_bytes(b"never decoded")
    obj._setup_player("MODE_VIDEO", str(other), "", force_respawn=True)
    fresh = x.processes[-1]
    fresh.activate()
    x.pump()
    x.complete(fresh)
    assert fresh.player.windows["Default"].pause_requests == 1
    assert fresh.player.playback_state == "paused"
    assert obj.engine_snapshot["manual_pause"] is True


def test_fresh_player_without_pause_and_persistent_stop(lifecycle):
    x = lifecycle
    x.seed()
    x.start()
    fresh = x.processes[-1]
    fresh.activate()
    x.pump()
    assert fresh.player.playback_state == "playing"
    assert fresh.player.windows["Default"].pause_requests == 0
    assert fresh.player.windows["Default"].play_requests == 0
    fresh.terminate()
    x.seed(stop=True)
    stopped = x.start()
    assert len(x.processes) == 1
    assert stopped.engine_snapshot["desired"] == "stopped"
    assert stopped.player_process is None


def test_latest_play_intent_while_bootstrapping_paused_player(lifecycle):
    x = lifecycle
    x.seed(pause=True, mute=True)
    obj = x.start()
    fresh = x.processes[-1]
    assert obj._last_applied is None
    obj.start_playback()  # IPC is unavailable: intent persists, no false ACK.
    assert obj._last_applied is None
    fresh.activate()
    assert fresh.player.playback_state == "paused"  # Bootstrap held autoplay.
    x.pump()
    assert fresh.player.playback_state == "playing"
    assert fresh.player.windows["Default"].muted is True
    assert obj.engine_snapshot["manual_mute"] is True
    assert json.loads(x.path.read_text())["is_mute"] is True


def test_readiness_budget_and_stale_callbacks(lifecycle):
    x = lifecycle
    x.seed(pause=True)
    obj = x.start()
    old = x.processes[-1]
    assert obj._last_applied is None
    assert obj._reconcile_new_player(old, old.args[0], True, time.monotonic() - 1) is False
    assert obj._last_applied is None
    old.terminate()
    obj._setup_player("MODE_VIDEO", force_respawn=True)
    fresh = x.processes[-1]
    old.args[0].set()
    assert obj._reconcile_new_player(old, old.args[0], True, time.monotonic() + 30) is False
    fresh.activate()
    x.pump()
    x.complete(fresh)
    assert fresh.player.playback_state == "paused"
    assert fresh.player.windows["Default"].pause_requests == 1


def test_failed_actuation_is_not_deduplicated(lifecycle, monkeypatch):
    x = lifecycle
    x.seed(pause=True)
    obj = x.start()
    fresh = x.processes[-1]
    fresh.activate()
    real_pause = fresh.player.pause_playback

    def fail():
        raise server.GLib.Error("unavailable")

    monkeypatch.setattr(fresh.player, "pause_playback", fail)
    x.pump()
    assert obj._last_applied is None
    monkeypatch.setattr(fresh.player, "pause_playback", real_pause)
    x.pump()
    x.complete(fresh)
    assert obj._last_applied == ("paused", False)
    assert fresh.player.playback_state == "paused"


def test_video_readiness_is_after_source_activation(lifecycle, monkeypatch):
    from apricus.player.base_player import BasePlayer

    x = lifecycle
    x.seed(pause=True)
    x.start()
    fresh = x.processes[-1]
    event = fresh.args[0]
    fresh.player._policy_ready = event
    steps = []
    monkeypatch.setattr(BasePlayer, "do_activate", lambda self: steps.append("windows"))

    def set_source(self, value):
        assert event.is_set() is False
        steps.append("source")

    monkeypatch.setattr(VideoPlayer, "data_source", property(fset=set_source))
    fresh.player.do_activate()
    assert steps == ["windows", "source"]
    assert event.is_set() is True
    assert fresh.player._policy_ready is None


def test_mute_failure_does_not_repeat_successful_pause_fade(lifecycle, monkeypatch):
    x = lifecycle
    x.seed(pause=True, mute=True)
    obj = x.start()
    fresh = x.processes[-1]
    fresh.activate()
    original = VideoPlayer.is_mute
    unavailable = [True]

    def set_mute(self, value):
        if unavailable[0]:
            raise server.GLib.Error("mute unavailable")
        original.fset(self, value)

    monkeypatch.setattr(VideoPlayer, "is_mute", property(original.fget, set_mute))
    x.pump()
    assert obj._last_applied == ("paused", None)
    assert fresh.player.windows["Default"].pause_requests == 1
    unavailable[0] = False
    x.pump()
    x.complete(fresh)
    assert obj._last_applied == ("paused", True)
    assert fresh.player.windows["Default"].pause_requests == 1
    assert fresh.player.windows["Default"].muted is True


def test_stop_cancels_pending_player_readiness(lifecycle):
    x = lifecycle
    x.seed(pause=True)
    obj = x.start()
    fresh = x.processes[-1]
    obj.stop_playback()
    fresh.args[0].set()
    x.pump()
    assert obj.player_process is None
    assert obj.engine_snapshot["desired"] == "stopped"
    assert fresh.player.windows["Default"].pause_requests == 0


def test_web_readiness_after_initial_freeze(lifecycle, monkeypatch):
    from apricus.player import web_player as web
    from apricus.player.base_player import BasePlayer

    event = Event()
    app = web.WebPlayer.__new__(web.WebPlayer)
    app.config = {"data_source": {"Default": "https://example.invalid/"}}
    app._paused = True
    app._policy_ready = event
    frozen = []
    monkeypatch.setattr(BasePlayer, "do_activate", lambda self: None)

    def set_source(self, value):
        assert not event.is_set()
        self.windows = {"m": SimpleNamespace(set_frozen=frozen.append)}

    monkeypatch.setattr(web.WebPlayer, "data_source", property(fset=set_source))
    app.do_activate()
    assert frozen == [True]
    assert event.is_set()


@pytest.mark.parametrize("kind", ["video", "web"])
@pytest.mark.parametrize("paused", [False, True])
def test_player_entrypoint_installs_server_bootstrap_before_run(monkeypatch, kind, paused):
    import faulthandler

    from apricus.player import video_player, web_player

    module = video_player if kind == "video" else web_player
    app = SimpleNamespace()
    event = Event()
    seen = []

    def run(args):
        seen.append(app.is_paused_by_user if kind == "video" else app._paused)
        assert app._policy_ready is event

    app.run = run
    monkeypatch.setattr(module, "VideoPlayer" if kind == "video" else "WebPlayer", lambda: app)
    monkeypatch.setattr(module, "setup_logging", lambda: None)
    if kind == "web":
        monkeypatch.setattr(module, "setup_render_device", lambda: None)
    monkeypatch.setattr(faulthandler, "enable", lambda: None)
    monkeypatch.setattr(module.GLib, "unix_signal_add", lambda *args: None)
    monkeypatch.setattr(utils, "is_snap", lambda: False)
    monkeypatch.setattr(module, "SessionBus", lambda: SimpleNamespace(publish=lambda *args: None))
    module.main(ready=event, initially_paused=paused)
    assert seen == [paused]
