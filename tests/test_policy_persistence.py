"""Real server/ConfigUtil persistence in a temporary directory, no session bus."""

import copy
import itertools
import json

import pytest

from apricus import commons, server, utils
from apricus.policy import PlaybackPolicyEngine


@pytest.fixture
def persisted_server(tmp_path, monkeypatch):
    path = tmp_path / "config.json"
    clip = tmp_path / "video.mp4"
    clip.write_bytes(b"test source (never played)")
    for module in (commons, utils):
        monkeypatch.setattr(module, "CONFIG_DIR", str(tmp_path))
        monkeypatch.setattr(module, "CONFIG_PATH", str(path))
        monkeypatch.setattr(module, "LEGACY_CONFIG_PATH", str(tmp_path / "absent-legacy.json"))
    monkeypatch.setitem(commons.CONFIG_TEMPLATE, "data_source", {"Default": str(clip)})
    monkeypatch.setitem(commons.CONFIG_TEMPLATE, "playlist", {"Default": []})
    monkeypatch.setattr("apricus.monitor.MonitorInfo.monitors", lambda *args: [])
    monkeypatch.setattr(server, "get_instance", lambda name: None)
    initial = copy.deepcopy(commons.CONFIG_TEMPLATE)
    initial.update(mode="MODE_VIDEO", is_stopped=False, policy_manual_pause=False, is_mute=False)
    utils.ConfigUtil().save(initial, manual_stop=False, manual_pause=False)

    def restart():
        # Same load and latch restoration as server startup, without launching
        # GUI, sensors or processes. _load_config/_save_config are NOT mocked.
        obj = server.ApricusServer.__new__(server.ApricusServer)
        obj._load_config()
        obj.policy = PlaybackPolicyEngine()
        obj.policy.load_latches(
            manual_stop=obj.config["is_stopped"],
            manual_pause=obj.config["policy_manual_pause"],
            manual_mute=obj.config["is_mute"],
        )
        obj._last_applied = None
        obj.player_process = None
        obj._apply_stopped_background = lambda: None
        obj.reload = lambda: None  # Resume persistence, without spawning a player.
        return obj

    return restart, lambda: json.loads(path.read_text())


def _pause(obj, route):
    if route in ("property", "gui"):
        obj.is_paused_by_user = True
    if route in ("method", "gui"):
        obj.pause_playback()


def _play(obj, route):
    if route in ("property", "gui"):
        obj.is_paused_by_user = False
    if route in ("method", "gui"):
        obj.start_playback()


@pytest.mark.parametrize("route", ["method", "property", "gui"])
@pytest.mark.parametrize("automatic_pause", [False, True])
def test_pause_play_clears_disk_latch_and_survives_restart(
    persisted_server, route, automatic_pause
):
    restart, read_disk = persisted_server
    obj = restart()
    assert obj.policy.compute().desired == "playing"
    stale_playing = copy.deepcopy(obj.config)
    _pause(obj, route)
    assert obj.policy.latches()["manual_pause"] is True
    assert obj.config["policy_manual_pause"] is True
    assert read_disk()["policy_manual_pause"] is True
    stale_paused = copy.deepcopy(obj.config)
    # A non-owner GUI/playlist writer must not undo an explicit PAUSE.
    utils.ConfigUtil().save(stale_playing)
    assert read_disk()["policy_manual_pause"] is True
    if automatic_pause:
        obj.policy.reason_active_changed("fullscreen", True)
    _play(obj, route)
    assert obj.policy.latches()["manual_pause"] is False
    assert read_disk()["policy_manual_pause"] is False, (
        obj.policy.latches(),
        obj.config,
        read_disk(),
    )
    assert obj.config["policy_manual_pause"] is False
    assert obj.policy.compute().desired == ("paused" if automatic_pause else "playing")
    # A late save from the paused selection must not reintroduce PAUSE.
    utils.ConfigUtil().save(stale_paused)
    assert read_disk()["policy_manual_pause"] is False
    fresh = restart()
    assert fresh.policy.latches()["manual_pause"] is False
    assert fresh.config["policy_manual_pause"] is False
    assert fresh.policy.compute().desired == "playing"


@pytest.mark.parametrize("stop,pause,mute", list(itertools.product([False, True], repeat=3)))
def test_all_manual_latch_combinations_survive_real_disk_restart(
    persisted_server, stop, pause, mute
):
    restart, read_disk = persisted_server
    obj = restart()
    if pause:
        obj.pause_playback()
    obj.is_mute = mute
    if stop:
        obj.stop_playback()
    expected = {"manual_stop": stop, "manual_pause": pause, "manual_mute": mute}
    assert obj.policy.latches() == expected
    for config in (obj.config, read_disk()):
        assert (config["is_stopped"], config["policy_manual_pause"], config["is_mute"]) == (
            stop,
            pause,
            mute,
        )
    fresh = restart()
    assert fresh.policy.latches() == expected
    assert fresh.policy.compute().desired == (
        "stopped" if stop else "paused" if pause else "playing"
    )


def test_stop_resume_and_mute_clear_are_persisted(persisted_server):
    restart, read_disk = persisted_server
    obj = restart()
    stale_playing = copy.deepcopy(obj.config)
    obj.pause_playback()
    obj.is_mute = True
    obj.stop_playback()
    stale_stopped = copy.deepcopy(obj.config)
    # Ordinary writers preserve both disk latches (M1/M2 ownership rules).
    stale_playing["is_mute"] = True
    utils.ConfigUtil().save(stale_playing)
    assert read_disk()["is_stopped"] is True
    assert read_disk()["policy_manual_pause"] is True
    obj = restart()
    obj.start_playback()  # M1: this verb cannot clear STOP.
    assert read_disk()["is_stopped"] is True
    obj.resume_playback()
    assert obj.policy.latches() == {
        "manual_stop": False,
        "manual_pause": False,
        "manual_mute": True,
    }
    utils.ConfigUtil().save(stale_stopped)
    assert read_disk()["is_stopped"] is False
    assert read_disk()["policy_manual_pause"] is False
    assert read_disk()["is_mute"] is True
    obj.is_mute = False
    assert obj.config["is_mute"] is False
    assert read_disk()["is_mute"] is False
    assert restart().policy.latches() == {
        "manual_stop": False,
        "manual_pause": False,
        "manual_mute": False,
    }


@pytest.mark.parametrize("mute", [False, True])
def test_pause_play_and_later_server_save_preserve_manual_mute(persisted_server, mute):
    restart, read_disk = persisted_server
    obj = restart()
    obj.is_mute = mute
    _pause(obj, "gui")
    _play(obj, "gui")
    obj._save_config()  # Later ordinary server save must retain cleared PAUSE.
    assert read_disk()["policy_manual_pause"] is False
    assert read_disk()["is_stopped"] is False
    assert read_disk()["is_mute"] is mute
    assert restart().policy.latches() == {
        "manual_stop": False,
        "manual_pause": False,
        "manual_mute": mute,
    }
