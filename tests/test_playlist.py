"""Tests for MODE_PLAYLIST config + server + player queue logic (headless)."""

import json
from unittest.mock import patch

from apricus import commons
from apricus.player.video_player import VideoPlayer
from apricus.utils import ConfigUtil


def _server_stub():
    from apricus import server as srv

    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.config = {
        "mode": "MODE_NULL",
        "data_source": {"Default": ""},
        "playlist": {"Default": []},
        "playlist_shuffle": False,
    }
    saved = []
    s._save_config = lambda: saved.append(True)
    s._load_config = lambda: None
    setups = []

    def fake_setup(self, mode, data_source=None, monitor=None):
        setups.append(mode)
        self.config["mode"] = mode

    s._setup_player = fake_setup.__get__(s)
    return s, setups


def test_playlist_rejects_empty():
    s, setups = _server_stub()
    s.playlist([], monitor=None)
    s.playlist(None, monitor=None)
    assert setups == []
    assert s.config["mode"] == "MODE_NULL"


def test_playlist_sets_queue_for_monitor():
    s, setups = _server_stub()
    s.playlist(["/a.mp4", "/b.mp4"], monitor="M1")
    assert s.config["playlist"]["M1"] == ["/a.mp4", "/b.mp4"]
    assert setups == ["MODE_PLAYLIST"]


def test_playlist_all_sets_everywhere():
    s, setups = _server_stub()
    s.config["playlist"] = {"M1": ["/old.mp4"], "Default": ["/old.mp4"]}
    s.playlist(["/n.mp4"], monitor="all")
    assert s.config["playlist"]["M1"] == ["/n.mp4"]
    assert s.config["playlist"]["Default"] == ["/n.mp4"]
    assert setups == ["MODE_PLAYLIST"]


def test_queue_for_priority():
    pl = {"M#0": ["/new.mp4"], "M": ["/old.mp4"], "Default": ["/def.mp4"]}
    assert VideoPlayer._queue_for(pl, "M#0", "M") == ["/new.mp4"]
    assert VideoPlayer._queue_for(pl, "M#1", "M") == ["/old.mp4"]
    assert VideoPlayer._queue_for(pl, "X", "Y") == ["/def.mp4"]
    assert VideoPlayer._queue_for({}, "X", "Y") == []
    assert VideoPlayer._queue_for({"Default": "notalist"}, "X", "Y") == []


def test_first_queued_video():
    assert VideoPlayer._first_queued_video(FakePlayer({"Default": ["/d.mp4"]})) == "/d.mp4"
    assert VideoPlayer._first_queued_video(FakePlayer({"M": ["/m.mp4"]})) == "/m.mp4"
    assert VideoPlayer._first_queued_video(FakePlayer({})) is None


class FakePlayer:
    def __init__(self, playlist):
        self.config = {"playlist": playlist}


def test_v4_to_v5_migration(tmp_path, monkeypatch):
    cu = ConfigUtil()
    cu.save = lambda cfg: None
    cfg = {"version": 4, "mode": "MODE_NULL", "data_source": {"Default": ""}}
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        cu._migrateV4To5(cfg)
    assert cfg["version"] == 5
    assert cfg["playlist"]["Default"] == []
    assert cfg["playlist_shuffle"] is False


def test_v4_config_loads_without_wipe(tmp_path, monkeypatch):
    new_dir = tmp_path / "apricus"
    new_dir.mkdir()
    old_cfg = dict(commons.CONFIG_TEMPLATE)
    old_cfg["version"] = 4
    old_cfg.pop("playlist")
    old_cfg.pop("playlist_shuffle")
    old_cfg["audio_volume"] = 42
    (new_dir / "config.json").write_text(json.dumps(old_cfg))
    monkeypatch.setattr(commons, "CONFIG_PATH", str(new_dir / "config.json"))
    monkeypatch.setattr(commons, "CONFIG_DIR", str(new_dir))
    import apricus.utils as U

    monkeypatch.setattr(U, "CONFIG_PATH", str(new_dir / "config.json"))
    monkeypatch.setattr(U, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(U, "LEGACY_CONFIG_PATH", str(tmp_path / "no-legacy.json"))
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["version"] == 5
    assert loaded["audio_volume"] == 42
    assert loaded["playlist"]["Default"] == []
