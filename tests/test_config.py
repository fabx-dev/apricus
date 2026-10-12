# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for ConfigUtil legacy Hidamari import (no display required)."""

import json
from unittest.mock import patch

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

    loaded = None
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["audio_volume"] == 77
    assert (new_dir / "config.json").is_file()
    # second load: adopted config used, legacy untouched
    (legacy_dir / "config.json").write_text(json.dumps(dict(legacy_cfg, audio_volume=11)))
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        assert ConfigUtil().load()["audio_volume"] == 77


def test_clamp_repairs_degenerate_numerics():
    from apricus.utils import ConfigUtil

    cfg = {"audio_volume": 999, "fade_interval": 0, "fade_duration_sec": -3}
    out = ConfigUtil._clamp_config(cfg)
    assert out["audio_volume"] == 100
    assert out["fade_interval"] == 0.1
    assert out["fade_duration_sec"] == 0.0
    bad = {"audio_volume": "alto", "fade_interval": "x", "fade_duration_sec": None}
    out = ConfigUtil._clamp_config(bad)
    assert out == {"audio_volume": 50, "fade_interval": 0.1, "fade_duration_sec": 1.5}


def test_save_is_atomic_and_backed_up(tmp_path, monkeypatch):
    import json

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = tmp_path / "apricus"
    new_dir.mkdir()
    monkeypatch.setattr(commons, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(commons, "CONFIG_PATH", str(new_dir / "config.json"))
    import apricus.utils as U

    monkeypatch.setattr(U, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(U, "CONFIG_PATH", str(new_dir / "config.json"))

    cu = ConfigUtil()
    cfg1 = dict(commons.CONFIG_TEMPLATE)
    cfg1["audio_volume"] = 11
    cu.save(cfg1)
    assert not (new_dir / "config.json.tmp").exists()
    cfg2 = dict(cfg1)
    cfg2["audio_volume"] = 22
    cu.save(cfg2)
    assert (new_dir / "config.json.bak").is_file()
    assert json.loads((new_dir / "config.json.bak").read_text())["audio_volume"] == 11
    # corrupt main file -> recovers from backup instead of wiping
    (new_dir / "config.json").write_text("{broken")
    from unittest.mock import patch

    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = cu.load()
    assert loaded["audio_volume"] == 11
