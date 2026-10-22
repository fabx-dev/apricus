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
    extension_load_state,
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


def test_vlc_options_pin_exact_flags_per_renderer():
    """Renderer selection: exact flags per vout, none by default."""
    from apricus.player.video_player import _vlc_options

    assert _vlc_options(False, "xcb") == [
        "--no-disable-screensaver",
        "--no-interact",
        "--aout=pulse",
        "--vout=xcb",
    ]
    assert _vlc_options(True, "xcb") == [
        "--no-disable-screensaver",
        "--no-interact",
        "--no-audio",
        "--aout=none",
        "--vout=xcb",
    ]
    assert _vlc_options(False, "gl")[-1] == "--vout=gl"
    assert _vlc_options(False, "xv")[-1] == "--vout=xv"
    plain = _vlc_options(False, None)
    assert not any(o.startswith("--vout") for o in plain)
    assert "--no-interact" in plain
    assert "--no-interact" in _vlc_options(True, None)
    # Unknown vout names are never passed through to VLC.
    assert not any(o.startswith("--vout") for o in _vlc_options(False, "bogus"))


def test_renderer_resolution_precedence(monkeypatch):
    """APRICUS_VOUT > saved selection > automatic policy."""
    import apricus.player.video_player as player_mod
    from apricus.player.video_player import resolve_renderer_vout

    monkeypatch.setenv("APRICUS_VOUT", "gl")
    assert resolve_renderer_vout("xcb") == "gl"
    assert resolve_renderer_vout("auto") == "gl"
    monkeypatch.setenv("APRICUS_VOUT", "  ")
    monkeypatch.setattr(player_mod, "integration_active", lambda: True)
    monkeypatch.setattr(player_mod, "is_gnome", lambda: True)
    monkeypatch.setattr(player_mod, "is_wayland", lambda: True)
    assert resolve_renderer_vout("auto") == "gl"
    assert resolve_renderer_vout("xcb") == "xcb"
    assert resolve_renderer_vout("xv") == "xv"
    assert resolve_renderer_vout("gl") == "gl"
    monkeypatch.delenv("APRICUS_VOUT", raising=False)
    assert resolve_renderer_vout("xcb") == "xcb"


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
    assert resolve_renderer_vout("auto") == "gl"
    env(True, True, False)
    assert resolve_renderer_vout("auto") == "xcb"
    env(True, False, False)
    assert resolve_renderer_vout("auto") is None
    env(False, True, False)
    assert resolve_renderer_vout("auto") is None
    env(False, False, False)
    assert resolve_renderer_vout("auto") is None
    # Explicit selection wins over the policy everywhere.
    env(False, False, False)
    assert resolve_renderer_vout("xv") == "xv"
    # Unknown saved values fall back to automatic, never to VLC.
    env(True, True, True)
    assert resolve_renderer_vout("bogus") == "gl"
    env(True, True, False)
    assert resolve_renderer_vout("bogus") == "xcb"
    # Unknown env values are ignored (with warning), never passed to VLC.
    monkeypatch.setenv("APRICUS_VOUT", "bogus-vout")
    assert resolve_renderer_vout("auto") == "xcb"


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
    proc = subprocess.run(
        ["bash", installer, "--bogus"], capture_output=True, text=True, timeout=60, env=env
    )
    assert proc.returncode != 0

def _deadline_player(*, resolve_error=None, first_frame=False, started_ago=None):
    """VideoPlayer-shaped stub for playback_error() (no display needed)."""

    import time

    from apricus.player.video_player import VideoPlayer

    class Win:
        def playback_error(self):
            return None

        _first_frame_reported = first_frame

    class Stub:
        FIRST_FRAME_TIMEOUT_SEC = VideoPlayer.FIRST_FRAME_TIMEOUT_SEC
        _resolve_error = resolve_error
        windows = {"m": Win()}
        _media_started_at = (
            None if started_ago is None else time.monotonic() - started_ago
        )

    return Stub()


def test_no_frames_reported_only_after_timeout():
    from apricus.player.video_player import VideoPlayer

    fresh = _deadline_player()
    assert VideoPlayer.playback_error(fresh) is None
    old = _deadline_player(
        started_ago=VideoPlayer.FIRST_FRAME_TIMEOUT_SEC + 5
    )
    assert VideoPlayer.playback_error(old) == "No video frames received yet"
    framed = _deadline_player(
        first_frame=True, started_ago=VideoPlayer.FIRST_FRAME_TIMEOUT_SEC + 5
    )
    assert VideoPlayer.playback_error(framed) is None
    resolving = _deadline_player(
        resolve_error="boom", started_ago=VideoPlayer.FIRST_FRAME_TIMEOUT_SEC + 5
    )
    assert VideoPlayer.playback_error(resolving) == "boom"


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
