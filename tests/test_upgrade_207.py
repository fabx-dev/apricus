"""Synthetic 2.0.7 config round-trip; not a Snap Store upgrade test.

Schema based on 5d81f51: CONFIG_VERSION=5, without M1/M2 backfill keys.
Only tmp_path data is used; no installed application or user data is touched.
"""

import json
from pathlib import Path

import pytest

from apricus import commons
from apricus import utils as U


@pytest.mark.parametrize("muted", [False, True])
def test_207_playlist_preferences_and_persistent_files_survive(tmp_path, monkeypatch, muted):
    config_dir = tmp_path / "config" / "apricus"
    config_dir.mkdir(parents=True)
    library = tmp_path / "videos"
    library.mkdir()
    videos = [str(library / name) for name in ("first.mp4", "second.mp4")]
    for path in videos:
        with open(path, "wb") as stream:
            stream.write(b"synthetic media sentinel, not decoded")
    old = {
        "version": 5,
        "mode": "MODE_PLAYLIST",
        "data_source": {"Default": videos[0]},
        "is_mute": muted,
        "audio_volume": 37,
        "is_static_wallpaper": False,
        "is_pause_when_maximized": True,
        "is_mute_when_maximized": True,
        "fade_duration_sec": 1.5,
        "fade_interval": 0.1,
        "is_show_systray": False,
        "is_first_time": False,
        "video_dir": str(library),
        "playlist": {"Default": videos},
        "playlist_shuffle": True,
        "stream_history": [],
        "stream_favorites": ["https://www.youtube.com/watch?v=DFf37yQHDn8"],
        "web_history": [],
        "web_favorites": ["https://example.org/"],
    }
    path = config_dir / "config.json"
    original = json.dumps(old, indent=2).encode()
    path.write_bytes(original)
    sentinel = config_dir / "persistent-user-data.bin"
    sentinel.write_bytes(b"preserve unrelated persistent data")
    for module in (commons, U):
        monkeypatch.setattr(module, "CONFIG_DIR", str(config_dir))
        monkeypatch.setattr(module, "CONFIG_PATH", str(path))
        monkeypatch.setattr(module, "LEGACY_CONFIG_PATH", str(tmp_path / "absent-legacy"))
    monkeypatch.setattr("apricus.monitor.MonitorInfo.monitors", lambda *_: [])
    monkeypatch.setitem(commons.CONFIG_TEMPLATE, "data_source", {"Default": ""})
    monkeypatch.setitem(commons.CONFIG_TEMPLATE, "playlist", {"Default": []})
    config = U.ConfigUtil()
    loaded = config.load()
    for key, value in old.items():
        assert loaded[key] == value, key
    assert path.read_bytes() == original  # Loading alone does not rewrite it.
    assert loaded["policy_manual_pause"] is False
    assert loaded["is_stopped"] is False
    config.save(loaded)
    reloaded = config.load()
    for key, value in old.items():
        assert reloaded[key] == value, key
    assert json.loads(path.with_suffix(".json.bak").read_bytes()) == old
    assert sentinel.read_bytes() == b"preserve unrelated persistent data"
    assert all(Path(p).is_file() for p in videos)
