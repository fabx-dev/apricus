# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the GNOME Shell wallpaper integration (no display required)."""

import json
import os
import pathlib

import pytest

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
EXT_DIR = os.path.join(REPO_ROOT, "gnome-extension", EXTENSION_UUID)
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
    """Structural contract: background-layer adoption keeps icons above video.

    The extension reparents role-identified actors into the Shell's
    background group and re-pins them above recreated static backgrounds.
    Icons stay above whatever output module VLC picks (verified live on
    Mutter 50 with VLC's automatic `gl` pick: libvlc 3 ignores `--vout`
    for embedded playback).
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


def test_vlc_options_pin_exact_flags_per_renderer():
    """Canonical flags per stored renderer id, none by default.

    These values are inert under LibVLC 3 (verdict C: the media player
    shadows "vout", so VLC always uses its automatic pick), but they
    are still passed in canonical form for forward compatibility.
    """
    from apricus.player.video_player import _vlc_options

    assert _vlc_options(False, "opengl") == [
        "--no-disable-screensaver",
        "--no-interact",
        "--aout=pulse",
        "--vout=opengl",
    ]
    assert _vlc_options(True, "xcb_x11") == [
        "--no-disable-screensaver",
        "--no-interact",
        "--no-audio",
        "--aout=none",
        "--vout=xcb_x11",
    ]
    assert _vlc_options(False, "xcb_xv")[-1] == "--vout=xcb_xv"
    plain = _vlc_options(False, None)
    assert not any(o.startswith("--vout") for o in plain)
    assert "--no-interact" in plain
    assert "--no-interact" in _vlc_options(True, None)
    # Unknown vout names are never passed through to VLC.
    assert not any(o.startswith("--vout") for o in _vlc_options(False, "bogus"))
    assert not any(o.startswith("--vout") for o in _vlc_options(False, "gl"))


def test_renderer_resolution_precedence(monkeypatch):
    """APRICUS_VOUT > saved selection > automatic policy (forward-compat values)."""
    import apricus.player.video_player as player_mod
    from apricus.player.video_player import resolve_renderer_vout

    monkeypatch.setenv("APRICUS_VOUT", "opengl")
    assert resolve_renderer_vout("xcb") == "opengl"
    assert resolve_renderer_vout("auto") == "opengl"
    # Historic UI ids still work as env aliases.
    monkeypatch.setenv("APRICUS_VOUT", "gl")
    assert resolve_renderer_vout("auto") == "opengl"
    monkeypatch.setenv("APRICUS_VOUT", "xcb")
    assert resolve_renderer_vout("auto") == "xcb_x11"
    monkeypatch.setenv("APRICUS_VOUT", "xv")
    assert resolve_renderer_vout("auto") == "xcb_xv"
    monkeypatch.setenv("APRICUS_VOUT", "  ")
    monkeypatch.setattr(player_mod, "integration_active", lambda: True)
    monkeypatch.setattr(player_mod, "is_gnome", lambda: True)
    monkeypatch.setattr(player_mod, "is_wayland", lambda: True)
    assert resolve_renderer_vout("auto") == "opengl"
    assert resolve_renderer_vout("xcb") == "xcb_x11"
    assert resolve_renderer_vout("xv") == "xcb_xv"
    assert resolve_renderer_vout("gl") == "opengl"
    monkeypatch.delenv("APRICUS_VOUT", raising=False)
    assert resolve_renderer_vout("xcb") == "xcb_x11"


def test_renderer_automatic_policy_matrix(monkeypatch):
    """Automatic policy per session/backend state."""
    import apricus.player.video_player as player_mod
    from apricus.player.video_player import resolve_renderer_vout

    monkeypatch.delenv("APRICUS_VOUT", raising=False)

    def env(gnome, wayland, active):
        monkeypatch.setattr(player_mod, "is_gnome", lambda: gnome)
        monkeypatch.setattr(player_mod, "is_wayland", lambda: wayland)
        monkeypatch.setattr(player_mod, "integration_active", lambda: active)

    env(True, True, True)
    assert resolve_renderer_vout("auto") == "opengl"
    env(True, True, False)
    assert resolve_renderer_vout("auto") == "xcb_x11"
    env(True, False, False)
    assert resolve_renderer_vout("auto") is None
    env(False, True, False)
    assert resolve_renderer_vout("auto") is None
    env(False, False, False)
    assert resolve_renderer_vout("auto") is None
    # Explicit selection wins over the policy everywhere.
    env(False, False, False)
    assert resolve_renderer_vout("xv") == "xcb_xv"
    # Unknown saved values fall back to automatic, never to VLC.
    env(True, True, True)
    assert resolve_renderer_vout("bogus") == "opengl"
    env(True, True, False)
    assert resolve_renderer_vout("bogus") == "xcb_x11"
    # Unknown env values are ignored (with warning), never passed to VLC.
    monkeypatch.setenv("APRICUS_VOUT", "bogus-vout")
    assert resolve_renderer_vout("auto") == "xcb_x11"


def test_shipped_metadata_declares_only_reviewed_shell_versions():
    """The bundled extension must only declare versions covered by static
    API review (49/50/51: background group, restacked, _isOverviewWindow,
    tab-list/app-system APIs confirmed identical). Runtime verified on 50
    only; 49/51 stay labeled as such. An over-broad shell-version would
    silently break sorting on upgrade instead of cleanly disabling."""
    with open(EXT_META, encoding="utf-8") as f:
        meta = json.load(f)
    declared = set(meta.get("shell-version") or [])
    assert declared, "extension must declare supported Shell versions"
    assert declared <= {"49", "50", "51"}, f"unreviewed Shell versions: {sorted(declared)}"


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


def test_missing_shell_schema_never_aborts(monkeypatch):
    """Flatpak runtime has no org.gnome.shell schema: Gio.Settings.new()
    would abort the process (GLib fatal, uncatchable). The helper must
    probe with SettingsSchemaSource.lookup() and report inactive.
    """
    from apricus import gnome_shell as gs_mod

    class _NoSchemaSource:
        def lookup(self, *args, **kwargs):
            return None

    monkeypatch.setattr(
        "gi.repository.Gio.SettingsSchemaSource.get_default",
        lambda: _NoSchemaSource(),
    )
    assert gs_mod._shell_settings() is None
    assert gs_mod.extension_is_enabled() is False


def test_x11_lib_declares_all_signatures():
    """Every libX11 entry point needs explicit ctypes argtypes.

    Without them ctypes truncates 64-bit pointers to C int and libX11
    segfaults (fatal, uncatchable — crashed the Flatpak player in the
    legacy stacking guard). Any future addition must extend the table.
    """
    import apricus.player.base_player as mod

    lib = mod._x11_lib()
    assert lib is not None
    for name, (argtypes, restype) in mod._X11_FUNCS.items():
        func = getattr(lib, name)
        assert list(func.argtypes) == argtypes, name
        assert func.restype == restype, name


def test_player_integration_watch_handles_missing_schema(monkeypatch):
    """_watch_shell_integration degrades gracefully without Gio schemas."""
    import apricus.player.base_player as mod

    monkeypatch.setattr(mod, "_shell_settings", lambda: None)

    class Stub:
        pass

    mod.BasePlayer._watch_shell_integration(Stub())
    assert getattr(Stub(), "_integration_watched", False) is False


def test_detection_with_broken_metadata(tmp_path):
    ext = os.path.join(str(tmp_path), EXTENSION_UUID)
    os.makedirs(ext)
    with open(os.path.join(ext, "metadata.json"), "w", encoding="utf-8") as f:
        f.write("{not json")
    assert read_metadata([str(tmp_path)]) is None
    assert extension_is_compatible("50.1", [str(tmp_path)]) is False


def test_integration_active_requires_all_conditions(tmp_path, monkeypatch):
    """Shell verdicts, compatibility fence and the sandbox fallback."""
    dirs = _write_fake_extension(str(tmp_path))
    monkeypatch.setattr(gnome_shell, "is_gnome", lambda: True)
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: True)
    # Hermetic Shell answers: the real session bus (with our extension
    # possibly loaded) must not leak into the matrix.
    monkeypatch.setattr(gnome_shell, "extension_load_state", lambda: None)
    # Shell confirms loaded: True even where metadata is blind or would
    # disagree — a loaded extension proves compatibility itself, since
    # the Shell fences versions on load.
    assert integration_active("50.1", dirs, enabled=True, loaded=True) is True
    assert integration_active("49", dirs, enabled=True, loaded=True) is True
    # Listed as enabled but Shell has NOT loaded it (e.g. Wayland session
    # started before enable): must NOT stand the legacy guard down.
    assert integration_active("50.1", dirs, enabled=True, loaded=False) is False
    # Shell unreachable (sandbox): fall back to the enabled list, still
    # fenced by the declared versions (loaded defaults to _UNSET so the
    # mocked load_state is what runs).
    monkeypatch.setattr(gnome_shell, "extension_is_enabled", lambda: True)
    assert integration_active("50.1", dirs) is True
    assert integration_active("49", dirs) is False
    monkeypatch.setattr(gnome_shell, "extension_is_enabled", lambda: False)
    assert integration_active("50.1", dirs) is False
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
    # Neutral Shell answer by default: real D-Bus must not leak into the
    # matrix (this session may have the extension loaded).
    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: None)

    monkeypatch.setattr(gnome_shell, "extension_dir", lambda *a: None)
    assert integration_status(dirs)[0] == "not_installed"

    # Sandbox-blind filesystem (dir invisible) but Shell-confirmed loaded.
    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: 1)
    assert integration_status(dirs)[0] == "active"
    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: None)

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


def test_shell_toggle_recreates_windows_on_activate(monkeypatch):
    """Enabling mid-session recreates windows (proven window-created path)."""
    import apricus.player.base_player as mod

    calls = []

    class Stub:
        _shell_managed = False
        windows = {}

        def _recreate_wallpaper_windows(self):
            calls.append("recreate")

        def _start_legacy_stacking(self):
            calls.append("legacy")

    monkeypatch.setattr(mod, "integration_active", lambda: True)
    monkeypatch.setattr(mod, "log_status", lambda: None)
    mod.BasePlayer._on_shell_integration_changed(Stub(), None)
    assert calls == ["recreate"]
    monkeypatch.setattr(mod, "integration_active", lambda: False)
    stub = Stub()
    stub._shell_managed = True
    mod.BasePlayer._on_shell_integration_changed(stub, None)
    assert calls == ["recreate", "legacy"]
    assert stub._shell_managed is False
    # No change -> no action.
    calls.clear()
    mod.BasePlayer._on_shell_integration_changed(stub, None)
    assert calls == []


def test_shell_toggle_handler_never_raises(monkeypatch):
    """Detection failures on the settings signal must not break playback."""
    import apricus.player.base_player as mod

    class Stub:
        _shell_managed = False
        windows = {}

        def _recreate_wallpaper_windows(self):
            raise AssertionError("must not run when state is unknown")

        def _start_legacy_stacking(self):
            raise AssertionError("must not run when state is unknown")

    def boom():
        raise RuntimeError("no bus")

    monkeypatch.setattr(mod, "integration_active", boom)
    mod.BasePlayer._on_shell_integration_changed(Stub(), None)


def test_recreate_resets_windows_and_reactivates():
    """Windows are cleaned up, dropped and rebuilt via do_activate."""
    from apricus.player.base_player import BasePlayer

    order = []

    class Win:
        def cleanup(self):
            order.append("cleanup")

        def destroy(self):
            order.append("destroy")

    class Stub:
        def __init__(self):
            self.windows = {"m0": Win(), "m1": None}
            self.reactivated = False

        def do_activate(self):
            self.reactivated = True

    stub = Stub()
    BasePlayer._recreate_wallpaper_windows(stub)
    assert order == ["cleanup", "destroy"]
    assert stub.windows == {"m0": None, "m1": None}
    assert stub.reactivated is True


def test_host_receipt_roundtrip_and_freshness(tmp_path, monkeypatch):
    """Sandbox status receipt: fresh+valid is honored, else ignored."""
    import json
    import time

    from apricus import gnome_shell as mod

    export = tmp_path / "gnome-integration"
    export.mkdir()
    monkeypatch.setattr(mod, "host_export_dir", lambda: str(export))

    def write(payload, age_sec=0):
        p = export / "status.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        if age_sec:
            old = time.time() - age_sec
            os.utime(p, (old, old))

    good = {
        "uuid": mod.EXTENSION_UUID,
        "installed": True,
        "enabled": True,
        "loaded": True,
        "shell": "50.1",
        "checked_at": 1234567890,
    }
    assert mod.read_host_receipt() is None  # missing file
    write({"uuid": "other@x", "installed": True})
    assert mod.read_host_receipt() is None  # wrong uuid
    write({"uuid": mod.EXTENSION_UUID, "installed": "yes"})
    assert mod.read_host_receipt() is None  # malformed shape
    write(good)
    assert mod.read_host_receipt()["loaded"] is True
    write(good, age_sec=mod.RECEIPT_MAX_AGE_SEC + 60)
    assert mod.read_host_receipt() is None  # stale receipts never apply
    write("not json {{{")
    assert mod.read_host_receipt() is None  # corrupt file


def test_sandbox_status_uses_host_receipt(tmp_path, monkeypatch):
    """Blind fs + denied bus + fresh receipt => Shell-verified states."""
    from apricus import gnome_shell as mod

    monkeypatch.setattr(mod, "is_gnome", lambda: True)
    monkeypatch.setattr(mod, "is_wayland", lambda: True)
    monkeypatch.setattr(mod, "extension_dir", lambda *a: None)
    monkeypatch.setattr(mod, "extension_raw_state", lambda: mod._UNREACHABLE)
    export = tmp_path / "gnome-integration"
    export.mkdir()
    monkeypatch.setattr(mod, "host_export_dir", lambda: str(export))

    import json

    def write(enabled, loaded):
        (export / "status.json").write_text(
            json.dumps(
                {
                    "uuid": mod.EXTENSION_UUID,
                    "installed": True,
                    "enabled": enabled,
                    "loaded": loaded,
                    "shell": "50.1",
                    "checked_at": 1234567890,
                }
            ),
            encoding="utf-8",
        )

    write(True, True)
    assert mod.integration_status()[0] == "active"
    write(True, False)
    assert mod.integration_status()[0] == "needs_logout"
    write(False, False)
    assert mod.integration_status()[0] == "disabled"
    old = __import__("time").time() - mod.RECEIPT_MAX_AGE_SEC - 1
    os.utime(export / "status.json", (old, old))
    state, detail = mod.integration_status()
    assert state == "stale"
    assert detail["receipt"]["enabled"] is False


def test_host_zip_and_installer_roundtrip(tmp_path):
    """The distributable ZIP installs byte-identical files (Snap/Flatpak path)."""
    import json
    import shutil
    import subprocess
    import zipfile

    repo = pathlib.Path(__file__).resolve().parent.parent
    if shutil.which("zip") is None or shutil.which("unzip") is None:
        pytest.skip("zip/unzip not available")
    out_zip = tmp_path / "ext.zip"
    proc = subprocess.run(
        [str(repo / "gnome-extension" / "build-zip.sh"), str(out_zip)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    with zipfile.ZipFile(out_zip) as zf:
        names = sorted(zf.namelist())
    assert names == [
        "apricus-integration@fabx-dev/",
        "apricus-integration@fabx-dev/extension.js",
        "apricus-integration@fabx-dev/metadata.json",
    ], names
    with zipfile.ZipFile(out_zip) as zf:
        meta = json.loads(zf.read("apricus-integration@fabx-dev/metadata.json"))
    assert meta["uuid"] == EXTENSION_UUID
    assert set(meta["shell-version"]) == {"49", "50", "51"}
    with zipfile.ZipFile(out_zip) as zf:
        for name in ("metadata.json", "extension.js"):
            zipped = zf.read(f"apricus-integration@fabx-dev/{name}")
            source = (repo / "gnome-extension" / EXTENSION_UUID / name).read_bytes()
            assert zipped == source, name
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = dict(os.environ, HOME=str(fake_home))
    installer = str(repo / "gnome-extension" / "install-host.sh")
    proc = subprocess.run(
        ["bash", installer, "--zip", str(out_zip)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    installed = fake_home / ".local/share/gnome-shell/extensions" / EXTENSION_UUID
    for name in ("metadata.json", "extension.js"):
        src = (repo / "gnome-extension" / EXTENSION_UUID / name).read_bytes()
        assert (installed / name).read_bytes() == src
    # The host run also leaves a machine-readable status receipt next to
    # the ZIP (shape only here: enabled/loaded depend on the live Shell).
    receipt = tmp_path / "status.json"
    assert receipt.is_file(), "install-host.sh must write status.json in --zip mode"
    data = json.loads(receipt.read_text(encoding="utf-8"))
    assert data["uuid"] == EXTENSION_UUID and data["installed"] is True
    assert isinstance(data["enabled"], bool) and isinstance(data["loaded"], bool)
    assert abs(__import__("time").time() - data["checked_at"]) < 300
    # Re-running over an existing install (the update flow) is idempotent.
    proc = subprocess.run(
        ["bash", installer, "--zip", str(out_zip)],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    assert proc.returncode == 0, proc.stderr
    for name in ("metadata.json", "extension.js"):
        src = (repo / "gnome-extension" / EXTENSION_UUID / name).read_bytes()
        assert (installed / name).read_bytes() == src
    assert sorted(p.name for p in installed.iterdir()) == [
        "extension.js",
        "metadata.json",
    ]
    proc = subprocess.run(
        ["bash", installer, "--bogus"], capture_output=True, text=True, timeout=60, env=env
    )
    assert proc.returncode != 0


def test_respawn_player_forces_fresh_instance():
    from apricus import server as srv

    calls = []

    class Stub:
        config = {"mode": "MODE_VIDEO", "data_source": {"Default": "/a.mp4"}}

        def _load_config(self):
            pass

        def _setup_player(self, mode, data_source=None, monitor=None, force_respawn=False):
            calls.append((mode, force_respawn))

    assert srv.ApricusServer.respawn_player(Stub()) is True
    assert calls == [("MODE_VIDEO", True)]


def test_respawn_player_noop_without_media_mode():
    from apricus import server as srv

    calls = []

    class Stub:
        config = {"mode": "MODE_NULL"}

        def _load_config(self):
            pass

        def _setup_player(self, *args, **kwargs):
            calls.append(args)

    assert srv.ApricusServer.respawn_player(Stub()) is False
    assert calls == []


def _deadline_player(*, resolve_error=None, first_frame=False, started_ago=None, playing=True):
    """VideoPlayer-shaped stub for playback_error (no display needed)."""
    import time

    from apricus.player.video_player import VideoPlayer

    class Win:
        def playback_error(self):
            return None

        _first_frame_reported = first_frame

        def is_playing(self):
            return playing

    class Stub:
        FIRST_FRAME_TIMEOUT_SEC = VideoPlayer.FIRST_FRAME_TIMEOUT_SEC
        _resolve_error = resolve_error
        windows = {"m": Win()}
        _media_started_at = None if started_ago is None else time.monotonic() - started_ago

        def is_playing(self):
            return playing

        def _is_actually_playing(self):
            return playing

    return Stub()


def test_no_frames_reported_only_after_timeout():
    from apricus.player.video_player import VideoPlayer

    assert VideoPlayer.playback_error(_deadline_player()) is None
    old = _deadline_player(started_ago=VideoPlayer.FIRST_FRAME_TIMEOUT_SEC + 5)
    assert VideoPlayer.playback_error(old) == "No video frames received yet"
    framed = _deadline_player(first_frame=True, started_ago=VideoPlayer.FIRST_FRAME_TIMEOUT_SEC + 5)
    assert VideoPlayer.playback_error(framed) is None
    resolving = _deadline_player(
        resolve_error="boom", started_ago=VideoPlayer.FIRST_FRAME_TIMEOUT_SEC + 5
    )
    assert VideoPlayer.playback_error(resolving) == "boom"


def test_no_frames_silent_when_paused():
    """Manual pauses, auto-pause and buffering must never trip the watchdog."""
    from apricus.player.video_player import VideoPlayer

    paused = _deadline_player(playing=False, started_ago=VideoPlayer.FIRST_FRAME_TIMEOUT_SEC + 60)
    assert VideoPlayer.playback_error(paused) is None


def test_restored_queue_index_clamps_and_prefers_key():
    from apricus.player.video_player import VideoPlayer

    saved = {"M#1": 5, "M#0": 2}
    assert VideoPlayer._restored_queue_index(saved, "M#1", "M", 3) == 2
    assert VideoPlayer._restored_queue_index(saved, "M#9", "M", 3) == 0
    assert VideoPlayer._restored_queue_index(saved, "M#9", "M#0", 3) == 2
    assert VideoPlayer._restored_queue_index(None, "M#1", "M", 3) == 0
    assert VideoPlayer._restored_queue_index({"M#1": -1}, "M#1", "M", 3) == 0
    assert VideoPlayer._restored_queue_index({"M#1": "x"}, "M#1", "M", 3) == 0
    assert VideoPlayer._restored_queue_index({"M#1": 7}, "M#1", "M", 0) == 0
    assert VideoPlayer._restored_queue_index({"M#1": 7}, "M#1", "M", None) == 0


def test_stable_monitor_key_matches_apply_source_keys():
    from apricus.player.video_player import VideoPlayer

    class Mon:
        def __init__(self, name, x):
            self._name = name
            self._x = x

        def get_model(self):
            return self._name

        def get_geometry(self):
            class G:
                pass

            g = G()
            g.x = self._x
            g.y = 0
            return g

    class P:
        def __init__(self):
            m1, m2 = Mon("Dell", 0), Mon("Dell", 1920)
            self.windows = {m1: object(), m2: object()}

    p = P()
    assert VideoPlayer._stable_monitor_key(p, list(p.windows)[1]) == "Dell#1"
    assert VideoPlayer._stable_monitor_key(p, list(p.windows)[0]) == "Dell#0"


def test_save_queue_index_persists_and_sanitizes():
    from apricus.player.video_player import VideoPlayer

    saved = {}

    class FakeConfigUtil:
        def save(self, config):
            saved.update(config)

    import apricus.player.video_player as player_mod

    real_configutil = player_mod.ConfigUtil
    player_mod.ConfigUtil = FakeConfigUtil
    try:

        class P:
            config = {}

        p = P()
        VideoPlayer._save_queue_index(p, "M#1", 4)
        assert saved.get("playlist_position") == {"M#1": 4}
        VideoPlayer._save_queue_index(p, "", 4)
        assert saved.get("playlist_position") == {"M#1": 4}
    finally:
        player_mod.ConfigUtil = real_configutil


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("1", "active"),
        ("1.0", "active"),
        ("2", "disabled"),
        ("3", "error"),
        ("4", "incompatible"),
        ("denied", "unknown"),
    ],
)
def test_host_check_only_never_installs_or_enables(tmp_path, monkeypatch, raw, expected):
    """A real script run against a fake Shell must only update its receipt."""
    import subprocess

    fake_home = tmp_path / "home"
    export = tmp_path / "export"
    bins = tmp_path / "bin"
    for directory in (fake_home, export, bins):
        directory.mkdir()
    installer = pathlib.Path(REPO_ROOT) / "gnome-extension/install-host.sh"
    commands = {
        "gnome-shell": "echo 'GNOME Shell 50.1'",
        "gnome-extensions": 'if [ "$1" != list ]; then echo mutation >> "$HOME/unexpected"; exit 1; fi',
        "gdbus": "exit 1" if raw == "denied" else f'''echo "({{'state': <{raw}>}},)"''',
    }
    for name, body in commands.items():
        command = bins / name
        command.write_text("#!/bin/bash\n" + body + "\n")
        command.chmod(0o755)
    # The ZIP deliberately does not exist: verification must not unpack it.
    proc = subprocess.run(
        ["bash", str(installer), "--zip", str(export / "absent.zip"), "--check"],
        env=dict(os.environ, HOME=str(fake_home), PATH=str(bins) + os.pathsep + os.environ["PATH"]),
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    assert not list(fake_home.iterdir()), "--check must neither install nor enable"
    assert sorted(p.name for p in export.iterdir()) == ["status.json"]
    data = json.loads((export / "status.json").read_text())
    assert data["verified"] is (raw != "denied")
    monkeypatch.setattr(gnome_shell, "is_gnome", lambda: True)
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: True)
    monkeypatch.setattr(gnome_shell, "extension_dir", lambda *a: None)
    monkeypatch.setattr(gnome_shell, "host_export_dir", lambda: str(export))
    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: gnome_shell._UNREACHABLE)
    assert gnome_shell.integration_status()[0] == expected
