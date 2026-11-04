"""Plain source entrypoint: dependency paths only, no hooks or overrides."""

import json
import multiprocessing as mp
import os
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# Test-only override so the M2 tree (no local venv, no installs allowed) can
# run with an existing dependency venv. Must be an explicit absolute path.
# NOTE: compare lexically (no resolve() on the binary itself): venv launchers
# are symlinks to the base interpreter, and resolving them would point at
# /usr/bin instead of the venv.
_TEST_PYTHON = os.environ.get("APRICUS_TEST_PYTHON")
if _TEST_PYTHON:
    _TEST_VENV = os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(_TEST_PYTHON)), "..")
    )
else:
    _TEST_VENV = str(ROOT / ".venv")
if not sys.flags.no_site or os.path.normpath(sys.prefix) != _TEST_VENV:
    raise RuntimeError(
        "Use -S with APRICUS_TEST_PYTHON=<venv>/bin/python (or this worktree's .venv)"
    )
sys.path[:0] = [str(ROOT / "src"), sysconfig.get_path("purelib"), "/usr/lib/python3/dist-packages"]


def probe(label):
    from gi.repository import Gio

    from apricus import commons

    assert "sitecustomize" not in sys.modules
    assert not any("landlock" in name or name == "m1_bootstrap" for name in sys.modules)
    result = dict(
        label=label,
        pid=os.getpid(),
        prefix=sys.prefix,
        source=commons.__file__,
        config=commons.CONFIG_PATH,
        sitecustomize=False,
        landlock=False,
        background=Gio.Settings.new("org.gnome.desktop.background").get_string("picture-uri"),
        environment={
            key: os.environ.get(key)
            for key in (
                "HOME",
                "XDG_CONFIG_HOME",
                "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS",
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "DCONF_PROFILE",
            )
        },
    )
    print(json.dumps(result), flush=True)


def main():
    if sys.argv[1:] == ["--probe"]:
        probe("parent")
        for method in ("spawn", "forkserver"):
            process = mp.get_context(method).Process(target=probe, args=(method,))
            process.start()
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join()
                raise RuntimeError(f"{method} probe timeout")
            if process.exitcode:
                raise RuntimeError(f"{method} probe failed")
    elif sys.argv[1:] == ["--run"]:
        from apricus.__main__ import main as app_main

        sys.argv = ["apricus", "-b", "-d"]
        app_main()
    else:
        raise SystemExit("--probe or separately authorized --run")


if __name__ == "__main__":
    main()
