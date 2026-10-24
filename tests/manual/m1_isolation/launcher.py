#!/usr/bin/env python3
# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prepare/probe M1 paths on real GNOME. There is NO runtime launch subcommand."""

import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from m1_bootstrap import PROTECTED
from m1_probe import host_settings

WORKTREE = Path(__file__).resolve().parents[3]
ADAPTER = Path(__file__).resolve().parent


def native_dconf_clean(log):
    """Do not accept matching cached values when native initialization failed."""
    return "dconf-CRITICAL" not in log and "dconf will not work properly" not in log


def stable_files():
    home = Path.home()
    roots = [
        home / ".config/apricus",
        home / ".config/hidamari",
        home / ".cache/apricus",
        home / "snap/apricus/current/.config/apricus",
        home / "snap/apricus/current/.cache/apricus",
        home / "snap/apricus/common/.cache/apricus",
        home / ".var/app/io.github.fabx_dev.Apricus/config/apricus",
        home / ".local/share/gnome-shell/extensions/apricus-integration@fabx-dev",
    ]
    files = [
        home / ".config/autostart/io.github.fabx_dev.Apricus.desktop",
        home / ".config/systemd/user/apricus.service",
        home / ".local/bin/apricus",
        home / ".local/share/applications/io.github.fabx_dev.Apricus.desktop",
    ]
    for root in roots:
        if root.is_dir():
            files.extend(path for path in root.rglob("*") if path.is_file())
    result = {}
    for path in files:
        if not path.is_file():
            result[str(path)] = None
            continue
        info = path.stat()
        result[str(path)] = dict(
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            mode=stat.S_IMODE(info.st_mode),
            uid=info.st_uid,
            gid=info.st_gid,
            mtime_ns=info.st_mtime_ns,
        )
    return result


def snap_processes():
    result = {}
    for proc in Path("/proc").glob("[0-9]*"):
        try:
            if (proc / "attr/current").read_text().split()[0] != "snap.apricus.apricus":
                continue
            fields = (proc / "stat").read_text().rsplit(")", 1)[1].split()
            result[proc.name] = dict(comm=(proc / "comm").read_text().strip(), start=fields[19])
        except (OSError, IndexError, ProcessLookupError):
            pass
    return result


def app_bus_owners():
    return {
        name: subprocess.check_output(
            [
                "gdbus",
                "call",
                "--session",
                "--dest",
                "org.freedesktop.DBus",
                "--object-path",
                "/org/freedesktop/DBus",
                "--method",
                "org.freedesktop.DBus.NameHasOwner",
                name,
            ],
            text=True,
        ).strip()
        for name in (
            "io.github.fabx_dev.Apricus.server",
            "io.github.fabx_dev.Apricus.player",
            "io.github.fabx_dev.Apricus.gui4",
            "io.github.fabxdev.Apricus.GnomeBridge",
        )
    }


def source_hashes():
    return {
        str(path.relative_to(WORKTREE)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (WORKTREE / "src").rglob("*.py")
    }


def main():
    if len(sys.argv) != 1:
        raise SystemExit("Preflight only: no runtime/stop/install commands accepted")
    if Path(sys.prefix).resolve() != WORKTREE / ".venv":
        raise SystemExit("Use only the independent M1 .venv/bin/python")
    os.umask(0o077)
    build = WORKTREE / "build"
    if build.is_symlink():
        raise SystemExit("Do not use a symlinked build directory")
    build.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="m1p-", dir=build))
    for path in (
        "p/config/apricus",
        "p/config/vlc",
        "p/legacy/hidamari",
        "p/cache",
        "p/data",
        "p/media",
        "p/t",
        "p/autostart",
        "p/exports",
        "p/ipc",
        "evidence",
        "control",
    ):
        (root / path).mkdir(parents=True, exist_ok=True)
    if len(str(root / "p/t/pymp-12345678/listener-12345678")) >= 105:
        raise SystemExit("Private temp path is too long for multiprocessing AF_UNIX sockets")
    (root / "control/sentinel").write_text("must remain unchanged\n")
    sentinel_hash = hashlib.sha256((root / "control/sentinel").read_bytes()).hexdigest()
    before = dict(
        environment={key: os.environ.get(key) for key in PROTECTED},
        settings=host_settings(),
        stable_files=stable_files(),
        snap_processes=snap_processes(),
        app_bus_owners=app_bus_owners(),
        source_hashes=source_hashes(),
    )
    (root / "host-before.json").write_text(json.dumps(before, indent=2))
    (root / "marker.json").write_text(
        json.dumps(
            {
                "source": str(WORKTREE),
                "phase": "preflight",
                "git_head": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=WORKTREE, text=True
                ).strip(),
                "adapter_sha256": {
                    path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in ADAPTER.glob("*.py")
                },
            },
            indent=2,
        )
    )
    env = os.environ.copy()
    env.update(
        APRICUS_M1_PREFLIGHT_ROOT=str(root),
        APRICUS_M1_PHASE="preflight",
        APRICUS_M1_HOST_ENV=json.dumps(before["environment"]),
        PYTHONPATH=os.pathsep.join((str(ADAPTER), str(WORKTREE / "src"))),
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONNOUSERSITE="1",
        PYTHONSAFEPATH="1",
        XDG_CACHE_HOME=str(root / "p/cache"),
        XDG_DATA_HOME=str(root / "p/data"),
        TMPDIR=str(root / "p/t"),
        TMP=str(root / "p/t"),
        TEMP=str(root / "p/t"),
        DENO_DIR=str(root / "p/cache/deno"),
        SQLITE_TMPDIR=str(root / "p/t"),
        MESA_SHADER_CACHE_DIR=str(root / "p/cache/mesa"),
        __GL_SHADER_DISK_CACHE_PATH=str(root / "p/cache/nvidia"),
    )
    assert {key: env.get(key) for key in PROTECTED} == before["environment"]
    print(f"Evidence root: {root}", flush=True)
    with (root / "evidence/probe.log").open("w") as log:
        completed = subprocess.run(
            [sys.executable, str(ADAPTER / "m1_probe.py")],
            env=env,
            cwd=WORKTREE,
            stdout=log,
            stderr=log,
            timeout=300,
        )
    after = dict(
        environment={key: os.environ.get(key) for key in PROTECTED},
        settings=host_settings(),
        stable_files=stable_files(),
        snap_processes=snap_processes(),
        app_bus_owners=app_bus_owners(),
        source_hashes=source_hashes(),
    )
    (root / "host-after.json").write_text(json.dumps(after, indent=2))
    records = [json.loads(path.read_text()) for path in (root / "evidence").glob("*.json")]
    bootstraps = [
        json.loads(path.read_text()) for path in (root / "evidence/bootstrap").glob("*.json")
    ]

    def remaining_helpers():
        remaining = []
        for row in bootstraps:
            try:
                current = Path(f"/proc/{row['pid']}/stat").read_text().rsplit(")", 1)[1].split()[19]
                if current == row["start"]:
                    remaining.append(row["pid"])
            except OSError:
                pass
        return remaining

    deadline = time.monotonic() + 5
    while remaining_helpers() and time.monotonic() < deadline:
        time.sleep(0.05)
    labels = {"controller", "thread-worker", "exec-worker"} | {
        f"{method}-{role}"
        for method in ("spawn", "forkserver")
        for role in ("server", "video", "web", "gui")
    }
    checks = {
        # Matching values alone miss broken dconf change-notification setup.
        "native_dconf_initialization_clean": native_dconf_clean(
            (root / "evidence/probe.log").read_text(errors="replace")
        ),
        "all_probes_completed": completed.returncode == 0
        and len(records) == 11
        and {row["label"] for row in records} == labels,
        "all_kernel_guards_verified": bool(records)
        and all(
            row["landlock_abi"] >= 3
            and len(row["denials"]) == 5
            and set(row["denials"].values()) == {"DENIED"}
            for row in records
        ),
        "host_settings_unchanged": after["settings"] == before["settings"],
        "host_environment_unchanged": after["environment"] == before["environment"],
        "stable_files_unchanged": after["stable_files"] == before["stable_files"],
        "snap_processes_unchanged": after["snap_processes"] == before["snap_processes"],
        "no_application_bus_owners_changed": after["app_bus_owners"] == before["app_bus_owners"],
        "production_sources_unchanged": after["source_hashes"] == before["source_hashes"],
        "no_probe_helpers_left": not remaining_helpers(),
        "sentinel_unchanged": hashlib.sha256((root / "control/sentinel").read_bytes()).hexdigest()
        == sentinel_hash,
        "settings_equal_in_all_children": bool(records)
        and all(row["settings"] == before["settings"] for row in records),
        "session_equal_in_all_children": bool(records)
        and all(row["protected_environment"] == before["environment"] for row in records),
    }
    report = dict(
        status="PASS" if all(checks.values()) else "FAIL",
        checks=checks,
        roles=[(row["label"], row["pid"]) for row in records],
        bootstrap_pids=[row["pid"] for row in bootstraps],
        no_wallpaper_started=True,
        root=str(root),
    )
    (root / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if report["status"] != "PASS":
        print((root / "evidence/probe.log").read_text()[-8000:])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
