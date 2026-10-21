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
    extension_load_state,
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


def test_extension_manages_stacking_in_place():
    """Structural contract: background-layer adoption with pixmap rendering.

    The extension reparents role-identified actors into the Shell's
    background group and re-pins them above recreated static backgrounds.
    This requires pixmap rendering (VLC `--vout=xcb`): GPU-direct outputs
    bypass the pixmap via overlay planes that no stacking order can place
    below DING (verified live on Mutter 50).
    """
    with open(EXT_JS, encoding="utf-8") as f:
        js = f.read()
    assert "_backgroundGroup" in js
    assert "remove_child(actor)" in js and "add_child(actor)" in js
    assert "set_child_above_sibling(actor, null)" in js
    assert "child-added" in js
    assert "'unmanaged'" in js or '"unmanaged"' in js
    assert "'destroy'" in js or '"destroy"' in js  # drop record, never touch dead actors
    assert "get_tab_list" in js  # focus is returned, never kept
    assert "Gio.Subprocess" not in js  # the player owns playback, not the Shell
    assert "owns_window" not in js  # XWayland renderer: role match, not WaylandClient


def test_vlc_options_pin_exact_flags_per_session(monkeypatch):
    """Renderer selection: xcb only on GNOME Wayland, GPU default elsewhere."""
    from apricus.player.video_player import _vlc_options

    assert _vlc_options(False, True) == [
        "--no-disable-screensaver",
        "--no-interact",
        "--aout=pulse",
        "--vout=xcb",
    ]
    assert _vlc_options(True, True) == [
        "--no-disable-screensaver",
        "--no-interact",
        "--no-audio",
        "--aout=none",
        "--vout=xcb",
    ]
    plain = _vlc_options(False, False)
    assert "--vout=xcb" not in plain and "--no-interact" in plain
    assert "--no-interact" in _vlc_options(True, False)


def test_shipped_metadata_declares_only_verified_shell_versions():
    """The bundled extension must not claim GNOME versions that were never
    tested live (51+): an over-broad shell-version silently breaks sorting
    on upgrade instead of cleanly disabling the integration."""
    with open(EXT_META, encoding="utf-8") as f:
        meta = json.load(f)
    declared = set(meta.get("shell-version") or [])
    assert declared, "extension must declare supported Shell versions"
    assert declared <= {"50"}, f"untested Shell versions declared: {sorted(declared)}"


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
    # Shell confirms loaded.
    assert integration_active("50.1", dirs, enabled=True, loaded=True) is True
    # Listed as enabled but Shell has NOT loaded it (e.g. Wayland session
    # started before enable): must NOT stand the legacy guard down.
    assert integration_active("50.1", dirs, enabled=True, loaded=False) is False
    # Shell unreachable (sandbox): fall back to the enabled list.
    monkeypatch.setattr(gnome_shell, "extension_is_enabled", lambda: True)
    assert integration_active("50.1", dirs, loaded=None) is True
    monkeypatch.setattr(gnome_shell, "extension_is_enabled", lambda: False)
    assert integration_active("50.1", dirs, loaded=None) is False
    assert integration_active("49", dirs, enabled=True, loaded=True) is False
    assert integration_active("50.1", [str(tmp_path / "empty")], enabled=True) is False
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: False)
    assert integration_active("50.1", dirs, enabled=True, loaded=True) is False


def test_load_state_true_only_when_shell_reports_enabled(monkeypatch):
    class FakeProxy:
        def __init__(self, payload=None, fail=False):
            self.payload = payload
            self.fail = fail

        def GetExtensionInfo(self, _uuid):
            if self.fail:
                raise Exception("sandbox denial")
            return self.payload

    import apricus.gnome_shell as mod

    class FakeBus:
        def __init__(self, proxy):
            self.proxy = proxy

        def get(self, *_args):
            return self.proxy

    import pydbus

    monkeypatch.setattr(pydbus, "SessionBus", lambda: FakeBus(FakeProxy({"state": 1})))
    assert mod.extension_load_state() is True
    monkeypatch.setattr(pydbus, "SessionBus", lambda: FakeBus(FakeProxy({"state": 3})))
    assert mod.extension_load_state() is False
    monkeypatch.setattr(pydbus, "SessionBus", lambda: FakeBus(FakeProxy({})))
    assert mod.extension_load_state() is False
    monkeypatch.setattr(pydbus, "SessionBus", lambda: FakeBus(FakeProxy(fail=True)))
    assert mod.extension_load_state() is None


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


def test_status_matrix_for_setup_ui(tmp_path, monkeypatch):
    from apricus.gnome_shell import _UNREACHABLE, integration_status

    dirs = _write_fake_extension(str(tmp_path), ("50",))
    monkeypatch.setattr(gnome_shell, "is_gnome", lambda: True)
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: True)
    monkeypatch.setattr(gnome_shell, "shell_version", lambda: "50.1")

    monkeypatch.setattr(gnome_shell, "extension_dir", lambda *a: None)
    assert integration_status(dirs)[0] == "not_installed"

    monkeypatch.setattr(gnome_shell, "extension_dir", lambda *a: str(tmp_path))
    monkeypatch.setattr(gnome_shell, "read_metadata", lambda *a: {"shell-version": ["49"]})
    assert integration_status(dirs)[0] == "incompatible"

    monkeypatch.setattr(gnome_shell, "read_metadata", lambda *a: {"shell-version": ["50"]})
    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: _UNREACHABLE)
    assert integration_status(dirs)[0] == "unknown"

    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: 1)
    state, detail = integration_status(dirs)
    assert state == "active" and detail["shell"] == "50.1"

    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: 3)
    assert integration_status(dirs)[0] == "error"

    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: 4)
    assert integration_status(dirs)[0] == "incompatible"

    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: None)
    monkeypatch.setattr(gnome_shell, "extension_is_enabled", lambda: True)
    assert integration_status(dirs)[0] == "needs_logout"
    monkeypatch.setattr(gnome_shell, "extension_is_enabled", lambda: False)
    assert integration_status(dirs)[0] == "not_discovered"
    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: 2)
    assert integration_status(dirs)[0] == "disabled"

    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: False)
    assert integration_status(dirs)[0] == "unknown"


def test_log_status_never_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(gnome_shell, "is_gnome", lambda: True)
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: True)
    monkeypatch.setattr(gnome_shell, "extension_dir", lambda *a: None)
    log_status()  # not installed
    dirs = _write_fake_extension(str(tmp_path), ("49",))
    monkeypatch.setattr(gnome_shell, "extension_dir", lambda *a: dirs[0] + "/" + EXTENSION_UUID)
    monkeypatch.setattr(gnome_shell, "shell_version", lambda: "50.1")
    log_status()  # incompatible
