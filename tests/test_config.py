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


class FakeContext:
    def __init__(self, sender):
        self.sender = sender


def test_authorize_allows_direct_calls():
    from apricus.utils import _authorize

    assert _authorize("video", None) is None


def test_authorize_allows_same_uid(monkeypatch):
    import os

    import apricus.utils as U

    monkeypatch.setattr(U, "_caller_uid", lambda sender: os.getuid())
    U._authorize("video", FakeContext(":1.42"))


def test_authorize_rejects_foreign_uid(monkeypatch):
    import os

    import pytest

    import apricus.utils as U

    monkeypatch.setattr(U, "_caller_uid", lambda sender: os.getuid() + 999)
    with pytest.raises(PermissionError):
        U._authorize("quit", FakeContext(":1.99"))


def test_stream_keys_backfilled_without_version_bump(tmp_path, monkeypatch):
    """Old configs without stream keys load with empty lists (no wipe)."""
    from unittest.mock import patch

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = tmp_path / "apricus"
    new_dir.mkdir()
    legacy = dict(commons.CONFIG_TEMPLATE)
    del legacy["stream_history"]
    del legacy["stream_favorites"]
    (new_dir / "config.json").write_text(json.dumps(legacy))
    monkeypatch.setattr(commons, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(commons, "CONFIG_PATH", str(new_dir / "config.json"))
    import apricus.utils as U

    monkeypatch.setattr(U, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(U, "CONFIG_PATH", str(new_dir / "config.json"))

    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["stream_history"] == []
    assert loaded["stream_favorites"] == []
    assert loaded["version"] == commons.CONFIG_VERSION


def test_load_versionless_modern_config_stamps_and_keeps_data(tmp_path, monkeypatch):
    """A current-shape config that lost its version stamp must load (not
    raise TypeError) and keep user data instead of mis-migrating."""
    from unittest.mock import patch

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = tmp_path / "apricus"
    new_dir.mkdir()
    videos = tmp_path / "v"
    videos.mkdir()
    versionless = dict(commons.CONFIG_TEMPLATE)
    del versionless["version"]
    versionless["video_dir"] = str(videos)
    versionless["audio_volume"] = 50
    (new_dir / "config.json").write_text(json.dumps(versionless))
    monkeypatch.setattr(commons, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(commons, "CONFIG_PATH", str(new_dir / "config.json"))
    import apricus.utils as U

    monkeypatch.setattr(U, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(U, "CONFIG_PATH", str(new_dir / "config.json"))

    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["version"] == commons.CONFIG_VERSION
    assert loaded["video_dir"] == str(videos)
    assert loaded["audio_volume"] == 50


def test_valid_http_url_allows_and_blocks():
    from apricus.utils import valid_http_url

    assert valid_http_url("https://example.com/v.mp4") == "https://example.com/v.mp4"
    assert valid_http_url("http://localhost:8080/x") == "http://localhost:8080/x"
    assert valid_http_url("  https://example.com/  ") == "https://example.com/"
    assert valid_http_url("HTTPS://EXAMPLE.COM/") == "HTTPS://EXAMPLE.COM/"
    assert valid_http_url("file:///etc/passwd") is None
    assert valid_http_url("ftp://example.com/x") is None
    assert valid_http_url("http://user:pass@example.com/") is None
    assert valid_http_url("https://user@example.com/") is None
    assert valid_http_url("https:///no-host") is None
    assert valid_http_url("nota url") is None
    assert valid_http_url("") is None
    assert valid_http_url(None) is None
    assert valid_http_url(123) is None


def _isolated_dirs(tmp_path, monkeypatch):
    import apricus.utils as U
    from apricus import commons

    new_dir = tmp_path / "apricus"
    new_dir.mkdir()
    monkeypatch.setattr(commons, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(commons, "CONFIG_PATH", str(new_dir / "config.json"))
    monkeypatch.setattr(U, "CONFIG_DIR", str(new_dir))
    monkeypatch.setattr(U, "CONFIG_PATH", str(new_dir / "config.json"))
    return new_dir


def test_first_save_after_backfill_writes_backup(tmp_path, monkeypatch):
    """A valid-but-outdated config (1.0 shape) must leave a .bak with the
    original content on the first save after backfill (regression: the
    backup was skipped because the old file failed _check)."""
    from unittest.mock import patch

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = _isolated_dirs(tmp_path, monkeypatch)
    old_shape = dict(commons.CONFIG_TEMPLATE)
    del old_shape["stream_history"]
    del old_shape["web_history"]
    old_shape["audio_volume"] = 42
    (new_dir / "config.json").write_text(json.dumps(old_shape))
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["audio_volume"] == 42
    assert loaded["stream_history"] == []
    # Load alone must not rewrite the disk...
    assert "stream_history" not in json.loads((new_dir / "config.json").read_text())
    assert not (new_dir / "config.json.bak").exists()
    # ...but the first save backs up the original bytes.
    loaded["audio_volume"] = 43
    ConfigUtil().save(loaded)
    bak = json.loads((new_dir / "config.json.bak").read_text())
    assert bak["audio_volume"] == 42
    assert "stream_history" not in bak
    assert json.loads((new_dir / "config.json").read_text())["audio_volume"] == 43


def test_backfill_preserves_unknown_keys_and_second_save_is_noop(tmp_path, monkeypatch):
    """Unknown keys survive load+save; a second identical save writes nothing."""
    from unittest.mock import patch

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = _isolated_dirs(tmp_path, monkeypatch)
    cfg = dict(commons.CONFIG_TEMPLATE)
    cfg["future_key"] = {"nested": [1, 2]}
    (new_dir / "config.json").write_text(json.dumps(cfg))
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["future_key"] == {"nested": [1, 2]}
    ConfigUtil().save(loaded)
    assert json.loads((new_dir / "config.json").read_text())["future_key"] == {"nested": [1, 2]}
    before = (new_dir / "config.json").read_text()
    ConfigUtil().save(loaded)
    assert (new_dir / "config.json").read_text() == before


def test_corrupt_file_saves_without_backup_but_writes_new(tmp_path, monkeypatch):
    """Unparseable disk content cannot be backed up, but save must still
    write the new config (no partial file left behind)."""
    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = _isolated_dirs(tmp_path, monkeypatch)
    (new_dir / "config.json").write_text("{broken")
    cfg = dict(commons.CONFIG_TEMPLATE)
    ConfigUtil().save(cfg)
    assert not (new_dir / "config.json.bak").exists()
    assert json.loads((new_dir / "config.json").read_text())["version"] == commons.CONFIG_VERSION
    assert not (new_dir / "config.json.tmp").exists()


def test_save_failure_keeps_original_file(tmp_path, monkeypatch):
    """A write error must not destroy the existing config."""
    import pytest

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = _isolated_dirs(tmp_path, monkeypatch)
    (new_dir / "config.json").write_text(json.dumps(dict(commons.CONFIG_TEMPLATE)))
    # Point the dir at a regular file: makedirs fails with OSError.
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    import apricus.utils as U

    monkeypatch.setattr(U, "CONFIG_DIR", str(blocker))
    monkeypatch.setattr(commons, "CONFIG_DIR", str(blocker))
    changed = dict(commons.CONFIG_TEMPLATE)
    changed["audio_volume"] = 43
    with pytest.raises(OSError):
        ConfigUtil().save(changed)
    assert json.loads((new_dir / "config.json").read_text())["version"] == commons.CONFIG_VERSION


def test_update01_isolated_10_shape_migration(tmp_path, monkeypatch):
    """RC2-UPDATE-01: a 1.0-shape config (v5, no stream/web keys) migrates
    in an isolated dir: load() leaves the disk byte-identical, first save
    writes a .bak with the exact original bytes."""
    from unittest.mock import patch

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = _isolated_dirs(tmp_path, monkeypatch)
    original = dict(commons.CONFIG_TEMPLATE)
    for key in ("stream_history", "stream_favorites", "web_history", "web_favorites"):
        del original[key]
    original["audio_volume"] = 63
    original["mode"] = "MODE_VIDEO"
    raw = json.dumps(original, indent=3)
    (new_dir / "config.json").write_text(raw)
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["audio_volume"] == 63
    assert loaded["mode"] == "MODE_VIDEO"
    assert loaded["stream_history"] == [] and loaded["web_favorites"] == []
    assert (new_dir / "config.json").read_text() == raw
    assert not (new_dir / "config.json.bak").exists()
    loaded["audio_volume"] = 64
    ConfigUtil().save(loaded)
    assert (new_dir / "config.json.bak").read_text() == (raw if raw.endswith("\n") else raw + "\n")
    assert json.loads((new_dir / "config.json").read_text())["audio_volume"] == 64


def test_update02_garbage_without_backup_falls_back_to_template(tmp_path, monkeypatch):
    """RC2-UPDATE-02: garbage with no usable .bak falls back to template
    instead of crashing; no backup is fabricated."""
    from unittest.mock import patch

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = _isolated_dirs(tmp_path, monkeypatch)
    (new_dir / "config.json").write_text("{not json")
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["version"] == commons.CONFIG_VERSION
    assert not (new_dir / "config.json.bak").exists()


def test_update05_second_boot_is_stable(tmp_path, monkeypatch):
    """RC2-UPDATE-05: load->save->load->save is byte-stable, no .bak churn."""
    from unittest.mock import patch

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = _isolated_dirs(tmp_path, monkeypatch)
    (new_dir / "config.json").write_text(json.dumps(dict(commons.CONFIG_TEMPLATE)))
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        first = ConfigUtil().load()
    ConfigUtil().save(first)
    stable = (new_dir / "config.json").read_text()
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        second = ConfigUtil().load()
    ConfigUtil().save(second)
    assert (new_dir / "config.json").read_text() == stable


def test_resolves_public_blocks_non_public_ips():
    """RC2-SEC-03: loopback/private/link-local/multicast never pass."""
    from apricus.utils import resolves_public

    assert resolves_public("93.184.216.34") is True
    for bad in (
        "127.0.0.1",
        "::1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.10.20",
        "224.0.0.1",
        "0.0.0.0",
        "",
        None,
    ):
        assert resolves_public(bad) is False, bad


def test_resolves_public_fails_closed_on_dns_error(monkeypatch):
    import socket

    from apricus.utils import resolves_public

    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("dns"))
    )
    assert resolves_public("example.com") is False


def test_resolves_public_uses_all_addresses(monkeypatch):
    """One non-public address among several poisons the host (rebind-safe)."""
    import socket

    from apricus.utils import resolves_public

    def fake(host, *a, **k):
        return [
            (socket.AF_INET, None, None, "", ("93.184.216.34", 0)),
            (socket.AF_INET, None, None, "", ("127.0.0.1", 0)),
        ]

    monkeypatch.setattr(socket, "getaddrinfo", fake)
    assert resolves_public("example.com") is False


def test_public_http_url_combines_scheme_and_dns(monkeypatch):
    import socket

    from apricus.utils import public_http_url

    assert public_http_url("https://93.184.216.34/v.mp4") == "https://93.184.216.34/v.mp4"
    assert public_http_url("http://127.0.0.1:8080/x") is None
    assert public_http_url("http://10.1.2.3/v") is None
    assert public_http_url("http://user:pass@93.184.216.34/") is None
    assert public_http_url("file:///etc/passwd") is None
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: (_ for _ in ()).throw(OSError("dns"))
    )
    assert public_http_url("https://example.com/v") is None


def test_update03_readonly_dir_save_raises_and_keeps_original(tmp_path, monkeypatch):
    """RC2-UPDATE-03: realistic write failure (chmod 555 dir): save raises
    OSError, the original file is byte-identical, no .tmp lingers."""
    import os

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = _isolated_dirs(tmp_path, monkeypatch)
    raw = json.dumps(dict(commons.CONFIG_TEMPLATE))
    (new_dir / "config.json").write_text(raw)
    os.chmod(new_dir, 0o555)
    try:
        import pytest

        changed = dict(commons.CONFIG_TEMPLATE)
        changed["audio_volume"] = 44
        with pytest.raises(OSError):
            ConfigUtil().save(changed)
        assert (new_dir / "config.json").read_text() == raw
        assert not (new_dir / "config.json.tmp").exists()
        assert not (new_dir / "config.json.bak").exists()
    finally:
        os.chmod(new_dir, 0o755)


def test_update04_unknown_keys_ignored_and_preserved(tmp_path, monkeypatch):
    """RC2-UPDATE-04: forward-compat property behind safe downgrade —
    unknown keys (e.g. written by a newer version) pass _check, survive
    load, and are written back by save (v1.0 code reads the same way:
    inclusive key check + wholesale dump, verified via git show v1.0)."""
    from unittest.mock import patch

    from apricus import commons
    from apricus.utils import ConfigUtil

    new_dir = _isolated_dirs(tmp_path, monkeypatch)
    cfg = dict(commons.CONFIG_TEMPLATE)
    cfg["newer_feature"] = {"x": 1}
    assert ConfigUtil._check(cfg) is True
    (new_dir / "config.json").write_text(json.dumps(cfg))
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=[]):
        loaded = ConfigUtil().load()
    assert loaded["newer_feature"] == {"x": 1}
    loaded["audio_volume"] = 45
    ConfigUtil().save(loaded)
    assert json.loads((new_dir / "config.json").read_text())["newer_feature"] == {"x": 1}
