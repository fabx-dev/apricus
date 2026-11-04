"""Policy + source selection + real temporary config, without GTK/VLC processes."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from test_policy_startup import Window
from test_policy_startup import lifecycle as lifecycle

from apricus import server, utils
from apricus.player import video_player as video
from apricus.player import web_player as web
from apricus.player.base_player import BasePlayer


class Monitor(str):
    def get_model(self):
        return str(self)

    def get_geometry(self):
        return SimpleNamespace(x=0 if self == "Virtual-1" else 640, y=0, width=640, height=360)

    def is_primary(self):
        return self == "Virtual-1"


class MediaWindow(Window):
    def __init__(self):
        super().__init__()
        self.loaded = None
        self.loads = []
        self.played_media = []

    def media_new(self, path):
        return SimpleNamespace(path=path, add_option=lambda option: None)

    def set_media(self, media):
        self.loaded = media.path if media else None
        self.loads.append(self.loaded)

    def play(self):
        if self.loaded is not None:
            self.played_media.append(self.loaded)
            super().play()

    def stop(self):
        self.playing = False

    def centercrop(self, *args):
        pass

    def get_name(self):
        return "test-window"

    def attach_end_reached(self, callback):
        self.end_callback = callback

    def load_uri(self, source, snapshot_cb=None):
        self.loaded = source
        self.loads.append(source)

    def set_frozen(self, frozen):
        self.frozen = frozen

    def set_is_mute(self, muted):
        self.muted = muted


@pytest.fixture
def system(lifecycle, monkeypatch):
    x = lifecycle
    x.seed()
    clips = []
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        p = x.path.parent / name
        p.write_bytes(b"never decoded")
        clips.append(str(p))
    x.clips = clips
    x.monitors = ["Virtual-1"]
    x.pages = []
    for name in ("one.html", "two.html"):
        page = x.path.parent / name
        page.write_text("<html></html>")
        x.pages.append(page.as_uri())
    monkeypatch.setattr(video.mediacache, "probe_video", lambda path: (640, 360, 6))
    monkeypatch.setattr(BasePlayer, "do_activate", lambda self: None)
    monkeypatch.setattr(video.VideoPlayer, "_live_cache_paths", lambda self: set())
    monkeypatch.setattr(server.GLib, "idle_add", lambda fn, *args: x.tasks.append((fn, args)))
    monkeypatch.setattr(server.ApricusServer, "_quit_player", staticmethod(lambda **kwargs: None))
    process = server.Process
    original_start = process.start

    def start(self):
        original_start(self)
        if self.player.mode == "MODE_WEBPAGE":
            cfg = self.player.config
            self.player = web.WebPlayer.__new__(web.WebPlayer)
            self.player.config = cfg
            self.player._paused = self.args[1]
        self.player.wallpaper = SimpleNamespace(
            supports_static=False, prune_cache=lambda paths: None
        )
        self.player.windows = {Monitor(name): MediaWindow() for name in x.monitors}
        self.player._policy_ready = self.args[0]

    def activate(self):
        self.player.do_activate()  # Real source setup, not just a fake playing flag.
        x.registry["player"] = self.player

    monkeypatch.setattr(process, "start", start)
    monkeypatch.setattr(process, "activate", activate)

    def seed(mode="MODE_PLAYLIST", pause=False, stop=False, position=0):
        cfg = utils.ConfigUtil().load()
        cfg.update(
            mode=mode,
            policy_manual_pause=pause,
            is_stopped=stop,
            is_static_wallpaper=False,
            playlist={"Default": clips[:2]},
            playlist_position=position if isinstance(position, dict) else {"Virtual-1": position},
        )
        if mode == "MODE_WEBPAGE":
            cfg["data_source"] = {"Default": x.pages[0]}
        # Initial fixture state only; runtime tests use the real locked writer.
        x.path.write_text(json.dumps(cfg))

    x.select_initial = seed

    def ready():
        p = x.processes[-1]
        p.activate()
        x.pump()
        return p

    x.ready = ready
    return x


def test_playlist_play_after_paused_restart_loads_saved_item(system):
    x = system
    x.select_initial(position=1)
    old = x.start()
    previous = x.ready()
    old.pause_playback()
    previous.terminate()
    new = x.start()
    fresh = x.ready()
    assert fresh.player.playback_state == "paused"
    assert new.engine_snapshot["manual_pause"] is True
    new.start_playback()
    assert new.engine_snapshot["desired"] == "playing"
    assert fresh.player.playback_state == "playing"
    assert fresh.player.windows["Virtual-1"].loaded == x.clips[1]
    assert json.loads(x.path.read_text())["playlist_position"] == {"Virtual-1": 1}
    assert len(x.processes) == 2


def test_playlist_selected_while_paused_replaces_old_media_without_autoplay(system):
    x = system
    x.select_initial(mode="MODE_VIDEO")
    obj = x.start()
    p = x.ready()
    window = p.player.windows["Virtual-1"]
    old_media = window.loaded
    obj.pause_playback()
    obj.playlist(x.clips[:2], "")
    x.pump()  # Deliver switch_media's real deferred source application.
    assert p.player.playback_state == "paused"
    assert window.loaded == x.clips[0], (old_media, window.loaded)
    obj.start_playback()
    assert p.player.playback_state == "playing"
    assert window.loaded == x.clips[0]
    assert len(x.processes) == 1


def test_pause_and_stop_cannot_overwrite_player_position(system):
    x = system
    x.select_initial()
    obj = x.start()
    p = x.ready()
    window = p.player.windows["Virtual-1"]
    p.player._on_media_end(window)
    assert json.loads(x.path.read_text())["playlist_position"] == {"Virtual-1": 1}
    assert obj.config["playlist_position"] == {"Virtual-1": 0}  # Stale server snapshot.
    obj.pause_playback()
    assert json.loads(x.path.read_text())["playlist_position"] == {"Virtual-1": 1}
    obj.stop_playback()
    assert json.loads(x.path.read_text())["playlist_position"] == {"Virtual-1": 1}


@pytest.mark.parametrize("mode", ["MODE_VIDEO", "MODE_PLAYLIST", "MODE_WEBPAGE"])
@pytest.mark.parametrize("muted", [False, True])
def test_transport_restart_and_selection_while_stopped(system, mode, muted):
    x = system
    x.select_initial(mode=mode, position=1)
    cfg = json.loads(x.path.read_text())
    cfg["is_mute"] = muted
    x.path.write_text(json.dumps(cfg))
    obj = x.start()
    x.ready()
    assert obj.playback_state == "playing"
    obj.pause_playback()
    assert obj.playback_state == obj.engine_snapshot["desired"] == "paused"
    assert json.loads(x.path.read_text())["policy_manual_pause"] is True
    obj.start_playback()
    assert obj.playback_state == obj.engine_snapshot["desired"] == "playing"
    obj.pause_playback()
    obj.stop_playback()
    stopped = x.start()  # Real config load, no process creation under STOP.
    assert stopped.player_process is None
    assert len(x.processes) == 1
    assert stopped.engine_snapshot["manual_stop"] is True
    assert stopped.engine_snapshot["manual_pause"] is True
    assert json.loads(x.path.read_text())["playlist_position"] == {"Virtual-1": 1}
    if mode == "MODE_VIDEO":
        stopped.video(x.clips[2], "")
        expected = x.clips[2]
    elif mode == "MODE_PLAYLIST":
        stopped.playlist([x.clips[2], x.clips[0]], "")
        expected = x.clips[0]  # Saved position 1, now in the new selection.
    else:
        stopped.webpage(x.pages[1], "")
        expected = x.pages[1]
    assert stopped.player_process is None
    assert len(x.processes) == 1
    assert json.loads(x.path.read_text())["playlist_position"] == {"Virtual-1": 1}
    stopped.resume_playback()
    fresh = x.ready()
    assert len(x.processes) == 2
    assert fresh.player.windows["Virtual-1"].loaded == expected
    assert stopped.playback_state == stopped.engine_snapshot["desired"] == "playing"
    assert stopped.engine_snapshot["manual_stop"] is False
    assert stopped.engine_snapshot["manual_pause"] is False
    assert stopped.engine_snapshot["manual_mute"] is muted
    disk = json.loads(x.path.read_text())
    assert disk["is_stopped"] is False and disk["policy_manual_pause"] is False
    assert disk["is_mute"] is muted
    assert disk["playlist_position"] == {"Virtual-1": 1}


def test_multi_monitor_playlist_positions_and_no_advance_during_pause(system):
    x = system
    x.monitors = ["Virtual-1", "Virtual-2"]
    x.select_initial(position={"Virtual-1": 1, "Virtual-2": 2})
    cfg = json.loads(x.path.read_text())
    cfg["playlist"]["Virtual-2"] = [x.clips[2], x.clips[1], x.clips[0]]
    x.path.write_text(json.dumps(cfg))
    obj = x.start()
    p = x.ready()
    one, two = p.player.windows.values()
    assert (one.loaded, two.loaded) == (x.clips[1], x.clips[0])
    obj.pause_playback()
    positions = json.loads(x.path.read_text())["playlist_position"]
    for w in (one, two):
        p.player._on_media_end(w)  # Late end notifications cannot advance PAUSE.
    assert json.loads(x.path.read_text())["playlist_position"] == positions
    assert (one._queue_index, two._queue_index) == (1, 2)
    obj.start_playback()
    obj.start_playback()
    assert obj.playback_state == "playing"
    assert one.play_requests == two.play_requests == 2  # Initial start + one resume.
    assert one.loads == [x.clips[1]] and two.loads == [x.clips[0]]
    p.player._on_media_end(one)
    assert one.loaded == x.clips[0]
    assert json.loads(x.path.read_text())["playlist_position"] == {"Virtual-1": 0, "Virtual-2": 2}


def test_play_before_deferred_selection_does_not_resume_previous_media(system):
    x = system
    x.select_initial(mode="MODE_VIDEO")
    obj = x.start()
    p = x.ready()
    w = p.player.windows["Virtual-1"]
    original = w.loaded
    obj.pause_playback()
    obj.playlist(x.clips[:2], "")
    obj.start_playback()  # Arrives before switch_media's source callback.
    assert not w.playing
    x.pump()
    assert w.loaded == x.clips[0]
    assert w.played_media == [original, x.clips[0]]
    assert len(x.processes) == 1


def test_queued_selections_coalesce_and_keep_pause(system):
    x = system
    x.select_initial(pause=True)
    obj = x.start()
    p = x.ready()
    w = p.player.windows["Virtual-1"]
    obj.playlist([x.clips[1]], "")
    obj.playlist([x.clips[2]], "")
    x.pump()
    assert w.loaded == x.clips[2]
    assert w.loads == [x.clips[0], x.clips[2]]
    assert not w.playing and obj.engine_snapshot["manual_pause"]
    obj.start_playback()
    assert w.played_media == [x.clips[2]]


def test_automatic_pause_veto_and_manual_priority_hold_playlist(system):
    x = system
    x.select_initial()
    obj = x.start()
    p = x.ready()
    w = p.player.windows["Virtual-1"]
    obj.policy.reason_active_changed("fullscreen", True)
    obj._apply_policy_output(obj.policy.compute())
    p.player._on_media_end(w)
    assert w._queue_index == 0 and not w.playing
    obj.pause_playback()
    obj.policy.reason_active_changed("fullscreen", False)
    obj._apply_policy_output(obj.policy.compute())
    assert not w.playing and obj.engine_snapshot["manual_pause"]
    obj.start_playback()
    assert w.playing
    assert w._queue_index == 0


def test_position_writer_merges_monitors_and_rejects_superseded_queue(system):
    x = system
    x.select_initial()
    store = utils.ConfigUtil()
    stale = store.load()
    assert store.save_playlist_position("Virtual-1", 1, x.clips[:2]) == {"Virtual-1": 1}
    assert store.save_playlist_position("Virtual-2", 1, x.clips[:2]) == {
        "Virtual-1": 1,
        "Virtual-2": 1,
    }
    # Later unrelated save preserves both positions even with a stale map.
    stale["is_mute"] = True
    store.save(stale)
    assert json.loads(x.path.read_text())["playlist_position"] == {"Virtual-1": 1, "Virtual-2": 1}
    current = store.load()
    current["playlist"]["Virtual-1"] = [x.clips[2]]
    store.save(current)
    before = x.path.read_bytes()
    assert store.save_playlist_position("Virtual-1", 0, x.clips[:2]) is None
    assert x.path.read_bytes() == before
    positions = store.save_playlist_position("Virtual-2", 0, x.clips[:2])
    assert positions == {"Virtual-1": 1, "Virtual-2": 0}
    assert json.loads(x.path.read_text())["is_mute"] is True
    store.save(store.load(), manual_stop=True)
    before = x.path.read_bytes()
    assert store.save_playlist_position("Virtual-2", 1, x.clips[:2]) is None
    assert x.path.read_bytes() == before


@pytest.mark.parametrize("index", [True, -1, "1", None])
def test_invalid_position_update_cannot_change_data(system, index):
    x = system
    x.select_initial()
    before = x.path.read_bytes()
    assert utils.ConfigUtil().save_playlist_position("Virtual-1", index, x.clips[:2]) is None
    assert x.path.read_bytes() == before


@pytest.mark.parametrize("kind", ["video", "webpage"])
def test_nonplaylist_selection_during_pause_keeps_new_content(system, kind):
    x = system
    x.select_initial(mode="MODE_VIDEO", position=1)
    obj = x.start()
    first = x.ready()
    obj.pause_playback()
    if kind == "video":
        obj.video(x.clips[2], "")
        x.pump()
        current = first
        expected = x.clips[2]
        assert len(x.processes) == 1
    else:
        obj.webpage(x.pages[1], "")
        current = x.ready()
        expected = x.pages[1]
        assert len(x.processes) == 2
        assert first.is_alive() is False
    w = current.player.windows["Virtual-1"]
    assert w.loaded == expected
    assert obj.playback_state == obj.engine_snapshot["desired"] == "paused"
    obj.start_playback()
    assert obj.playback_state == "playing"
    assert json.loads(x.path.read_text())["playlist_position"] == {"Virtual-1": 1}


def test_monitor_without_queue_does_not_keep_old_video(system):
    x = system
    x.monitors = ["Virtual-1", "Virtual-2"]
    x.select_initial(mode="MODE_VIDEO")
    obj = x.start()
    p = x.ready()
    obj.pause_playback()
    cfg = utils.ConfigUtil().load()
    cfg.update(mode="MODE_PLAYLIST", playlist={"Default": [], "Virtual-2": x.clips[:2]})
    utils.ConfigUtil().save(cfg)
    obj.playlist(x.clips[:2], "Virtual-2")
    x.pump()
    one, two = p.player.windows.values()
    assert one.loaded is None
    assert two.loaded == x.clips[0]
    assert not one.playing and not two.playing
    obj.start_playback()
    assert not one.playing and two.playing


def test_old_queue_end_callback_cannot_change_single_video(system):
    x = system
    x.select_initial()
    obj = x.start()
    p = x.ready()
    w = p.player.windows["Virtual-1"]
    obj.video(x.clips[2], "")
    p.player._on_media_end(w)  # Queued source update is pending.
    x.pump()
    assert w.loaded == x.clips[2]
    p.player._on_media_end(w)  # Old playlist callback after mode changed.
    assert w.loaded == x.clips[2]


def test_concurrent_monitor_position_writes_merge_without_losing_user_state(system):
    x = system
    x.select_initial(pause=True)
    cfg = utils.ConfigUtil().load()
    cfg["is_mute"] = True
    utils.ConfigUtil().save(cfg)

    def update(key, final):
        store = utils.ConfigUtil()
        for i in range(8):
            assert store.save_playlist_position(key, i % 2, x.clips[:2]) is not None
        store.save_playlist_position(key, final, x.clips[:2])

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(update, "Virtual-1", 1), pool.submit(update, "Virtual-2", 0)]
        for future in futures:
            future.result()
    disk = json.loads(x.path.read_text())
    assert disk["playlist_position"] == {"Virtual-1": 1, "Virtual-2": 0}
    assert disk["policy_manual_pause"] is True and disk["is_mute"] is True
    assert disk["is_stopped"] is False and disk["playlist"] == cfg["playlist"]
    assert not x.path.with_suffix(".json.tmp").exists()
