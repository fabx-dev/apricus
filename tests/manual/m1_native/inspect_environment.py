"""Inspect source/dependency paths with Python -S; never start Apricus.

Ubuntu's system sitecustomize installs Apport. -S avoids even that hook; only
import paths for the independent venv, source and system GI are added here.
There are no environment, filesystem-policy or application-behavior overrides.
"""

import importlib.util
import json
import os
import sys
import sysconfig
from pathlib import Path

if not sys.flags.no_site:
    raise SystemExit("Use the dedicated .venv/bin/python -S to avoid all sitecustomize hooks")

WORKTREE = Path(__file__).resolve().parents[3]
if Path(sys.prefix).resolve() != WORKTREE / ".venv":
    raise SystemExit("Only this worktree's independent venv is supported")

sys.path[:0] = [
    str(WORKTREE / "src"),
    sysconfig.get_path("purelib"),
    "/usr/lib/python3/dist-packages",
]

from apricus import commons

assert "sitecustomize" not in sys.modules
assert not any("landlock" in name or name == "m1_bootstrap" for name in sys.modules)
print(
    json.dumps(
        dict(
            executable=sys.executable,
            prefix=sys.prefix,
            source=commons.__file__,
            config=commons.CONFIG_PATH,
            legacy=commons.LEGACY_CONFIG_PATH,
            no_sitecustomize=True,
            no_landlock=True,
            no_application_started=True,
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
            dependencies={
                name: importlib.util.find_spec(name) is not None
                for name in ("gi", "vlc", "pydbus", "yt_dlp", "requests", "setproctitle")
            },
        ),
        indent=2,
    )
)
