# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only GSettings API probes; trace native dconf initialization writes.

Opt-in --allow-marker-write uses only the approved existing-inode WRITE_FILE
rule. Never launches Apricus or modifies GSettings values.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from landlock_guard import check_dconf, dconf_identity
from launcher import WORKTREE, app_bus_owners, snap_processes, source_hashes, stable_files
from m1_bootstrap import PROTECTED
from m1_probe import host_settings


def snapshot():
    return dict(
        environment={key: os.environ.get(key) for key in PROTECTED},
        settings=host_settings(),
        stable_files=stable_files(),
        snap_processes=snap_processes(),
        bus_owners=app_bus_owners(),
        sources=source_hashes(),
    )


def main():
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix="m1-dconf-", dir=WORKTREE / "build"))
    before = snapshot()
    allow = sys.argv[1:] == ["--allow-marker-write"]
    if sys.argv[1:] and not allow:
        raise SystemExit("Only optional --allow-marker-write is supported")
    identity = dconf_identity()
    runtime = Path(os.environ["XDG_RUNTIME_DIR"]) / "dconf/user"
    paths = {}
    for path in (runtime, Path.home() / ".config/dconf/user"):
        info = path.stat()
        paths[str(path)] = dict(
            realpath=str(path.resolve()),
            inode=info.st_ino,
            device=info.st_dev,
            size=info.st_size,
            symlink=path.is_symlink(),
            parent_symlink=path.parent.is_symlink(),
        )
    probe = """from gi.repository import Gio,GLib
s=Gio.Settings.new("org.gnome.desktop.background")
s.connect("changed",lambda *args:None)
print(s.get_string("picture-uri"))
loop=GLib.MainLoop()
GLib.timeout_add(200,lambda:loop.quit())
loop.run()
"""
    traces = {}
    for label in ("host", "guarded"):
        prefix = ""
        if label == "guarded":
            exception = f", dconf={identity!r}" if allow else ""
            prefix = (
                f"import sys; sys.path.insert(0,{str(Path(__file__).parent.resolve())!r}); "
                f"from landlock_guard import restrict_writes,check_dconf; "
                f"restrict_writes(({str(root)!r},){exception});\n"
            )
        result = subprocess.run(
            [
                "strace",
                "-f",
                "-yy",
                "-s",
                "256",
                "-o",
                str(root / f"{label}.trace"),
                "-e",
                "trace=openat,mmap,munmap,pwrite64,write,ftruncate,fallocate,mkdir,mkdirat,readlink,newfstatat,close",
                "/usr/bin/python3",
                "-c",
                prefix + probe,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        (root / f"{label}.stdout").write_text(result.stdout)
        (root / f"{label}.stderr").write_text(result.stderr)
        traces[label] = dict(
            returncode=result.returncode, stdout=result.stdout, stderr=result.stderr
        )
        check_dconf(identity)
    after = snapshot()
    report = dict(
        paths=paths,
        before=before,
        after=after,
        host_state_unchanged=before == after,
        traces=traces,
        dconf_write_exception_granted=allow,
        dconf_identity=identity,
        no_apricus_launched=True,
    )
    (root / "report.json").write_text(json.dumps(report, indent=2))
    print(root)
    print("Host/Snap/settings/protected files unchanged:", before == after)
    for label in traces:
        for line in (root / f"{label}.trace").read_text().splitlines():
            if str(runtime) in line:
                print(label, line)


if __name__ == "__main__":
    main()
