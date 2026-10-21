# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the GNOME Shell wallpaper integration (no display required)."""

import json
import os

from apricus import gnome_shell
from apricus.gnome_shell import (
    EXTENSION_UUID,
    WALLPAPER_WINDOW_ROLE,
    extension_dir,
    extension_is_compatible,
    extension_is_enabled,
    integration_active,
    log_status,
    read_metadata,
)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXT_DIR = os.path.join(
    REPO_ROOT, "gnome-extension", EXTENSION_UUID
)
EXT_JS = os.path.join(EXT_DIR, "extension.js")
EXT_META = os.path.join(EXT_DIR, "metadata.json")


def _write_fake_extension(base, shell_versions=("50",)):
    ext = os.path.join(base, EXTENSION_UUID)
    os.makedirs(ext)
    with open(os.path.join(ext, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump({"uuid": EXTENSION_UUID, "shell-version": list(shell_versions)}, f)
    return [base]


def test_role_constant_matches_extension_js():
    """The role the player sets must be the role the extension adopts."""
    with open(EXT_JS, encoding="utf-8") as f:
        js = f.read()
    assert f"WALLPAPER_ROLE = '{WALLPAPER_WINDOW_ROLE}'" in js


def test_metadata_is_valid_and_version_pinned():
    with open(EXT_META, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["uuid"] == EXTENSION_UUID
    assert os.path.basename(EXT_DIR) == EXTENSION_UUID
    assert "50" in meta["shell-version"]
    assert "user" in meta.get("session-modes", [])


def test_extension_adopts_into_background_group():
    """Structural contract: reparent + re-pin + cleanup + no renderer spawn."""
    with open(EXT_JS, encoding="utf-8") as f:
        js = f.read()
    assert "_backgroundGroup" in js
    assert "remove_child(actor)" in js and "add_child(actor)" in js
    assert "set_child_above_sibling(actor, null)" in js
    assert "child-added" in js
    assert "'unmanaged'" in js or '"unmanaged"' in js
    assert "get_tab_list" in js  # focus is returned, never kept
    assert "Gio.Subprocess" not in js  # the player owns playback, not the Shell
    assert "owns_window" not in js  # XWayland renderer: role match, not WaylandClient


def test_detection_with_compatible_metadata(tmp_path):
    dirs = _write_fake_extension(str(tmp_path), ("50", "51"))
    assert extension_dir(dirs) is not None
    meta = read_metadata(dirs)
    assert meta["uuid"] == EXTENSION_UUID
    assert extension_is_compatible("50.1", dirs) is True
    assert extension_is_compatible("51", dirs) is True
    assert extension_is_compatible("49", dirs) is False


def test_detection_without_extension(tmp_path):
    assert extension_dir([str(tmp_path)]) is None
    assert read_metadata([str(tmp_path)]) is None
    assert extension_is_compatible("50.1", [str(tmp_path)]) is False


def test_detection_with_broken_metadata(tmp_path):
    ext = os.path.join(str(tmp_path), EXTENSION_UUID)
    os.makedirs(ext)
    with open(os.path.join(ext, "metadata.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert read_metadata([str(tmp_path)]) is None
    assert extension_is_compatible("50.1", [str(tmp_path)]) is False


def test_integration_active_requires_all_conditions(tmp_path, monkeypatch):
    dirs = _write_fake_extension(str(tmp_path))
    monkeypatch.setattr(gnome_shell, "is_gnome", lambda: True)
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: True)
    assert integration_active("50.1", dirs, enabled=True) is True
    assert integration_active("50.1", dirs, enabled=False) is False
    assert integration_active("49", dirs, enabled=True) is False
    assert integration_active("50.1", [str(tmp_path / "empty")], enabled=True) is False
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: False)
    assert integration_active("50.1", dirs, enabled=True) is False


def test_enabled_check_never_raises_without_schema(monkeypatch):
    class Boom:
        @staticmethod
        def new(_schema):
            raise Exception("no schema in this env")

    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio as RealGio

    monkeypatch.setattr(RealGio.Settings, "new", Boom.new)
    assert extension_is_enabled() is False


def test_log_status_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(gnome_shell, "is_gnome", lambda: True)
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: True)
    monkeypatch.setattr(gnome_shell, "extension_dir", lambda *a: None)
    log_status()  # not installed
    dirs = _write_fake_extension(str(tmp_path), ("49",))
    monkeypatch.setattr(gnome_shell, "extension_dir", lambda *a: dirs[0] + "/" + EXTENSION_UUID)
    monkeypatch.setattr(gnome_shell, "shell_version", lambda: "50.1")
    log_status()  # incompatible
