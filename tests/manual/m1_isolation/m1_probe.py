# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Import/path/child-process probes only. No server/player/GUI is instantiated."""

import copy
import ctypes
import errno
import importlib
import json
import multiprocessing as mp
import os
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

SETTINGS = {
    "org.gnome.desktop.background": (
        "picture-uri",
        "picture-uri-dark",
        "picture-options",
        "primary-color",
        "secondary-color",
        "color-shading-type",
    ),
    "org.gnome.desktop.screensaver": ("picture-uri", "lock-enabled"),
    "org.gnome.shell": ("enabled-extensions", "disable-user-extensions"),
    "org.gnome.desktop.interface": ("color-scheme",),
}


def host_settings():
    values = {}
    for schema, keys in SETTINGS.items():
        available = subprocess.check_output(
            ["gsettings", "list-keys", schema], text=True
        ).splitlines()
        for key in keys:
            values[f"{schema}:{key}"] = (
                subprocess.check_output(["gsettings", "get", schema, key], text=True).strip()
                if key in available
                else "<schema key unavailable>"
            )
    return values


def settings():
    from gi.repository import Gio

    values = {}
    for schema, keys in SETTINGS.items():
        obj = Gio.Settings.new(schema)
        for key in keys:
            values[f"{schema}:{key}"] = (
                obj.get_value(key).print_(True)
                if obj.props.settings_schema.has_key(key)
                else "<schema key unavailable>"
            )
    return values


def blocked_write_checks(label):
    import m1_bootstrap as shim

    sentinel = shim.ROOT / "control/sentinel"
    alias = shim.PROFILE / "t" / f"escape-{os.getpid()}-{label}"
    alias.symlink_to(sentinel)
    results = {}
    for name, target in (("python", sentinel), ("symlink", alias)):
        try:
            target.write_bytes(b"must-not-write")
        except PermissionError:
            results[name] = "DENIED"
        else:
            raise AssertionError(f"Outside write succeeded: {name}")
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.open(os.fsencode(sentinel), os.O_WRONLY | os.O_TRUNC)
    if fd >= 0:
        os.close(fd)
        raise AssertionError("Native open/truncate escaped filesystem guard")
    assert ctypes.get_errno() in (errno.EACCES, errno.EPERM)
    results["native_open_truncate"] = "DENIED"
    # Native exec inherits Landlock even without Python/sitecustomize hooks.
    child = subprocess.run(
        ["/bin/sh", "-c", 'printf forbidden > "$1"', "m1-write-probe", str(sentinel)],
        capture_output=True,
    )
    assert child.returncode != 0
    results["non_python_exec"] = "DENIED"
    try:
        os.link(sentinel, alias.with_name(alias.name + "-hardlink"))
    except OSError as error:
        assert error.errno in (errno.EACCES, errno.EPERM, errno.EXDEV)
        results["hardlink_escape"] = "DENIED"
    else:
        raise AssertionError("Cross-boundary hardlink unexpectedly succeeded")
    alias.unlink()
    return results


def emit(role, label):
    import m1_bootstrap as shim

    modules = {
        "server": "apricus.server",
        "video": "apricus.player.video_player",
        "web": "apricus.player.web_player",
        "gui": "apricus.gui.gtk4app",
    }
    if role in modules:
        module = importlib.import_module(modules[role])
        if role == "server":
            assert "gi.repository.Gtk" not in sys.modules, "server adapter pinned GTK"
    else:
        module = None
    from gi.repository import Gio, GLib

    from apricus import commons, gnome_shell, mediacache, snap_ipc, stop_support, utils, yt_utils

    integration_state, _detail = gnome_shell.integration_status()
    assert integration_state == "active", integration_state
    expected = json.loads(os.environ["APRICUS_M1_HOST_ENV"])
    assert {key: os.environ.get(key) for key in shim.PROTECTED} == expected
    assert Path(GLib.get_user_cache_dir()) == shim.PROFILE / "cache"
    assert Path(GLib.get_user_data_dir()) == shim.PROFILE / "data"
    host_config = expected["XDG_CONFIG_HOME"] or str(Path(expected["HOME"]) / ".config")
    assert GLib.get_user_config_dir() == host_config
    aliases = {}
    for name, loaded in list(sys.modules.items()):
        if not name.startswith("apricus") or loaded is None:
            continue
        for key, expected_path in shim.PATHS.items():
            if hasattr(loaded, key):
                actual = getattr(loaded, key)
                assert actual == expected_path, (name, key, actual)
                aliases[f"{name}.{key}"] = actual
    paths = dict(shim.PATHS)
    paths.update(
        meta_cache=yt_utils._meta_cache_path(),
        media_cache=mediacache._cache_file(),
        static_cache=stop_support.static_cache_path("/read-only-fixture.mp4"),
        exports=gnome_shell.host_export_dir(),
        systemd=utils._systemd_user_dir(),
        ipc=snap_ipc._sock_path(commons.DBUS_NAME_SERVER),
        temp=tempfile.gettempdir(),
    )
    for path in paths.values():
        assert Path(path).resolve().is_relative_to(shim.PROFILE), path
    config = copy.deepcopy(commons.CONFIG_TEMPLATE)
    # Writing ConfigUtil exercises .tmp/.bak/.lock paths, no runtime object.
    utils.ConfigUtil().save(config)
    assert not Path(commons.LEGACY_CONFIG_PATH).exists()
    # Exercise known writes through actual application helpers, not just path
    # strings. No extraction, portal, desktop launcher or playback is invoked.
    utils.setup_autostart(True)
    assert Path(commons.AUTOSTART_DESKTOP_PATH).is_file()
    utils.setup_autostart(False)
    assert not Path(commons.AUTOSTART_DESKTOP_PATH).exists()
    yt_utils.save_cached_meta(
        "https://example.invalid/preflight",
        {
            "formats": [{"url": "https://example.invalid/fixture.mp4", "ext": "mp4"}],
        },
    )
    assert Path(yt_utils._meta_cache_path()).is_file()
    assert Path(stop_support.stopped_background(config)).is_relative_to(shim.PROFILE)
    with yt_utils.youtube_dl.YoutubeDL(yt_utils.base_options(quiet=True)) as ydl:
        paths["yt_dlp_native_cache"] = ydl.cache._get_root_dir()
        assert Path(paths["yt_dlp_native_cache"]).is_relative_to(shim.PROFILE)
    marker = shim.PROFILE / "t" / f"worker-{os.getpid()}-{label}.txt"
    marker.write_text("isolated worker write\n")
    with tempfile.NamedTemporaryFile() as temporary:
        assert Path(temporary.name).is_relative_to(shim.PROFILE)
    denials = blocked_write_checks(label)
    for operation in (
        lambda: Gio.Settings.new("org.gnome.desktop.background").set_string("picture-uri", ""),
        lambda: utils.setup_systemd_autostart(True),
        lambda: utils.gnome_extension_set_disable("ding@rastersoft.com"),
        lambda: utils.gnome_desktop_icon_workaround(),
        lambda: subprocess.run(
            ["gsettings", "set", "org.gnome.desktop.background", "picture-uri", "''"]
        ),
    ):
        try:
            operation()
        except PermissionError:
            pass
        else:
            raise AssertionError("Session mutation was not blocked")
    extras = {}
    if role == "gui":
        from apricus.gui import gui_utils
        from apricus.stream_thumbs import ThumbCache

        pixbuf = gui_utils.GdkPixbuf.Pixbuf.new(gui_utils.GdkPixbuf.Colorspace.RGB, False, 8, 2, 2)
        pixbuf.fill(0x222222FF)
        fixture = shim.PROFILE / "media" / f"thumbnail-{os.getpid()}.png"
        pixbuf.savev(str(fixture), "png", [], [])
        factory = gui_utils._get_thumb_factory()
        uri = fixture.as_uri()
        mtime = int(fixture.stat().st_mtime)
        factory.save_thumbnail(pixbuf, uri, mtime)
        thumbnail = factory.lookup(uri, mtime)
        assert thumbnail and Path(thumbnail).resolve().is_relative_to(shim.PROFILE / "cache")
        extras["native_thumbnail"] = thumbnail
        # Favicon path is pure; no network request or Gtk widget is created.
        extras["favicon"] = module.ControlPanel._favicon_path("example.invalid")
        stream_cache = ThumbCache(str(shim.PROFILE / "cache/apricus/stream-thumbs"))
        stream_cache._save_index({})
        extras["stream_index"] = stream_cache._index_path()
        assert Path(extras["stream_index"]).is_file()
        for path in extras.values():
            assert Path(path).is_relative_to(shim.PROFILE)
    if role == "web":
        manager = module._m1_test_data_manager
        extras["webkit_data"] = manager.get_base_data_directory()
        extras["webkit_cache"] = manager.get_base_cache_directory()
        for path in extras.values():
            assert Path(path).is_relative_to(shim.PROFILE)
    if role == "video":
        extras["vlc_config"] = module._m1_test_vlc_config
        assert isinstance(module.vlc.Instance, type)
        original = module._m1_original_vlc_options(False, None)
        assert module._vlc_options(False, None) == [*original, f"--config={extras['vlc_config']}"]
        assert "--no-disable-screensaver" in original
        assert Path(extras["vlc_config"]).is_relative_to(shim.PROFILE)
        binary = shutil.which("vlc")
        if binary is None:
            raise RuntimeError("Cannot verify the public VLC --config option without its CLI help")
        help_result = subprocess.run(
            [binary, f"--config={extras['vlc_config']}", "--longhelp", "--advanced"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        assert help_result.returncode == 0 and "--config" in help_result.stdout
        extras["vlc_public_config_option_verified"] = True
        extras["vlc_version"] = module.vlc.libvlc_get_version().decode()
    if module is not None:
        constructor = {
            "server": "ApricusServer",
            "video": "VideoPlayer",
            "web": "WebPlayer",
            "gui": "ControlPanel",
        }[role]
        assert getattr(module, constructor).__init__ is shim.denied
        try:
            getattr(module, constructor).__init__(None)
        except PermissionError:
            pass
        else:
            raise AssertionError("Application startup guard failed")
    result = dict(
        role=role,
        label=label,
        pid=os.getpid(),
        ppid=os.getppid(),
        executable=sys.executable,
        prefix=sys.prefix,
        source=commons.__file__,
        landlock_abi=shim.ABI,
        bootstrap_origin_pid=shim.BOOT_PID,
        protected_environment=expected,
        settings=settings(),
        paths=paths,
        aliases=aliases,
        denials=denials,
        extras=extras,
        no_application_constructed=True,
        host_extension_state=integration_state,
    )
    dest = shim.EVIDENCE / f"{os.getpid()}-{label}.json"
    dest.write_text(json.dumps(result, indent=2))
    return result


def controller():
    # Importable top-level target: multiprocessing uses the actual spawn and
    # forkserver executable paths used by Apricus, not emulated env dicts.
    emit("server", "controller")
    for method in ("spawn", "forkserver"):
        for role in ("server", "video", "web", "gui"):
            proc = mp.get_context(method).Process(target=emit, args=(role, f"{method}-{role}"))
            proc.start()
            proc.join(45)
            if proc.is_alive():
                proc.terminate()
                proc.join(5)
                raise RuntimeError(f"Probe timeout: {method}/{role}")
            if proc.exitcode:
                raise RuntimeError(f"Probe failed: {method}/{role}, exit {proc.exitcode}")
    with ThreadPoolExecutor(max_workers=1) as pool:
        pool.submit(emit, "worker", "thread-worker").result(timeout=30)
    subprocess.run(
        [sys.executable, "-c", 'from m1_probe import emit; emit("worker", "exec-worker")'],
        check=True,
        timeout=45,
    )


if __name__ == "__main__":
    controller()
