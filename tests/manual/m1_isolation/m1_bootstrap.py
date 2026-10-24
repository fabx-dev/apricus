# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preflight-only adapter; never edits production files or constructs a player.

Loaded before Apricus in every fresh Python interpreter via sitecustomize;
forked workers inherit it. This is test instrumentation, NOT a production API
or a general-purpose security sandbox. Runtime launching is intentionally absent.
"""

import copy
import fcntl
import importlib.abc
import importlib.machinery
import json
import os
import stat
import sys
import tempfile
from pathlib import Path

ROOT = None
PROFILE = None
EVIDENCE = None
ABI = None
BOOT_PID = None
PROTECTED = (
    "HOME",
    "XDG_CONFIG_HOME",
    "XDG_RUNTIME_DIR",
    "DBUS_SESSION_BUS_ADDRESS",
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "XAUTHORITY",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
    "GSETTINGS_BACKEND",
    "DCONF_PROFILE",
    "GSETTINGS_SCHEMA_DIR",
    "GDK_BACKEND",
)
PATHS = {}


def denied(*_args, **_kwargs):
    raise PermissionError("M1 preflight: session mutation/application startup is not authorized")


def inside(path):
    resolved = Path(os.fsdecode(path)).resolve()
    return any(resolved.is_relative_to(base) for base in (PROFILE, EVIDENCE))


def audit(event, args):
    if event in ("os.chmod", "os.chown", "os.utime", "os.setxattr", "os.removexattr"):
        path = args[0]
        if isinstance(path, int):
            path = os.readlink(f"/proc/self/fd/{path}")
        if not inside(path):
            raise PermissionError(f"M1 preflight blocked metadata mutation: {event}")
    if event == "subprocess.Popen":
        command = args[1]
        if isinstance(command, (str, bytes)):
            raise PermissionError("M1 preflight rejects shell command strings")
        name = Path(os.fsdecode(command[0])).name
        verbs = [os.fsdecode(value) for value in command[1:]]
        if name in {"systemctl", "snap", "flatpak", "flatpak-spawn", "xdg-open"}:
            raise PermissionError(f"M1 preflight rejects external action: {name}")
        if name in {"gnome-extensions", "gsettings", "dconf"}:
            safe = {"info", "list", "get", "list-keys", "range", "list-recursively", "read", "dump"}
            if not verbs or verbs[0] not in safe:
                raise PermissionError(f"M1 preflight rejects mutating command: {name}")


def patch_loaded(name, module):
    # Imports only; no app instance is made by these patches.
    classes = {
        "apricus.server": "ApricusServer",
        "apricus.player.base_player": "BasePlayer",
        "apricus.player.video_player": "VideoPlayer",
        "apricus.player.web_player": "WebPlayer",
        "apricus.gui.gtk4app": "ControlPanel",
    }
    if name in classes and os.environ.get("APRICUS_M1_PHASE") == "preflight":
        getattr(module, classes[name]).__init__ = denied
    if name == "apricus.gui.gtk4app":
        # Native/portal choosers can persist their own host preferences over
        # D-Bus. They are not part of M1 validation; preseed test media instead.
        module.ControlPanel.on_choose_video_dir = denied
        module.ControlPanel.on_webpage_choose_file = denied
    if name == "apricus.player.video_player":
        original_options = module._vlc_options
        config = str(PROFILE / "config/vlc/vlcrc")

        def private_options(disable_audio, vout):
            return [*original_options(disable_audio, vout), f"--config={config}"]

        # Keep the actual vlc.Instance class and python-vlc type conversions.
        # Adapt only this application's options, via a public VLC config flag.
        module._vlc_options = private_options
        module._m1_original_vlc_options = original_options
        module._m1_test_vlc_config = config
    if name == "apricus.player.web_player":
        webkit = module.WebKit2
        manager = webkit.WebsiteDataManager(
            base_data_directory=str(PROFILE / "data/webkit"),
            base_cache_directory=str(PROFILE / "cache/webkit"),
        )
        module._m1_test_data_manager = manager

        def new_with_settings(settings):
            # Defined for a later reviewed live launcher, but constructors are
            # blocked in this preflight. No default persistent WebKit context.
            context = webkit.WebContext.new_with_website_data_manager(manager)
            return webkit.WebView(web_context=context, settings=settings)

        webkit.WebView.new_with_settings = new_with_settings


class PatchLoader(importlib.abc.Loader):
    def __init__(self, delegate, name):
        self.delegate, self.name = delegate, name

    def create_module(self, spec):
        return self.delegate.create_module(spec)

    def exec_module(self, module):
        self.delegate.exec_module(module)
        patch_loaded(self.name, module)


class PatchFinder(importlib.abc.MetaPathFinder):
    targets = {
        "apricus.server",
        "apricus.player.base_player",
        "apricus.player.video_player",
        "apricus.player.web_player",
        "apricus.gui.gtk4app",
    }

    def find_spec(self, fullname, path=None, target=None):
        if fullname not in self.targets:
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is None or spec.loader is None:
            raise ImportError(fullname)
        spec.loader = PatchLoader(spec.loader, fullname)
        return spec


def activate():
    global ROOT, PROFILE, EVIDENCE, ABI, PATHS, BOOT_PID
    if ROOT is not None:
        return
    phase = os.environ.get("APRICUS_M1_PHASE")
    if phase not in ("preflight", "live"):
        raise RuntimeError("Only preflight or separately authorized live validation is allowed")
    worktree = Path(__file__).resolve().parents[3]
    if Path(sys.prefix).resolve() != worktree / ".venv":
        raise RuntimeError("Every Python child must use the independent M1 virtual environment")
    root = Path(os.environ["APRICUS_M1_PREFLIGHT_ROOT"])
    if (
        root != root.resolve()
        or root.parent != worktree / "build"
        or not root.name.startswith("m1p-")
    ):
        raise RuntimeError(
            "Root must be a fresh canonical build/m1p-* directory in the M1 worktree"
        )
    if root.stat().st_uid != os.getuid() or stat.S_IMODE(root.stat().st_mode) != 0o700:
        raise RuntimeError("Private root must belong to this user and have mode 0700")
    if not (root / "marker.json").is_file():
        raise RuntimeError("Missing preflight root marker")
    if phase == "live":
        approval = json.loads((root / "live-authorization.json").read_text())
        if approval.get("approved") is not True or approval.get("backup_verified") is not True:
            raise RuntimeError("Live validation requires explicit approval and verified backup")
        if not (root / "backup/manifest.json").is_file() or not (root / "restore.py").is_file():
            raise RuntimeError("Independent restore procedure missing")
    expected = json.loads(os.environ["APRICUS_M1_HOST_ENV"])
    if {name: os.environ.get(name) for name in PROTECTED} != expected:
        raise RuntimeError("Host session/config/display environment changed")
    if os.environ.get("SNAP") or os.environ.get("FLATPAK_ID") or Path("/.flatpak-info").exists():
        raise RuntimeError("Native source preflight only, not a sandbox bypass")
    if any(name.startswith("apricus") for name in sys.modules):
        raise RuntimeError("Apricus was imported before isolation")
    if "gi.repository.GLib" in sys.modules:
        raise RuntimeError("GLib was loaded before cache/data isolation")
    ROOT, PROFILE, EVIDENCE = root, root / "p", root / "evidence"
    os.umask(0o077)
    for base in (PROFILE, EVIDENCE):
        if not base.is_dir() or base.is_symlink():
            raise RuntimeError("Private directories missing or symlinked")
    if Path(os.environ["XDG_CACHE_HOME"]).resolve() != PROFILE / "cache":
        raise RuntimeError("Cache environment not isolated")
    if Path(os.environ["XDG_DATA_HOME"]).resolve() != PROFILE / "data":
        raise RuntimeError("Data environment not isolated")
    tempfile.tempdir = str(PROFILE / "t")
    sys.dont_write_bytecode = True
    # Reject inherited regular write FDs; the guard cannot revoke an existing FD.
    for entry in Path("/proc/self/fd").iterdir():
        fd = int(entry.name)
        try:
            if stat.S_ISREG(os.fstat(fd).st_mode) and fcntl.fcntl(fd, fcntl.F_GETFL) & os.O_ACCMODE:
                if not inside(entry.resolve()):
                    raise RuntimeError(f"Unexpected inherited write FD {fd}")
        except OSError:
            pass
    from landlock_guard import restrict_writes

    dconf = json.loads(os.environ["APRICUS_M1_DCONF_IDENTITY"])
    ABI = restrict_writes((PROFILE, EVIDENCE), dconf=dconf)
    sys.addaudithook(audit)
    from apricus import commons

    if Path(commons.__file__).resolve() != worktree / "src/apricus/commons.py":
        raise RuntimeError("Wrong Apricus source imported")
    PATHS = {
        "CONFIG_DIR": str(PROFILE / "config/apricus"),
        "CONFIG_PATH": str(PROFILE / "config/apricus/config.json"),
        "LEGACY_CONFIG_PATH": str(PROFILE / "legacy/hidamari/config.json"),
        "VIDEO_WALLPAPER_DIR": str(PROFILE / "media"),
        "AUTOSTART_DIR": str(PROFILE / "autostart"),
        "AUTOSTART_DESKTOP_PATH": str(PROFILE / "autostart" / f"{commons.PROJECT}.desktop"),
    }
    for name, value in PATHS.items():
        setattr(commons, name, value)
    commons.CONFIG_TEMPLATE = copy.deepcopy(commons.CONFIG_TEMPLATE)
    commons.CONFIG_TEMPLATE.update(
        data_source={"Default": ""},
        playlist={"Default": []},
        video_dir=PATHS["VIDEO_WALLPAPER_DIR"],
        is_first_time=False,
    )
    from apricus import gnome_shell, snap_ipc, utils

    # Imported aliases in all later modules receive these private paths/guards.
    utils._systemd_user_dir = lambda: str(PROFILE / "systemd/user")
    utils.setup_systemd_autostart = denied
    utils.gnome_extension_set_enable = denied
    utils.gnome_extension_set_disable = denied
    utils.gnome_desktop_icon_workaround = denied
    gnome_shell.host_export_dir = lambda: str(PROFILE / "exports")
    gnome_shell._bundled_helpers_dirs = lambda search_roots=None: [
        str(worktree / "gnome-extension")
    ]
    snap_ipc._sock_dir = lambda: str(PROFILE / "ipc")
    from gi.repository import Gio

    original_set_string = Gio.Settings.set_string
    original_set_value = Gio.Settings.set_value
    # Reads stay real. Only the three reviewed wallpaper keys may change live.
    for method in dir(Gio.Settings):
        if method.startswith("set_") or method in {"reset", "apply", "bind", "bind_with_mapping"}:
            setattr(Gio.Settings, method, denied)
    if phase == "live":
        allowed = {
            "org.gnome.desktop.background": {"picture-uri", "picture-uri-dark"},
            "org.gnome.desktop.screensaver": {"picture-uri"},
        }

        def allow_key(settings, key):
            if key not in allowed.get(settings.props.settings_schema.get_id(), set()):
                denied()

        def set_string(settings, key, value):
            allow_key(settings, key)
            return original_set_string(settings, key, value)

        def set_value(settings, key, value):
            allow_key(settings, key)
            if value.get_type_string() != "s":
                denied()
            return original_set_value(settings, key, value)

        Gio.Settings.set_string = set_string
        Gio.Settings.set_value = set_value
    for method in ("launch_default_for_uri", "launch_default_for_uri_async"):
        setattr(Gio.AppInfo, method, denied)
    sys.meta_path.insert(0, PatchFinder())
    BOOT_PID = os.getpid()
    directory = EVIDENCE / "bootstrap"
    directory.mkdir(exist_ok=True)
    (directory / f"{BOOT_PID}.json").write_text(
        json.dumps(
            dict(
                pid=BOOT_PID,
                ppid=os.getppid(),
                landlock_abi=ABI,
                paths=PATHS,
                start=Path("/proc/self/stat").read_text().rsplit(")", 1)[1].split()[19],
                executable=sys.executable,
                prefix=sys.prefix,
                protected_environment=expected,
                source=commons.__file__,
                stdio={str(fd): os.readlink(f"/proc/self/fd/{fd}") for fd in (0, 1, 2)},
            ),
            indent=2,
        )
    )
