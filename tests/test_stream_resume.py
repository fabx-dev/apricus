"""Deterministic VLC input-lifecycle regressions, no desktop/network/sleeps."""

from types import SimpleNamespace

import pytest
import vlc
from test_policy_startup import lifecycle as _isolated_server

from apricus.player import video_player as video

lifecycle = _isolated_server


class Input:
    """Model VLC 3's retained dead input after pause with can_pause=0."""

    def __init__(self, pausable=False):
        self.pausable = pausable
        self.state = vlc.State.Playing
        self.dead = False
        self.stops = 0
        self.plays = 0
        self.seeks = []
        self.stamp = 4200
        self.result = 0

    def can_pause(self):
        return self.pausable

    def is_playing(self):
        return self.state == vlc.State.Playing

    def pause(self):
        self.state = vlc.State.Paused if self.pausable else vlc.State.Ended
        self.dead = not self.pausable

    def stop(self):
        self.stops += 1
        self.dead = False
        self.state = vlc.State.Stopped

    def play(self):
        self.plays += 1
        if not self.dead and self.result == 0:
            self.state = vlc.State.Playing
        return self.result

    def get_time(self):
        return self.stamp

    def is_seekable(self):
        return True

    def set_time(self, stamp):
        self.seeks.append(stamp)

    def set_media(self, media):
        self.media = media


class Window:
    play = video.PlayerWindow.play
    pause = video.PlayerWindow.pause
    stop = video.PlayerWindow.stop
    set_media = video.PlayerWindow.set_media
    is_playing = video.PlayerWindow.is_playing
    play_fade = video.PlayerWindow.play_fade
    pause_fade = video.PlayerWindow.pause_fade
    _on_stream_playing = video.PlayerWindow._on_stream_playing
    _restore_stream_time = video.PlayerWindow._restore_stream_time
    _on_media_error = video.PlayerWindow._on_media_error

    def __init__(self, mode=video.MODE_STREAM, pausable=False):
        self.name = "test-stream"
        self._mode = mode
        self.input = Input(pausable)
        self._PlayerWindow__vlc_widget = SimpleNamespace(player=self.input)
        self.fade = video.Fade()
        self._stream_source = "https://youtube.com/watch?v=test"
        self._stream_expiry = None
        self._play_error = None
        self._first_frame_reported = True
        self.volumes = []

    def set_volume(self, value):
        self.volumes.append(value)

    def get_volume(self):
        return 0  # zero-volume pauses must complete too

    def set_mute(self, value):
        self.muted = value

    def media_new(self, url):
        return SimpleNamespace(add_option=lambda option: None)

    def add_audio_track(self, url):
        self.audio = url

    def centercrop(self, *dims):
        pass


@pytest.fixture
def loop(monkeypatch):
    queue = {}
    sequence = iter(range(1, 10000))

    def schedule(_ms, fn, *args):
        key = next(sequence)
        queue[key] = (fn, args)
        return key

    monkeypatch.setattr(video.GLib, "timeout_add", schedule)
    monkeypatch.setattr(video.GLib, "idle_add", lambda fn, *args: schedule(0, fn, *args))
    monkeypatch.setattr(video.GLib, "source_remove", lambda key: queue.pop(key, None))

    def drain():
        for _ in range(1000):
            if not queue:
                return
            key = next(iter(queue))
            fn, args = queue.pop(key)
            fn(*args)
        pytest.fail("unbounded callbacks")

    return SimpleNamespace(queue=queue, drain=drain)


@pytest.mark.parametrize("pausable", [False, True])
def test_repeated_resume_only_releases_nonpausable_stream_input(loop, pausable):
    w = Window(pausable=pausable)
    for cycle in range(6):
        w.pause_fade(1.5, 0.1)
        loop.drain()
        assert not w.is_playing()
        w.play_fade(50, 1.5, 0.1)
        w._on_stream_playing(None)
        loop.drain()
        assert w.is_playing()
        assert w.input.stops == (0 if pausable else cycle + 1)
    assert w.input.seeks == ([] if pausable else [4200] * 6)


def test_local_video_keeps_existing_resume_path(loop):
    w = Window(mode=video.MODE_VIDEO, pausable=True)
    w.pause_fade(1.5, 0.1)
    loop.drain()
    w.play_fade(50, 1.5, 0.1)
    loop.drain()
    assert w.is_playing()
    assert w.input.stops == 0
    assert w.input.seeks == []


def test_old_fade_cannot_pause_new_play_and_zero_step_completes(loop):
    w = Window()
    w.get_volume = lambda: 50
    w.pause_fade(1.5, 0.1)
    stale_fn, stale_args = next(iter(loop.queue.values()))
    w.play_fade(0, 1.5, 0.1)
    stale_fn(*stale_args)  # simulate a callback already queued at cancellation
    loop.drain()
    assert w.is_playing()
    assert w.input.stops == 0
    assert not w.fade.is_active


def test_pause_wins_during_opening_and_stale_seek_is_discarded(loop):
    w = Window()
    w.pause()
    w.play()
    w._on_stream_playing(None)
    w.pause_fade(1.5, 0.1)
    loop.drain()
    assert not w.is_playing()
    assert w.input.seeks == []
    w.input.state = vlc.State.Playing  # late input-open completion
    w._on_stream_playing(None)
    loop.drain()
    assert not w.is_playing()


def test_immediate_network_failure_is_visible_not_silently_retried(loop):
    w = Window()
    w.pause()
    w.input.result = -1
    w.play()
    assert w._play_error == "error"
    assert w.input.plays == 1
    assert not w.is_playing()


def test_source_replacement_invalidates_pending_seek(loop):
    w = Window()
    w.pause()
    w.play()
    w._on_stream_playing(None)
    w.set_media(object())
    loop.drain()
    assert w.input.seeks == []
    assert not w._stream_restart


def test_restart_ignores_old_playing_event_before_restoring_position(loop):
    w = Window()
    w._on_stream_playing(None)
    w.pause()
    w.play()
    loop.drain()
    assert w._stream_resume_time == 4200
    assert not w.input.seeks
    w._on_stream_playing(None)
    loop.drain()
    assert w.input.seeks == [4200]


def test_old_input_error_during_replacement_cannot_poison_new_media(loop):
    w = Window()
    w.input.set_media = lambda media: w._on_media_error(None)
    w.set_media(object())
    assert w._play_error is None
    assert not w._stream_restart


@pytest.mark.parametrize(
    "urls,want",
    [
        (("https://cdn/v?expire=200", "https://cdn/a?expire=100"), 100),
        (("https://cdn/expire/300/file",), 300),
        (("https://cdn/v?expire=bad",), None),
        (("https://cdn/v",), None),
    ],
)
def test_expiry_is_explicit_not_a_guessed_retry_timer(urls, want):
    assert video.stream_expiry(*urls) == want


@pytest.fixture
def refresh(loop, monkeypatch):
    w = Window()
    w.pause()
    w._stream_expiry = 100
    monitor = SimpleNamespace(
        is_primary=lambda: True, get_geometry=lambda: SimpleNamespace(height=1080)
    )

    # SimpleNamespace is not hashable; the monitor API needs identity keys.
    class Monitor:
        is_primary = staticmethod(monitor.is_primary)
        get_geometry = staticmethod(monitor.get_geometry)

    m = Monitor()
    app = video.VideoPlayer.__new__(video.VideoPlayer)
    app.config = {
        "mode": video.MODE_STREAM,
        "audio_volume": 50,
        "is_mute": True,
        "fade_duration_sec": 1.5,
        "fade_interval": 0.1,
    }
    app.windows = {m: w}
    app.is_paused_by_user = False
    app._stream_refresh_epoch = 1
    app._stream_refreshing = set()
    workers = []
    monkeypatch.setattr(
        video.threading,
        "Thread",
        lambda **kw: SimpleNamespace(start=lambda: workers.append(kw["target"])),
    )
    monkeypatch.setattr(video.ConfigUtil, "load", lambda self: {"is_stopped": False})
    monkeypatch.setattr(video.time, "time", lambda: 101)
    calls = []
    monkeypatch.setattr(video, "get_formats", lambda source, **kw: calls.append((source, kw)) or [])
    monkeypatch.setattr(
        video, "get_optimal_video", lambda *args: ("https://cdn/v?expire=500", 1920, 1080)
    )
    monkeypatch.setattr(video, "get_best_audio", lambda *args: "https://cdn/a?expire=500")
    return SimpleNamespace(app=app, window=w, workers=workers, calls=calls)


def test_valid_signed_urls_never_resolve_again(refresh, loop):
    x = refresh
    x.window._stream_expiry = 500
    x.app.start_playback()
    loop.drain()
    assert x.window.is_playing()
    assert not x.workers
    assert not x.calls


@pytest.mark.parametrize("interruption", [None, "pause", "manual_pause", "stop", "source"])
def test_expired_resolution_single_flight_and_latest_intent_wins(refresh, loop, interruption):
    x = refresh
    x.app.start_playback()
    x.app.start_playback()
    assert len(x.workers) == 1
    if interruption in ("pause", "manual_pause"):
        x.app.pause_playback()
        if interruption == "manual_pause":
            x.app.is_paused_by_user = True
    elif interruption == "stop":
        x.app._closing = True
    elif interruption == "source":
        x.app._stream_refresh_epoch += 1
    x.workers.pop()()
    loop.drain()
    assert len(x.calls) == 1
    assert x.calls[0][1] == {"fresh": True}
    assert x.window.is_playing() is (interruption is None)
    if interruption in ("pause", "manual_pause"):
        assert x.window._stream_resume_time == 4200


def test_resolution_failure_releases_busy_and_explicit_play_can_retry(refresh, loop, monkeypatch):
    x = refresh

    def fail(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(video, "get_formats", fail)
    x.app.start_playback()
    x.workers.pop()()
    loop.drain()
    assert not x.app._stream_refreshing
    assert x.window._play_error == "error"
    assert not x.window.is_playing()
    x.app.start_playback()
    assert len(x.workers) == 1


def test_actual_network_error_refreshes_only_on_explicit_play(refresh, loop):
    x = refresh
    x.window._stream_expiry = 500
    x.app.start_playback()
    x.window.input.state = vlc.State.Error
    x.window._on_media_error(None)
    loop.drain()
    assert not x.workers  # never an automatic retry storm
    x.app.start_playback()
    assert len(x.workers) == 1
    x.workers.pop()()
    loop.drain()
    assert x.window.is_playing()
    assert len(x.calls) == 1


@pytest.mark.parametrize("fresh", [False, True])
def test_resolution_cache_bypassed_only_for_required_refresh(monkeypatch, fresh):
    from apricus import utils, yt_utils

    calls = []
    monkeypatch.setattr(utils, "public_http_url", lambda url: True)
    monkeypatch.setattr(yt_utils, "get_cached_meta", lambda url: {"formats": ["cached"]})
    monkeypatch.setattr(yt_utils, "save_cached_meta", lambda *args: None)
    monkeypatch.setattr(yt_utils, "base_options", lambda: {})

    class Resolver:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, **kwargs):
            calls.append(url)
            return {"formats": ["fresh"]}

    monkeypatch.setattr(yt_utils, "youtube_dl", SimpleNamespace(YoutubeDL=lambda opts: Resolver()))
    result = yt_utils.get_formats("https://youtube.com/watch?v=test", fresh=fresh)
    assert result == (["fresh"] if fresh else ["cached"])
    assert len(calls) == int(fresh)


@pytest.mark.parametrize("reason", ["manual", "fullscreen", "maximized"])
@pytest.mark.parametrize("pausable", [False, True])
def test_server_stream_resume_without_gui(lifecycle, loop, reason, pausable):
    x = lifecycle
    x.seed()
    app = x.start()
    process = x.processes[-1]
    process.activate()
    w = Window(pausable=pausable)
    process.player.config["mode"] = video.MODE_STREAM
    process.player.windows = {next(iter(process.player.windows)): w}
    loop.drain()
    if reason == "manual":
        app.pause_playback()
    else:
        out, _ = app.policy.reasons_active_changed({reason: True}, settled=True)
        app._apply_policy_output(out)
    loop.drain()
    assert app.engine_snapshot["desired"] == "paused"
    assert not w.is_playing()
    if reason == "manual":
        app.start_playback()
    else:
        # Clicking Play while an automatic reason holds cannot bypass it.
        app.start_playback()
        assert not w.is_playing()
        out, _ = app.policy.reasons_active_changed({reason: False}, settled=True)
        app._apply_policy_output(out)
    w._on_stream_playing(None)
    loop.drain()
    assert app.engine_snapshot["desired"] == "playing"
    assert w.is_playing()
    assert w.input.stops == (0 if pausable else 1)
    assert w.input.seeks == ([] if pausable else [4200])
    assert len(x.processes) == 1  # server/player only; no GUI involved


def test_manual_stop_then_play_only_spawns_one_replacement(lifecycle, loop):
    x = lifecycle
    x.seed()
    app = x.start()
    old = x.processes[-1]
    old.activate()
    loop.drain()
    app.stop_playback()
    app.start_playback()  # STOP is not released by the PAUSE/PLAY actuator.
    assert app.engine_snapshot["desired"] == "stopped"
    assert not old.is_alive()
    app.resume_playback()
    fresh = x.processes[-1]
    fresh.activate()
    w = Window()
    fresh.player.config["mode"] = video.MODE_STREAM
    fresh.player.windows = {next(iter(fresh.player.windows)): w}
    loop.drain()
    assert app.engine_snapshot["desired"] == "playing"
    assert w.is_playing()
    assert sum(p.is_alive() for p in x.processes) == 1


def test_explicit_play_can_retry_failed_actuation_without_sensor_retry_storm(lifecycle, loop):
    x = lifecycle
    x.seed()
    app = x.start()
    process = x.processes[-1]
    process.activate()
    w = Window()
    process.player.config["mode"] = video.MODE_STREAM
    process.player.windows = {next(iter(process.player.windows)): w}
    loop.drain()
    w.pause()  # model the acknowledged-but-dead VLC input
    assert app.engine_snapshot["desired"] == "playing"
    assert not w.is_playing()
    app._apply_policy_output(app.policy.compute())
    assert w.input.plays == 0  # ordinary policy reconciliation stays idempotent
    app.start_playback()
    loop.drain()
    assert w.is_playing()
    assert w.input.plays == 1
    app.start_playback()
    loop.drain()
    assert w.input.plays == 1  # no restart if already playing
