"""Tests for login autostart helpers (no display, no systemd required)."""

import os
from unittest.mock import patch

from apricus import utils


def test_systemd_unit_content(tmp_path, monkeypatch):
    monkeypatch.setattr(utils.shutil, "which", lambda name: "/usr/bin/apricus")
    monkeypatch.setattr(utils.sys, "prefix", "/usr")
    monkeypatch.setattr(utils.sys, "executable", "/usr/bin/python3")
    content = utils.systemd_unit_content()
    assert "ExecStart=/usr/bin/apricus --fast -b" in content
    assert "After=graphical-session.target" in content
    assert "Restart=on-failure" in content
    assert "WantedBy=graphical-session.target" in content


def test_setup_systemd_enable_writes_unit(tmp_path, monkeypatch):
    unit_dir = tmp_path / "systemd" / "user"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(utils.shutil, "which", lambda name: "/usr/bin/apricus")
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return None

    with patch.object(utils.subprocess, "run", side_effect=fake_run):
        assert utils.setup_systemd_autostart(True) is True
    unit = unit_dir / utils.SYSTEMD_UNIT_NAME
    assert unit.is_file()
    assert "ExecStart=" in unit.read_text()
    assert any("daemon-reload" in c for c in calls)
    assert any("enable" in c for c in calls)


def test_setup_systemd_disable_removes_unit(tmp_path, monkeypatch):
    unit_dir = tmp_path / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    (unit_dir / utils.SYSTEMD_UNIT_NAME).write_text("x")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    with patch.object(utils.subprocess, "run", return_value=None):
        assert utils.setup_systemd_autostart(False) is True
    assert not (unit_dir / utils.SYSTEMD_UNIT_NAME).exists()


def test_setup_systemd_failure_is_false_not_raise(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    def boom(*args, **kwargs):
        raise OSError("no systemctl")

    with patch.object(utils.subprocess, "run", side_effect=boom):
        with patch.object(utils.os, "makedirs", side_effect=OSError("ro")):
            assert utils.setup_systemd_autostart(True) is False


def test_systemd_unit_uses_venv_python_inside_venv(tmp_path, monkeypatch):
    monkeypatch.setattr(utils.shutil, "which", lambda name: "/tmp/fake/bin/apricus")
    monkeypatch.setattr(utils.sys, "prefix", "/tmp/fake")
    monkeypatch.setattr(utils.sys, "base_prefix", "/usr")
    monkeypatch.setattr(utils.sys, "executable", "/tmp/fake/bin/python")
    content = utils.systemd_unit_content()
    assert "ExecStart=/tmp/fake/bin/python /tmp/fake/bin/apricus --fast -b" in content


def test_fast_env_skips_refresh(monkeypatch):
    monkeypatch.setenv("HIDAMARI_FAST", "1")
    assert os.environ.get("HIDAMARI_FAST") == "1"
