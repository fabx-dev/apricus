"""Backup-only preparation. No sudo, IPC quit, package or settings mutation.

Preparation requires APRICUS_TEST_SNAP_ARTIFACT to identify the retained package.
Uses no Apricus imports, sitecustomize or Landlock. verify.py is copied into
the evidence directory and works using only the system Python standard library.
"""

import hashlib
import json
import os
import shutil
import socket
import struct
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

WORKTREE = Path(__file__).resolve().parents[3]
ARTIFACT = Path(os.environ.get("APRICUS_TEST_SNAP_ARTIFACT", ""))


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def output(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()


def verify(root):
    manifest = json.loads((root / "manifest.json").read_text())
    for name, digest in manifest["retained_sha256"].items():
        if sha(root / name) != digest:
            raise RuntimeError(f"Hash mismatch: {name}")
    count = 0
    with tarfile.open(root / "user-data.tar.gz") as archive:
        for member in archive:
            if member.isfile():
                with archive.extractfile(member) as stream:
                    while stream.read(1024 * 1024):
                        pass
                count += 1
    print(f"PASS: retained file hashes and archive readability ({count} files)")


def main():
    if Path(__file__).name == "verify.py":
        verify(Path(__file__).resolve().parent)
        return
    if len(sys.argv) != 1:
        raise SystemExit("Backup preparation only, no removal/install/run subcommand")
    if not os.environ.get("APRICUS_TEST_SNAP_ARTIFACT") or not ARTIFACT.is_file():
        raise SystemExit("Set APRICUS_TEST_SNAP_ARTIFACT to a verified rollback package")
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix="m1-native-prep-", dir=WORKTREE / "build"))
    home = Path.home()
    configuration = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config")
    cache = Path(os.environ.get("XDG_CACHE_HOME") or home / ".cache")
    data = Path(os.environ.get("XDG_DATA_HOME") or home / ".local/share")
    snap_config = home / "snap/apricus/current/.config/apricus/config.json"
    config = json.loads(snap_config.read_text())
    config_sha = sha(snap_config)
    runtime = {}
    for attr in ("mode", "volume", "is_mute", "is_static_wallpaper", "is_playing"):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(5)
            connection.connect(f"/run/user/{os.getuid()}/snap.apricus/apricus-ipc/server.sock")
            pid, uid, gid = struct.unpack(
                "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
            )
            if (
                uid != os.getuid()
                or "snap.apricus.apricus" not in Path(f"/proc/{pid}/attr/current").read_text()
            ):
                raise RuntimeError("Unexpected Snap RPC peer")
            connection.sendall((json.dumps(dict(op="get", attr=attr)) + "\n").encode())
            reply = b""
            while b"\n" not in reply:
                chunk = connection.recv(65536)
                if not chunk:
                    raise RuntimeError("Incomplete Snap read-only RPC response")
                reply += chunk
            response = json.loads(reply)
            if not response["ok"]:
                raise RuntimeError(response)
            runtime[attr] = response["res"]
            runtime["peer"] = dict(pid=pid, uid=uid, gid=gid)
    (root / "runtime.json").write_text(json.dumps(runtime, indent=2))
    commands = {
        "snap-list": ["snap", "list", "apricus", "apricus-channel-poc"],
        "snap-connections": ["snap", "connections", "apricus"],
        "snap-saved": ["snap", "saved", "apricus"],
        "mount": ["findmnt", "-no", "SOURCE,FSTYPE,OPTIONS", "/snap/apricus/x3"],
        "processes": ["ps", "-u", str(os.getuid()), "-o", "pid,ppid,pgid,stat,comm,args"],
        "extension": ["gnome-extensions", "info", "apricus-integration@fabx-dev"],
        "ding": ["gnome-extensions", "info", "ding@rastersoft.com"],
        "gnome-background": ["gsettings", "list-recursively", "org.gnome.desktop.background"],
        "gnome-screensaver": ["gsettings", "list-recursively", "org.gnome.desktop.screensaver"],
        "gnome-shell": ["gsettings", "list-recursively", "org.gnome.shell"],
        "gnome-interface": ["gsettings", "list-recursively", "org.gnome.desktop.interface"],
        "dconf-dump": ["dconf", "dump", "/"],
    }
    for name, args in commands.items():
        (root / f"{name}.txt").write_text(output(args) + "\n")
    settings = {}
    for schema, keys in {
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
    }.items():
        for key in keys:
            settings[f"{schema}:{key}"] = output(["gsettings", "get", schema, key])
    targets = [
        home / "snap/apricus",
        configuration / "apricus",
        configuration / "hidamari",
        cache / "apricus",
        cache / "yt-dlp",
        cache / "vlc",
        data / "vlc",
        data / "webkitgtk",
        cache / "webkitgtk",
        configuration / "vlc",
        configuration / "autostart/io.github.fabx_dev.Apricus.desktop",
        configuration / "systemd/user/apricus.service",
        home / ".local/share/gnome-shell/extensions/apricus-integration@fabx-dev",
    ]
    inventory = {}
    for path in targets:
        inventory[str(path)] = dict(exists=path.exists(), symlink=path.is_symlink())
    with tarfile.open(root / "user-data.tar.gz", "w:gz", dereference=False) as archive:
        for path in targets:
            if path.exists() or path.is_symlink():
                archive.add(path, arcname=str(path).lstrip("/"))
    if sha(snap_config) != config_sha:
        raise RuntimeError(
            f"Active config changed during backup; retain evidence and re-inventory: {root}"
        )
    package = root / "apricus_2.0.7_previous.snap"
    shutil.copy2(ARTIFACT, package)
    if sha(package) != sha(ARTIFACT):
        raise RuntimeError("Retained Snap package verification failed")
    shutil.copy2(__file__, root / "verify.py")
    retained = {p.name: sha(p) for p in root.iterdir() if p.is_file()}
    manifest = dict(
        source_commit=output(["git", "rev-parse", "HEAD"]),
        installed_revision="x3",
        installed_mount="/snap/apricus/x3",
        installed_blob="/var/lib/snapd/snaps/apricus_x3.snap",
        installed_blob_checksum="PENDING USER PRIVILEGED READ; no privilege used",
        artifact_source=str(ARTIFACT),
        retained_sha256=retained,
        config=config,
        settings=settings,
        inventory=inventory,
        environment={
            key: os.environ.get(key)
            for key in (
                "HOME",
                "XDG_CONFIG_HOME",
                "XDG_CACHE_HOME",
                "XDG_DATA_HOME",
                "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS",
                "DISPLAY",
                "WAYLAND_DISPLAY",
                "DCONF_PROFILE",
                "PYTHONPATH",
            )
        },
        no_snap_stopped=True,
        no_package_removed=True,
        no_m1_started=True,
    )
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    verify(root)
    print(root)
    print("STOP: privileged installed-blob digest and removal require user intervention")


if __name__ == "__main__":
    main()
