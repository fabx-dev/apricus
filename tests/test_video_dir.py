"""Tests for the configurable video folder (no display required)."""

import json

from apricus import commons
from apricus.commons import CONFIG_KEY_VIDEO_DIR, resolve_video_dir
from apricus.utils import ConfigUtil, get_video_paths


def test_resolve_default_when_unset(tmp_path, monkeypatch):
    monkeypatch.setattr(commons, "VIDEO_WALLPAPER_DIR", str(tmp_path))
    # legacy folder exists -> compatibility fallback
    assert resolve_video_dir({}) == str(tmp_path)
    assert resolve_video_dir({CONFIG_KEY_VIDEO_DIR: None}) == str(tmp_path)


def test_resolve_custom_dir(tmp_path):
    custom = tmp_path / "mine"
    custom.mkdir()
    assert resolve_video_dir({CONFIG_KEY_VIDEO_DIR: str(custom)}) == str(custom)


def test_resolve_falls_back_when_custom_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(commons, "VIDEO_WALLPAPER_DIR", str(tmp_path))
    missing = tmp_path / "gone"
    assert resolve_video_dir({CONFIG_KEY_VIDEO_DIR: str(missing)}) == str(tmp_path)


def test_resolve_none_when_nothing_chosen(tmp_path, monkeypatch):
    monkeypatch.setattr(commons, "VIDEO_WALLPAPER_DIR", str(tmp_path / "nope"))
    assert resolve_video_dir({}) is None
    assert resolve_video_dir({CONFIG_KEY_VIDEO_DIR: None}) is None
    assert resolve_video_dir({CONFIG_KEY_VIDEO_DIR: str(tmp_path / "gone")}) is None


def test_get_video_paths_none_returns_empty():
    assert get_video_paths(None) == []


def test_old_config_without_key_is_not_wiped(tmp_path, monkeypatch):
    new_dir = tmp_path / "apricus"
    new_dir.mkdir()
    old_cfg = dict(commons.CONFIG_TEMPLATE)
    del old_cfg[CONFIG_KEY_VIDEO_DIR]
    old_cfg["audio_volume"] = 42
    (new_dir / "config.json").write_text(json.dumps(old_cfg))
    monkeypatch.setattr(commons, "CONFIG_PATH", str(new_dir / "config.json"))
    monkeypatch.setattr(commons, "CONFIG_DIR", str(new_dir))
    import apricus.utils as U

    monkeypatch.setattr(U, "CONFIG_PATH", str(new_dir / "config.json"))
    monkeypatch.setattr(U, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(U, "LEGACY_CONFIG_PATH", str(tmp_path / "no-legacy.json"))

    from unittest.mock import patch

    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["audio_volume"] == 42
    assert loaded[CONFIG_KEY_VIDEO_DIR] is None


def test_get_video_paths_uses_given_dir(tmp_path):
    (tmp_path / "a.txt").write_text("not video")
    assert get_video_paths(str(tmp_path)) == []


def test_get_video_paths_is_flat(tmp_path):
    from unittest.mock import patch

    sub = tmp_path / "sub"
    sub.mkdir()
    (tmp_path / "top.mp4").write_bytes(b"fake")
    (sub / "nested.mp4").write_bytes(b"fake")

    class FakeInfo:
        def __init__(self, path):
            self._path = path

        def get_content_type(self):
            return "video/mp4" if self._path.endswith(".mp4") else "text/plain"

    class FakeFile:
        def __init__(self, path):
            self._path = path

        def query_info(self, *args, **kwargs):
            return FakeInfo(self._path)

    with patch("apricus.utils.Gio.file_new_for_path", side_effect=FakeFile):
        paths = get_video_paths(str(tmp_path))
    assert paths == [str(tmp_path / "top.mp4")]


def test_get_video_paths_missing_dir_returns_empty(tmp_path):
    assert get_video_paths(str(tmp_path / "nope")) == []


def test_legacy_blur_key_is_dropped_without_wipe(tmp_path, monkeypatch):
    from unittest.mock import patch

    new_dir = tmp_path / "apricus"
    new_dir.mkdir()
    old_cfg = dict(commons.CONFIG_TEMPLATE)
    old_cfg["static_wallpaper_blur_radius"] = 9
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
    assert loaded["audio_volume"] == 42
    assert "static_wallpaper_blur_radius" not in loaded
