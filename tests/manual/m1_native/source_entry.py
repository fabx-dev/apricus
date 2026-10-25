"""Plain source entrypoint: dependency paths only, no hooks or overrides."""

import json
import multiprocessing as mp
import os
import sys
import sysconfig
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if not sys.flags.no_site or Path(sys.prefix).resolve() != ROOT / ".venv":
    raise RuntimeError("Use this worktree's .venv/bin/python -S")
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
