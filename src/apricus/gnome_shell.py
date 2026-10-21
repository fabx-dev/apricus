# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""GNOME Shell wallpaper integration (Wayland/DING support).

On GNOME Wayland, ordinary X11/XWayland restacking cannot place our video
windows below DING's native Wayland desktop window. The companion extension
``apricus-integration@fabx-dev`` (shipped under ``gnome-extension/``) adopts
our wallpaper windows into the Shell's background layer instead.

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


def extension_is_enabled():
    """True when the user enabled our extension in GNOME Shell."""
    try:
        from gi.repository import Gio

        settings = Gio.Settings.new("org.gnome.shell")
        enabled = settings.get_strv("enabled-extensions")
    except Exception as e:  # noqa: BLE001 - missing schema/sandbox: inactive
        logger.debug(f"[ShellIntegration] enabled-extensions unreadable: {e}")
        return False
    return EXTENSION_UUID in (enabled or [])


def integration_active(shell_version=None, search_dirs=None, enabled=None):
    """True when the Shell-side wallpaper layer is managing our windows.

    Requires GNOME + Wayland + a compatible, enabled extension. Everything
    else (Xorg, other desktops, sandboxed dconf that hides the setting)
    falls back to the legacy in-app stacking behavior.
    """
    if not (is_gnome() and is_wayland()):
        return False
    if not extension_is_compatible(shell_version, search_dirs):
        return False
    is_on = extension_is_enabled() if enabled is None else enabled
    return bool(is_on)


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
    logger.info("[ShellIntegration] active: wallpaper hosted in Shell background layer")
