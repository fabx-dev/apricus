# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""GNOME Shell wallpaper integration (Wayland/DING support).

On GNOME Wayland, ordinary X11/XWayland restacking cannot place our video
windows below DING's native Wayland desktop window. The companion extension
``apricus-integration@fabx-dev`` (shipped under ``gnome-extension/``)
hosts our role-identified wallpaper windows in the Shell's background
layer; icons stay above the video because the Shell parents our windows
into its background group, whatever output module VLC picks (verdict C:
under LibVLC 3 the embedded player always uses VLC's automatic pick —
see docs/notes/vlc3-vout-investigation.md).

This module only *detects* the integration; it never talks to the
compositor. The extension works autonomously by matching the window role
set by the player (see :data:`WALLPAPER_WINDOW_ROLE`).

All helpers are best-effort and never raise: a missing schema, a denied
dconf read (sandbox), or an unreadable metadata file simply means the
integration is reported as inactive and the player keeps its legacy
X11 stacking behavior.
"""

import json
import logging
import os
import subprocess
import sys

from apricus.commons import LOGGER_NAME
from apricus.utils import is_gnome, is_wayland

logger = logging.getLogger(LOGGER_NAME)

EXTENSION_UUID = "apricus-integration@fabx-dev"

# WM_WINDOW_ROLE set on every player wallpaper window before mapping.
# Must match WALLPAPER_ROLE in gnome-extension/.../extension.js.
WALLPAPER_WINDOW_ROLE = "apricus-wallpaper"


def _candidate_dirs(search_dirs=None):
    """Directories where a user- or system-wide Shell extension may live."""
    if search_dirs is not None:
        return list(search_dirs)
    home = os.path.expanduser("~")
    return [
        os.path.join(home, ".local", "share", "gnome-shell", "extensions"),
        "/usr/share/gnome-shell/extensions",
        "/usr/local/share/gnome-shell/extensions",
    ]


def extension_dir(search_dirs=None):
    """Installed path of our Shell extension, or None when absent."""
    for base in _candidate_dirs(search_dirs):
        candidate = os.path.join(base, EXTENSION_UUID)
        if os.path.isfile(os.path.join(candidate, "metadata.json")):
            return candidate
    return None


HOST_INSTALLER = "install-host.sh"
APP_ID_FLATPAK = "io.github.fabx_dev.Apricus"
APP_DIR_SNAP = "apricus"


def _bundled_helpers_dirs(search_roots=None):
    """Directories that may hold the shipped host-integration helpers.

    Layout (installed by meson into pkgdatadir/gnome-extension/):
    ``<dir>/apricus-integration@fabx-dev/{metadata.json,extension.js}``
    plus ``<dir>/install-host.sh``.
    """
    if search_roots is not None:
        return list(search_roots)
    roots = []
    snap = os.environ.get("SNAP")
    if snap:
        roots.append(os.path.join(snap, "usr", "share", "apricus", "gnome-extension"))
        roots.append(os.path.join(snap, "share", "apricus", "gnome-extension"))
    flatpak_id = os.environ.get("FLATPAK_ID")
    if flatpak_id or os.path.isfile("/.flatpak-info"):
        roots.append("/app/share/apricus/gnome-extension")
    roots.append(os.path.join(sys.prefix, "share", "apricus", "gnome-extension"))
    home = os.path.expanduser("~")
    roots.append(os.path.join(home, ".local", "share", "apricus", "gnome-extension"))
    roots.append("/usr/share/apricus/gnome-extension")
    roots.append("/usr/local/share/apricus/gnome-extension")
    return roots


def bundled_extension_source(search_roots=None):
    """Shipped (uninstalled-on-host) extension dir, or None.

    Unlike :func:`extension_dir`, which finds extensions GNOME Shell can
    load, this finds the copy bundled *inside* our own package (system,
    Snap or Flatpak install) that the guided host install copies out.
    """
    for base in _bundled_helpers_dirs(search_roots):
        candidate = os.path.join(base, EXTENSION_UUID)
        if os.path.isfile(os.path.join(candidate, "metadata.json")) and os.path.isfile(
            os.path.join(candidate, "extension.js")
        ):
            return candidate
    return None


def bundled_installer_path(search_roots=None):
    """Shipped ``install-host.sh`` path, or None when not packaged."""
    for base in _bundled_helpers_dirs(search_roots):
        candidate = os.path.join(base, HOST_INSTALLER)
        if os.path.isfile(candidate):
            return candidate
    return None


def host_export_dir():
    """User-visible directory for the host install files.

    Chosen so the *same* path works inside the sandbox and on the host:
    Snap user dirs (``~/snap/<app>/common``) and the Flatpak app-data
    dir (``~/.var/app/<id>``) are the same location on both sides.
    """
    from apricus.utils import is_flatpak, is_snap

    if is_snap():
        base = os.environ.get("SNAP_USER_COMMON") or os.path.expanduser(
            os.path.join("~", "snap", APP_DIR_SNAP, "common")
        )
    elif is_flatpak():
        base = os.path.join(os.path.expanduser("~"), ".var", "app", APP_ID_FLATPAK, "data")
    else:
        base = os.path.join(os.path.expanduser("~"), ".cache", "apricus")
    return os.path.join(base, "gnome-integration")


def host_install_command(export_dir):
    """The one host command the user runs (displayed, never executed)."""
    return (
        f'bash "{os.path.join(export_dir, HOST_INSTALLER)}" '
        f'--zip "{os.path.join(export_dir, EXTENSION_UUID + ".zip")}" --enable'
    )


def _write_extension_zip(source_dir, zip_path):
    """Create the distributable extension ZIP (stdlib only, no binaries)."""
    import zipfile

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ("metadata.json", "extension.js"):
            with open(os.path.join(source_dir, name), "rb") as f:
                zf.writestr(f"{EXTENSION_UUID}/{name}", f.read())


def prepare_host_install(dest_dir=None, search_roots=None):
    """Export the host install files; return ``(export_dir, command)``.

    Writes the extension ZIP plus a copy of ``install-host.sh``. Never
    executes anything on the host and never touches sandbox-external
    paths beyond the user-visible export dir. Raises RuntimeError with a
    human-readable message when the bundled files are missing.
    """
    source = bundled_extension_source(search_roots)
    if source is None:
        raise RuntimeError("bundled extension files not found in this package")
    installer = bundled_installer_path(search_roots)
    if installer is None:
        raise RuntimeError("bundled installer script not found in this package")
    export_dir = dest_dir or host_export_dir()
    os.makedirs(export_dir, exist_ok=True)
    _write_extension_zip(source, os.path.join(export_dir, EXTENSION_UUID + ".zip"))
    with open(installer, "rb") as f:
        payload = f.read()
    installer_dest = os.path.join(export_dir, HOST_INSTALLER)
    with open(installer_dest, "wb") as f:
        f.write(payload)
    try:
        os.chmod(installer_dest, 0o755)
    except OSError:  # noqa: BLE001 - best effort (e.g. noexec mounts)
        pass
    return export_dir, host_install_command(export_dir)


def read_metadata(search_dirs=None):
    """Parsed metadata.json of our extension, or None when unavailable."""
    path = extension_dir(search_dirs)
    if path is None:
        return None
    try:
        with open(os.path.join(path, "metadata.json"), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        logger.debug(f"[ShellIntegration] unreadable metadata: {e}")
        return None
    return data if isinstance(data, dict) else None


def shell_version():
    """Current GNOME Shell version (e.g. ``'50.1'``), or None when unknown."""
    try:
        out = subprocess.run(
            ["gnome-shell", "--version"],
            shell=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        ).stdout.decode("utf-8", "replace")
    except (OSError, subprocess.TimeoutExpired) as e:
        logger.debug(f"[ShellIntegration] gnome-shell --version failed: {e}")
        return None
    # Expected: "GNOME Shell 50.1"
    parts = out.strip().split()
    return parts[-1] if len(parts) >= 3 else None


def _major(version):
    try:
        return str(version).strip().split(".")[0]
    except (AttributeError, IndexError):
        return ""


def extension_is_compatible(shell_version=None, search_dirs=None):
    """True when our extension declares support for the running Shell."""
    meta = read_metadata(search_dirs)
    if meta is None:
        return False
    if shell_version is None:
        shell_version = globals()["shell_version"]()
    if not shell_version:
        return False
    supported = meta.get("shell-version") or []
    return _major(shell_version) in {_major(v) for v in supported}


# GNOME Shell ExtensionState (extensionSystem.js): only ENABLED means the
# extension code is actually loaded and running in the compositor.
_EXTENSION_STATE_ENABLED = 1


# Raw Shell states we distinguish explicitly (extensionSystem.js).
_EXTENSION_STATE_ERROR = 3
_EXTENSION_STATE_OUT_OF_DATE = 4


_UNREACHABLE = object()  # Shell D-Bus failure marker (e.g. sandbox denial)


def extension_raw_state():
    """Raw Shell state int for our extension, None, or _UNREACHABLE.

    None when the Shell answers but does not know the extension (not yet
    discovered: installed after the Shell started, which on Wayland needs
    a logout/login to pick up; or unparseable reply).
    _UNREACHABLE when the Shell cannot be reached at all (sandbox).
    """
    try:
        import pydbus

        proxy = pydbus.SessionBus().get("org.gnome.Shell.Extensions", "/org/gnome/Shell/Extensions")
        info = proxy.GetExtensionInfo(EXTENSION_UUID)
    except Exception as e:  # noqa: BLE001 - sandbox denial: unknown, not off
        logger.debug(f"[ShellIntegration] Shell state unreachable: {e}")
        return _UNREACHABLE
    try:
        state = dict(info).get("state")
    except (TypeError, ValueError):
        return None
    return int(state) if state is not None else None


def extension_load_state():
    """Runtime state of our extension: True / False / None.

    True only when the Shell reports state ENABLED (loaded and running).
    False when the Shell answers but the extension is not enabled-state
    (disabled, error, or unknown to this Shell session — e.g. installed
    and listed as enabled, but the Shell has not loaded it yet, which on
    Wayland requires a logout/login).
    None when the Shell cannot be reached at all (sandboxed D-Bus denial):
    callers must fall back to the GSettings enabled list instead.
    """
    state = extension_raw_state()
    if state is _UNREACHABLE:
        return None
    return state == _EXTENSION_STATE_ENABLED


def _shell_settings():
    """Gio.Settings for ``org.gnome.shell``, or None when unavailable.

    ``Gio.Settings.new()`` aborts the whole process (GLib fatal error,
    not a catchable Python exception) when the schema is not installed —
    e.g. inside the Flatpak runtime, which does not ship
    ``org.gnome.shell``. Always probe with
    ``SettingsSchemaSource.lookup()`` first; it fails safely.
    """
    try:
        from gi.repository import Gio

        source = Gio.SettingsSchemaSource.get_default()
        if source is None or source.lookup("org.gnome.shell", True) is None:
            return None
        return Gio.Settings.new("org.gnome.shell")
    except Exception:  # noqa: BLE001 - sandbox without schemas
        return None


def extension_is_enabled():
    """True when the user enabled our extension in GNOME Shell."""
    settings = _shell_settings()
    if settings is None:
        return False
    try:
        enabled = settings.get_strv("enabled-extensions")
    except Exception as e:  # noqa: BLE001 - sandbox denial: inactive
        logger.debug(f"[ShellIntegration] enabled-extensions unreadable: {e}")
        return False
    return EXTENSION_UUID in (enabled or [])


_UNSET = object()


def integration_active(shell_version=None, search_dirs=None, enabled=None, loaded=_UNSET):
    """True when the Shell-side wallpaper layer is managing our windows.

    Requires GNOME + Wayland + a compatible extension that the Shell
    confirms as loaded. An extension merely listed as enabled (e.g. before
    the Wayland logout/login that loads it) does NOT count: standing the
    legacy guard down with nothing managing stacking would be worse.
    When the Shell cannot be reached (sandbox), fall back to the enabled
    list as a best effort; everything else falls back to legacy behavior.
    """
    if not (is_gnome() and is_wayland()):
        return False
    if not extension_is_compatible(shell_version, search_dirs):
        return False
    state = extension_load_state() if loaded is _UNSET else loaded
    if state is True:
        return True
    if state is False:
        return False
    is_on = extension_is_enabled() if enabled is None else enabled
    return bool(is_on)


def integration_status(search_dirs=None):
    """Machine-readable integration state for setup UI (never raises).

    Returns ``(state, detail)`` where state is one of: ``active``,
    ``disabled``, ``needs_logout``, ``not_discovered``, ``not_installed``,
    ``incompatible``, ``error``, ``unknown`` (Shell unreachable: sandbox).
    ``detail`` carries paths/versions for user-facing messages.
    """
    detail = {}
    try:
        if not (is_gnome() and is_wayland()):
            return "unknown", detail
        path = extension_dir(search_dirs)
        if path is None:
            return "not_installed", detail
        detail["path"] = path
        ver = shell_version()
        detail["shell"] = ver
        meta = read_metadata(search_dirs)
        supported = (meta or {}).get("shell-version") or []
        detail["supported"] = list(supported)
        if not extension_is_compatible(ver, search_dirs):
            return "incompatible", detail
        raw = extension_raw_state()
        if raw is _UNREACHABLE:
            return "unknown", detail
        if raw == _EXTENSION_STATE_ENABLED:
            return "active", detail
        if raw == _EXTENSION_STATE_ERROR:
            return "error", detail
        if raw == _EXTENSION_STATE_OUT_OF_DATE:
            return "incompatible", detail
        if extension_is_enabled():
            # Enabled but the Shell does not report it loaded: logout/login
            # pending (or a load error without a state yet).
            return "needs_logout", detail
        if raw is None:
            # Installed, compatible, known-disabled... or known at all?
            # Unknown-to-Shell + not enabled = installed after Shell start.
            return "not_discovered", detail
        return "disabled", detail
    except Exception as e:  # noqa: BLE001 - status must never break the GUI
        logger.debug(f"[ShellIntegration] status check failed: {e}")
        return "unknown", {}


def log_status():
    """One-line journal entry describing the integration state."""
    if not (is_gnome() and is_wayland()):
        return
    path = extension_dir()
    if path is None:
        logger.info(
            "[ShellIntegration] extension not installed: "
            "GNOME Wayland icons use legacy stacking (see README)"
        )
        return
    ver = shell_version()
    if not extension_is_compatible(ver):
        logger.info(
            f"[ShellIntegration] extension installed but not compatible "
            f"with Shell {ver}: legacy stacking in use"
        )
        return
    if not extension_is_enabled():
        logger.info(
            f"[ShellIntegration] installed at {path} but disabled: run "
            f"'gnome-extensions enable {EXTENSION_UUID}' then log out/in"
        )
        return
    if extension_load_state() is False:
        logger.info(
            "[ShellIntegration] enabled but not loaded by the Shell: "
            "log out and back in, then check "
            f"'journalctl --user -g {EXTENSION_UUID}' for errors"
        )
        return
    logger.info("[ShellIntegration] active: wallpaper hosted in Shell background layer")
