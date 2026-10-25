# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Server-side policy wiring: engine owns decisions, player only actuates.

Headless: real PlaybackPolicyEngine + fake player process; GLib timeouts
are driven manually via _apply_policy_pending (no main loop needed).
"""

import copy

from apricus import server as srv
from apricus.commons import CONFIG_TEMPLATE
from apricus.policy import PlaybackPolicyEngine


class _FakePlayer:
    mode = "MODE_VIDEO"

    def __init__(self):
        self.calls = []
        self.is_paused_by_user = False
        self.is_mute = False

    def pause_playback(self):
        self.calls.append("pause")

    def start_playback(self):
        self.calls.append("start")

    @property
    def playback_state(self):
        return "paused" if self.is_paused_by_user else "playing"


class _FakeProcess:
    pid = 999999  # nonexistent: os.getpgid raises ProcessLookupError

    def __init__(self, *args, **kwargs):
        self.started = False

    def is_alive(self):
        return False

    def start(self):
        self.started = True

    def terminate(self):
        pass

    def kill(self):
        pass

    def join(self, timeout=None):
        pass


def _server(monkeypatch, config_extra=None, player=None, clock=None):
    obj = srv.ApricusServer.__new__(srv.ApricusServer)
    disk = copy.deepcopy(CONFIG_TEMPLATE)
    disk.update(
        mode="MODE_VIDEO",
        data_source={"Default": "/v.mp4"},
        audio_volume=67,
        is_mute=False,
    )
    if config_extra:
        disk.update(config_extra)
    obj.config = copy.deepcopy(disk)
    obj._load_config = lambda: setattr(obj, "config", copy.deepcopy(disk))
    saved = []
    obj._save_config = lambda **kw: (
        saved.append(dict(obj.config)),
        disk.update(copy.deepcopy(obj.config)),
    )[1]
    obj._refresh_systray = lambda mode: None
    obj._apply_stopped_background = lambda: saved.append("stopped-bg")
    obj.player_process = None
    obj._player_count = 0
    obj.policy = PlaybackPolicyEngine() if clock is None else PlaybackPolicyEngine(clock=clock)
    obj.policy.load_latches(
        manual_stop=bool(disk.get("is_stopped", False)),
        manual_pause=bool(disk.get("policy_manual_pause", False)),
        manual_mute=bool(disk.get("is_mute", False)),
    )
    obj._last_applied = None
    obj._auto_fed = {}
    if player is None:
        player = _FakePlayer()
    obj._player = player
    monkeypatch.setattr(srv, "get_instance", lambda _name: obj._player)
    monkeypatch.setattr(srv, "Process", _FakeProcess)
    monkeypatch.setattr(
        srv, "Monitors", lambda: type("M", (), {"get_monitors": staticmethod(lambda: [])})()
    )
    return obj, disk, saved


def test_pause_actuates_once_then_dedups(monkeypatch):
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer())
    obj.pause_playback()
    obj.pause_playback()
    assert obj._player.calls == ["pause"], obj._player.calls
    assert disk["policy_manual_pause"] is True
    assert obj.engine_snapshot["desired"] == "paused"


def test_start_while_stopped_is_noop(monkeypatch):
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer())
    obj.policy.request_manual_stop()
    obj.start_playback()
    assert obj._player.calls == []
    assert obj.policy.latches()["manual_stop"] is True


def test_resume_clears_stop_and_pause_then_reloads(monkeypatch):
    obj, disk, saved = _server(monkeypatch, player=None)
    obj.policy.request_manual_stop()
    obj.policy.request_manual_pause()
    # Mirror what a persisted stop+pause looks like on disk.
    disk["is_stopped"] = True
    disk["policy_manual_pause"] = True
    obj.config = copy.deepcopy(disk)
    reloaded = []
    obj.reload = lambda: reloaded.append(True)
    obj.resume_playback()
    assert reloaded == [True]
    assert disk["is_stopped"] is False
    assert disk["policy_manual_pause"] is False
    assert obj.policy.latches()["manual_stop"] is False


def test_mute_toggle_does_not_restart_playback(monkeypatch):
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer())
    obj._apply_policy_output(obj.policy.compute())
    assert obj._player.calls == ["start"]
    obj.is_mute = True
    assert obj._player.calls == ["start"], obj._player.calls
    assert obj._player.is_mute is True
    assert disk["is_mute"] is True
    assert obj.engine_snapshot["desired"] == "playing"


def _clocked(monkeypatch, start=1000.0):
    now = [start]
    return now, (lambda: now[0])


def test_maximized_mute_keeps_manual_key_and_playing(monkeypatch):
    now, _ = _clocked(monkeypatch)
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer(), clock=lambda: now[0])
    obj.config["is_mute_when_maximized"] = True
    obj.config["is_pause_when_maximized"] = False
    obj._on_window_state_changed({"is_any_maximized": True, "is_any_fullscreen": False})
    assert obj._player.calls == []
    now[0] += 10.0
    obj._apply_policy_pending(obj.policy.snapshot()["generation"])
    assert obj._player.is_mute is True
    assert obj._player.calls == ["start"], obj._player.calls
    assert disk["is_mute"] is False
    assert obj.engine_snapshot["desired"] == "playing"


def test_fullscreen_pause_and_unlock_resume(monkeypatch):
    now, _ = _clocked(monkeypatch)
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer(), clock=lambda: now[0])
    obj._on_window_state_changed({"is_any_maximized": False, "is_any_fullscreen": True})
    assert obj._player.calls == []
    now[0] += 10.0
    obj._apply_policy_pending(obj.policy.snapshot()["generation"])
    assert obj._player.calls == ["pause"], obj._player.calls
    assert obj._player.is_paused_by_user is True
    obj._on_window_state_changed({"is_any_maximized": False, "is_any_fullscreen": False})
    now[0] += 10.0
    obj._apply_policy_pending(obj.policy.snapshot()["generation"])
    assert obj._player.calls == ["pause", "start"], obj._player.calls
    assert obj._player.is_paused_by_user is False


def test_manual_pause_survives_fullscreen_unlock(monkeypatch):
    now, _ = _clocked(monkeypatch)
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer(), clock=lambda: now[0])
    obj.pause_playback()
    assert obj._player.calls == ["pause"]
    obj._on_window_state_changed({"is_any_maximized": True, "is_any_fullscreen": False})
    now[0] += 10.0
    obj._apply_policy_pending(obj.policy.snapshot()["generation"])
    obj._on_window_state_changed({"is_any_maximized": False, "is_any_fullscreen": False})
    now[0] += 10.0
    obj._apply_policy_pending(obj.policy.snapshot()["generation"])
    assert obj._player.calls == ["pause"], obj._player.calls
    assert obj.engine_snapshot["desired"] == "paused"


def test_debounce_defers_actuation_until_due(monkeypatch):
    now, _ = _clocked(monkeypatch)
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer(), clock=lambda: now[0])
    calls = []

    def fake_timeout_add(delay, cb, *args):
        calls.append((delay, cb, args))
        return 999

    monkeypatch.setattr("gi.repository.GLib.timeout_add", fake_timeout_add)
    obj.reason_active_changed("fullscreen", True)
    assert obj._player.calls == []
    assert calls and calls[0][0] == 250
    _, cb, (gen,) = calls[0]
    assert gen == obj.policy.snapshot()["generation"]
    # Not due yet: nothing actuated.
    now[0] += 0.1
    assert cb(gen) is False
    assert obj._player.calls == []
    # Due: single actuation...
    now[0] += 1.0
    assert cb(gen) is False
    assert obj._player.calls == ["pause"], obj._player.calls
    # ...and no repeat: the pending slot was consumed.
    assert obj.policy.pending_delay() is None
    # A superseded generation is dropped without actuating.
    obj.reason_active_changed("fullscreen", False)
    stale_cb, stale_gen = calls[-1][1], calls[-1][2][0]
    obj.policy.request_manual_pause()
    assert stale_cb(stale_gen) is False
    assert obj._player.calls == ["pause"]


def test_snapshot_is_json_safe_and_reflects_latches(monkeypatch):
    import json

    obj, disk, saved = _server(monkeypatch, player=None)
    snap = obj.engine_snapshot
    assert json.loads(json.dumps(snap))["desired"] == "playing"
    obj.pause_playback()
    snap = obj.engine_snapshot
    assert snap["desired"] == "paused" and snap["manual_pause"] is True
    assert isinstance(snap["active_reasons"], list)


def test_stop_then_resume_full_cycle(monkeypatch):
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer())
    obj._apply_stopped_background = lambda: saved.append("stopped-bg")
    obj.stop_playback()
    assert disk["is_stopped"] is True
    assert obj.player_process is None
    assert "stopped-bg" in saved
    obj.reload = lambda: saved.append("reload")
    obj.resume_playback()
    assert disk["is_stopped"] is False
    assert "reload" in saved
    assert obj.engine_snapshot["active_reasons"] == []


def test_pause_then_change_wallpaper_stays_paused(monkeypatch, tmp_path):
    """Paused + new selection: selection updates, nothing auto-plays, the
    latch survives; only an explicit Play resumes (auto reason permitting)."""
    clip = tmp_path / "new.mp4"
    clip.write_bytes(b"fake")
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer())
    monkeypatch.setattr(srv, "Process", _FakeProcess)
    obj._apply_policy_output(obj.policy.compute())
    assert obj._player.calls == ["start"]
    obj.pause_playback()
    assert obj._player.calls == ["start", "pause"]
    obj.video(str(clip), "")
    assert disk["data_source"]["Default"] == str(clip)
    assert disk["policy_manual_pause"] is True
    assert obj.engine_snapshot["desired"] == "paused"
    assert "start" not in obj._player.calls[2:], obj._player.calls
    # The replacement has a separate actuator and becomes ready later.
    obj._player = _FakePlayer()
    obj.player_process.is_alive = lambda: True
    obj._player_ready_event.set()
    obj._reconcile_new_player(obj.player_process, obj._player_ready_event, True, float("inf"))
    assert obj._player.calls == ["pause"]
    obj.start_playback()
    assert obj._player.calls[-1] == "start"
    assert obj.engine_snapshot["desired"] == "playing"
    assert disk["policy_manual_pause"] is False


def test_pause_then_change_webpage_stays_paused(monkeypatch):
    """Same contract for MODE_WEBPAGE (no WebKit involved: the server
    decision is mode-agnostic; the freeze itself is covered below)."""
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer())
    monkeypatch.setattr(srv, "Process", _FakeProcess)
    obj._apply_policy_output(obj.policy.compute())
    obj.pause_playback()
    obj.webpage("file:///w.html", "")
    assert disk["data_source"]["Default"] == "file:///w.html"
    assert disk["policy_manual_pause"] is True
    assert obj.engine_snapshot["desired"] == "paused"
    assert obj._player.calls == ["start", "pause"], obj._player.calls
    obj._player = _FakePlayer()
    obj.player_process.is_alive = lambda: True
    obj._player_ready_event.set()
    obj._reconcile_new_player(obj.player_process, obj._player_ready_event, True, float("inf"))
    assert obj._player.calls == ["pause"]


def test_setup_hook_syncs_fresh_player_without_restarting_playback(monkeypatch):
    """A respawned/reloaded player inherits the latch without a re-fade.
    Exercises the same fresh-player path _setup_player uses."""
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer())
    obj.policy.request_manual_pause()
    obj._sync_policy_persist()
    obj._player.calls.clear()
    obj._apply_policy_output(obj.policy.compute(), fresh=True)
    assert obj._player.calls == ["pause"], obj._player.calls
    assert obj._player.is_paused_by_user is True
    assert obj.engine_snapshot["desired"] == "paused"


def test_fresh_playing_player_gets_no_restart(monkeypatch):
    """A freshly spawned player already auto-started: syncing must not
    re-issue play (no restart fades); the mirror is still aligned."""
    obj, disk, saved = _server(monkeypatch, player=_FakePlayer())
    obj._player.calls.clear()
    obj._apply_policy_output(obj.policy.compute(), fresh=True)
    assert obj._player.calls == [], obj._player.calls
    assert obj._player.is_paused_by_user is False
    assert obj.engine_snapshot["desired"] == "playing"


def test_web_pause_freezes_and_reports_paused(monkeypatch):
    from types import SimpleNamespace

    from apricus.player import web_player as web

    monkeypatch.setattr(
        web, "ConfigUtil", lambda: SimpleNamespace(load=lambda: {"is_stopped": False})
    )
    player = web.WebPlayer.__new__(web.WebPlayer)
    player._closing = False
    player._paused = False
    calls = []
    window = SimpleNamespace(set_frozen=lambda frozen: calls.append(frozen))
    player.windows = {"m": window}
    assert player.is_playing is True
    player.pause_playback()
    assert player.is_playing is False
    assert calls == [True]
    player.start_playback()
    assert player.is_playing is True
    assert calls == [True, False]


def test_playlist_source_loads_but_does_not_play_while_manually_paused(monkeypatch):
    from types import SimpleNamespace

    from apricus.player import video_player as vid

    def run(paused):
        monkeypatch.setattr(
            vid,
            "ConfigUtil",
            lambda: SimpleNamespace(
                load=lambda: {"is_stopped": False, "policy_manual_pause": paused}
            ),
        )
        player = vid.VideoPlayer.__new__(vid.VideoPlayer)
        player._closing = False
        player.is_paused_by_user = paused
        player.config = {"is_stopped": False}
        player.windows = {}
        played = []

        class FakeMedia:
            def add_option(self, *args):
                pass

        window = SimpleNamespace(
            _queue=["/a.mp4"],
            _queue_index=0,
            _transitioning=False,
            get_name=lambda: "m",
            media_new=lambda video: (played.append(video), FakeMedia())[1],
            stop=lambda: None,
            set_media=lambda media: None,
            play=lambda: played.append("play"),
            centercrop=lambda w, h: None,
        )
        player._media_started_at = None
        player._play_queue_item(window, None, None)
        return played

    assert run(True) == ["/a.mp4"]
    out = run(False)
    assert out[0] == "/a.mp4" and "play" in out
