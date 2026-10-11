"""Tests for ConfigUtil legacy Hidamari import (no display required)."""

import json

from apricus import commons
from apricus.utils import ConfigUtil


def test_legacy_config_imported_once(tmp_path, monkeypatch):
    new_dir = tmp_path / "apricus"
    legacy_dir = tmp_path / "hidamari"
    legacy_dir.mkdir()
    legacy_cfg = dict(commons.CONFIG_TEMPLATE)
    legacy_cfg["audio_volume"] = 77
    (legacy_dir / "config.json").write_text(json.dumps(legacy_cfg))
    monkeypatch.setattr(commons, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(commons, "CONFIG_PATH", str(new_dir / "config.json"))
    monkeypatch.setattr(commons, "LEGACY_CONFIG_PATH", str(legacy_dir / "config.json"))
    # utils imported the names at module level; patch there too
    import apricus.utils as U

    monkeypatch.setattr(U, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(U, "CONFIG_PATH", str(new_dir / "config.json"))
    monkeypatch.setattr(U, "LEGACY_CONFIG_PATH", str(legacy_dir / "config.json"))

    loaded = ConfigUtil().load()
    assert loaded["audio_volume"] == 77
    assert (new_dir / "config.json").is_file()
    # second load: adopted config used, legacy untouched
    (legacy_dir / "config.json").write_text(json.dumps(dict(legacy_cfg, audio_volume=11)))
    assert ConfigUtil().load()["audio_volume"] == 77
