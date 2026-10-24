#!/usr/bin/env python3
"""Authorized manual-session supervisor and standalone stdlib-only recovery.

NO action happens on import. prepare never stops an application. supervise is
an explicit, separately authorized command. The copied restore.py works with
/usr/bin/python3 without this source tree, venv, pydbus or Apricus imports.
"""

import fcntl
import hashlib
import json
import os
import pathlib
import re
import shutil
import signal
import socket
import struct
import subprocess
import sys
import time

NAME = "io.github.fabx_dev.Apricus.server"
PATH = "/io/github/fabx_dev/Apricus/server"
INTERFACE = "io.github.fabx_dev.apricus.server"
SNAP_SOCKET = f"/run/user/{os.getuid()}/snap.apricus/apricus-ipc/server.sock"


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, data):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2))
    temporary.chmod(0o600)
    temporary.replace(path)


def command(args, timeout=10):
    return subprocess.check_output(
        args, text=True, stderr=subprocess.STDOUT, timeout=timeout
    ).strip()


def dbus(method, *args, name=NAME, path=PATH, timeout=10):
    return command(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            name,
            "--object-path",
            path,
            "--method",
            method,
            *args,
        ],
        timeout,
    )


def owner_pid(name=NAME):
    try:
        result = dbus(
            "org.freedesktop.DBus.GetConnectionUnixProcessID",
            name,
            name="org.freedesktop.DBus",
            path="/org/freedesktop/DBus",
        )
        return int(re.search(r"uint32\s+(\d+)", result)[1])
    except (subprocess.SubprocessError, TypeError):
        return None


def playback():
    value = dbus("org.freedesktop.DBus.Properties.Get", INTERFACE, "playback_state")
    return next(
        (state for state in ("stopped", "playing", "paused", "unavailable") if state in value),
        "unknown",
    )


def identity(pid):
    try:
        fields = pathlib.Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return dict(pid=int(pid), start=fields[19], state=fields[0], ppid=int(fields[1]))
    except OSError:
        return None


def same(row):
    current = identity(row["pid"])
    return current is not None and current["start"] == row["start"]


def tree(pid):
    result = {pid}
    changed = True
    while changed:
        changed = False
        for path in pathlib.Path("/proc").glob("[0-9]*"):
            row = identity(int(path.name))
            if row and row["ppid"] in result and row["pid"] not in result:
                result.add(row["pid"])
                changed = True
    return [row for p in result if (row := identity(p))]


def snap_rows():
    rows = []
    for path in pathlib.Path("/proc").glob("[0-9]*"):
        try:
            if (path / "attr/current").read_text().split()[0] == "snap.apricus.apricus":
                row = identity(int(path.name))
                if row:
                    rows.append(row)
        except (OSError, IndexError):
            pass
    return rows


def applications():
    result = []
    for path in pathlib.Path("/proc").glob("[0-9]*"):
        try:
            row = identity(int(path.name))
            if (
                row
                and row["state"] != "Z"
                and (path / "status").stat().st_uid == os.getuid()
                and (path / "comm").read_text().startswith("apricus")
            ):
                result.append(int(path.name))
        except OSError:
            pass
    return result


def rpc(attr, call=False, expected_pid=None):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(8)
        connection.connect(SNAP_SOCKET)
        pid, uid, _gid = struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )
        if uid != os.getuid() or (expected_pid and pid != expected_pid):
            raise RuntimeError("Unexpected Snap socket peer")
        request = dict(op="call" if call else "get", attr=attr)
        connection.sendall((json.dumps(request) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = connection.recv(65536)
            if not chunk:
                break
            data += chunk
        return pid, json.loads(data) if data else None


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def settings():
    result = {}
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
            result[f"{schema}:{key}"] = command(["gsettings", "get", schema, key])
    return result


def prepare(root):
    os.umask(0o077)
    if read(root / "report.json")["status"] != "PASS":
        raise RuntimeError("Preflight must pass first")
    backup = root / "backup"
    backup.mkdir(mode=0o700)  # Never overwrite/reuse historical backups.
    home = pathlib.Path.home()
    config_dir = (home / "snap/apricus/current/.config/apricus").resolve()
    config = read(config_dir / "config.json")
    server_pid, answer = rpc("mode")
    if not answer or not answer["ok"] or answer["res"] != config["mode"]:
        raise RuntimeError("Snap runtime/config disagree")
    if "snap.apricus.apricus" not in pathlib.Path(f"/proc/{server_pid}/attr/current").read_text():
        raise RuntimeError("Not the expected confined Snap")
    items = []
    for label, directory, restore in (
        ("snap-config", config_dir, True),
        ("native-config", home / ".config/apricus", False),
        (
            "extension",
            home / ".local/share/gnome-shell/extensions/apricus-integration@fabx-dev",
            False,
        ),
    ):
        for original in sorted(directory.rglob("*")) if directory.exists() else []:
            if original.is_symlink():
                raise RuntimeError(f"Review symlink before backing up: {original}")
            if not original.is_file():
                continue
            destination = backup / label / original.relative_to(directory)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(original, destination)
            sha = digest(destination)
            if sha != digest(original):
                raise RuntimeError("Backup verification failed")
            items.append(
                dict(
                    original=str(original),
                    backup=str(destination.relative_to(root)),
                    sha256=sha,
                    restore=restore,
                )
            )
    autostart = {}
    for path in (
        home / ".config/autostart/io.github.fabx_dev.Apricus.desktop",
        home / ".config/systemd/user/apricus.service",
    ):
        autostart[str(path)] = digest(path) if path.is_file() else None
    manifest = dict(
        installed_snap=command(["snap", "list", "apricus"]),
        source_commit=command(["git", "rev-parse", "HEAD"]),
        snap_server=identity(server_pid),
        snap_tree=tree(server_pid),
        snap_profile=str(config_dir),
        config=config,
        settings=settings(),
        items=items,
        autostart=autostart,
        bridge_was_owned=owner_pid("io.github.fabxdev.Apricus.GnomeBridge") is not None,
        created=time.time(),
        restore_script_sha256=digest(pathlib.Path(__file__)),
    )
    write(backup / "manifest.json", manifest)
    shutil.copy2(__file__, root / "restore.py")
    write(root / "state.json", dict(snap_stopped=False, restored=False, m1_processes=[]))
    # Preseed only private settings. Approved media are read-only inputs, not
    # copied datasets; filesystem guard denies writes to their original files.
    isolated = json.loads(json.dumps(config))
    source = (config.get("data_source") or {}).get("Default", "")
    if config.get("mode") != "MODE_VIDEO" or not pathlib.Path(source).is_file():
        raise RuntimeError("Expected the approved selected local video")
    isolated.update(
        is_stopped=False,
        is_first_time=False,
        is_show_systray=False,
        video_dir=str(pathlib.Path(source).parent),
    )
    write(root / "p/config/apricus/config.json", isolated)
    print(
        json.dumps(
            dict(
                backup_verified=True,
                files=len(items),
                revision=manifest["installed_snap"],
                mode=config["mode"],
                video=pathlib.Path(source).name,
                playlist_count=len((config.get("playlist") or {}).get("Default") or []),
                volume=config.get("audio_volume"),
                mute=config.get("is_mute"),
                static=config.get("is_static_wallpaper"),
                snap_pid=server_pid,
                restore=f"/usr/bin/python3 {root}/restore.py",
            ),
            indent=2,
        )
    )


def verify(root):
    manifest = read(root / "backup/manifest.json")
    for item in manifest["items"]:
        if digest(root / item["backup"]) != item["sha256"]:
            raise RuntimeError("Backup contents changed")
    if digest(root / "restore.py") != manifest["restore_script_sha256"]:
        raise RuntimeError("Independent restore script changed")
    return manifest


def remember(root, pid):
    state = read(root / "state.json")
    known = {f"{row['pid']}:{row['start']}": row for row in state["m1_processes"]}
    for row in tree(pid):
        known[f"{row['pid']}:{row['start']}"] = row
    state["m1_processes"] = list(known.values())
    write(root / "state.json", state)


def restore(root):
    with (root / "restore.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read(root / "state.json")
        if state.get("restored"):
            return
        manifest = verify(root)
        m1 = state.get("m1_server")
        if m1 and same(m1):
            remember(root, m1["pid"])
            if owner_pid() == m1["pid"]:
                try:
                    dbus(INTERFACE + ".quit", timeout=8)
                except subprocess.SubprocessError:
                    pass
            # Only recorded process identities, never signal by name or a
            # shared Snap/GNOME process group.
        state = read(root / "state.json")
        for sig, delay in ((signal.SIGTERM, 3), (signal.SIGKILL, 2)):
            for row in reversed(state["m1_processes"]):
                if same(row):
                    try:
                        os.kill(row["pid"], sig)
                    except ProcessLookupError:
                        pass
            time.sleep(delay)
        remaining = [
            row
            for row in state["m1_processes"]
            if same(row) and identity(row["pid"])["state"] != "Z"
        ]
        if remaining:
            raise RuntimeError(f"M1 still active; do not restart Snap: {remaining}")
        for row in state["m1_processes"]:
            try:
                os.waitpid(row["pid"], os.WNOHANG)
            except (ChildProcessError, ProcessLookupError):
                pass
        if state.get("snap_stopped"):
            if any(row["state"] != "Z" for row in snap_rows()):
                raise RuntimeError(
                    "An independently started Snap is active; do not overwrite its profile"
                )
            if applications():
                raise RuntimeError("Another Apricus is active; do not start a second controller")
            for item in manifest["items"]:
                if item["restore"]:
                    shutil.copy2(root / item["backup"], item["original"])
            for compound, value in manifest["settings"].items():
                schema, key = compound.split(":", 1)
                if (schema, key) in {
                    ("org.gnome.desktop.background", "picture-uri"),
                    ("org.gnome.desktop.background", "picture-uri-dark"),
                    ("org.gnome.desktop.screensaver", "picture-uri"),
                }:
                    command(["gsettings", "set", schema, key, value])
            if settings() != manifest["settings"]:
                raise RuntimeError(
                    "Unexpected GNOME settings differences; no unrelated setting restored automatically"
                )
            with (root / "snap-restored.log").open("a") as log:
                subprocess.Popen(
                    ["/snap/bin/apricus", "-b"],
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    _pid, response = rpc("mode")
                    if response and response["ok"]:
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.25)
            else:
                raise RuntimeError("Original Snap restart not confirmed")
        for item in manifest["items"]:
            if not item["restore"] and digest(pathlib.Path(item["original"])) != item["sha256"]:
                raise RuntimeError(
                    "Protected native/extension files changed; never auto-overwrite them"
                )
        for path, sha in manifest["autostart"].items():
            current = digest(pathlib.Path(path)) if pathlib.Path(path).is_file() else None
            if current != sha:
                raise RuntimeError("Autostart changed unexpectedly")
        verify(root)
        state["restored"] = True
        state["restored_at"] = time.time()
        write(root / "state.json", state)
        print(
            "RESTORED: M1 stopped, settings/profile restored, original Snap running; backup hashes intact",
            flush=True,
        )


def live_env(root):
    from launcher import ADAPTER, WORKTREE

    before = read(root / "host-before.json")["environment"]
    expected = dict(before)
    # This is Apricus's existing native GTK3/XWayland selection, not a change
    # to DISPLAY, WAYLAND_DISPLAY, session type or the host environment.
    expected["GDK_BACKEND"] = "x11"
    env = dict(os.environ)
    env.update(
        APRICUS_M1_PREFLIGHT_ROOT=str(root),
        APRICUS_M1_PHASE="live",
        APRICUS_M1_HOST_ENV=json.dumps(expected),
        GDK_BACKEND="x11",
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
    return env


def supervise(root):
    os.umask(0o077)
    manifest = verify(root)
    state = read(root / "state.json")
    if state.get("restored") or state.get("snap_stopped"):
        raise RuntimeError("Fresh prepared session required")
    old_pid = manifest["snap_server"]["pid"]
    if not same(manifest["snap_server"]):
        raise RuntimeError("Snap identity changed since backup; re-inventory first")
    for item in manifest["items"]:
        if item["restore"] and pathlib.Path(item["original"]).name == "config.json":
            if digest(pathlib.Path(item["original"])) != item["sha256"]:
                raise RuntimeError("Snap config changed since backup; do not stop it")

    def interrupted(_signum, _frame):
        raise KeyboardInterrupt("Supervisor interrupted; restoring")

    signal.signal(signal.SIGTERM, interrupted)
    # Commit recovery intent BEFORE the authorized shutdown.
    state["snap_stopped"] = True
    write(root / "state.json", state)
    process = None
    try:
        try:
            rpc("quit", call=True, expected_pid=old_pid)
        except (OSError, ValueError):
            pass  # Quit can close the socket before its reply; verify exit.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and any(
            same(row) and identity(row["pid"])["state"] != "Z" for row in manifest["snap_tree"]
        ):
            time.sleep(0.1)
        if (
            any(same(row) and identity(row["pid"])["state"] != "Z" for row in manifest["snap_tree"])
            or applications()
            or any(row["state"] != "Z" for row in snap_rows())
            or owner_pid()
        ):
            raise RuntimeError("Prior Apricus instance/children remain; M1 NOT started")
        write(root / "live-authorization.json", dict(approved=True, backup_verified=True))
        env = live_env(root)
        with (root / "evidence/live-server.log").open("w") as log:
            process = subprocess.Popen(
                [str(pathlib.Path(sys.executable)), "-m", "apricus", "-b", "-d"],
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        state = read(root / "state.json")
        state["m1_server"] = identity(process.pid)
        write(root / "state.json", state)
        remember(root, process.pid)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"M1 server exited at startup: {process.returncode}")
            remember(root, process.pid)
            try:
                if owner_pid() == process.pid and playback() == "playing":
                    break
            except subprocess.SubprocessError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("M1 did not reach VLC playing")
        write(
            root / "ready.json",
            dict(pid=process.pid, time=time.time(), state="playing", visible_frame="NOT MEASURED"),
        )
        print(
            f"READY: {root}; M1 server {process.pid}; visible frame requires user confirmation",
            flush=True,
        )
        expire = time.monotonic() + 1200
        while not (root / "finish").exists() and time.monotonic() < expire:
            if process.poll() is not None:
                raise RuntimeError(f"M1 server exited unexpectedly: {process.returncode}")
            remember(root, process.pid)
            if snap_rows():
                raise RuntimeError("A concurrent Snap appeared")
            log = (root / "evidence/live-server.log").read_text(errors="replace")
            if "Traceback (most recent call last)" in log or "bootstrap failed closed" in log:
                raise RuntimeError("M1 Python/bootstrap error; interrupt and restore")
            time.sleep(1)
    except BaseException as error:
        write(root / "failure.json", dict(error=str(error), time=time.time()))
        print(f"FAIL: {error}", flush=True)
    finally:
        restore(root)
        if process:
            process.wait(timeout=10)


def main():
    if pathlib.Path(__file__).name == "restore.py" and len(sys.argv) == 1:
        restore(pathlib.Path(__file__).resolve().parent)
        return
    action, directory = sys.argv[1:]
    root = pathlib.Path(directory).resolve()
    if action == "prepare":
        prepare(root)
    elif action == "verify":
        print("PASS: independent backup verification", len(verify(root)["items"]), "files")
    elif action == "supervise":
        supervise(root)
    elif action == "restore":
        restore(root)
    elif action == "watchdog":
        deadline = time.monotonic() + 1320
        while time.monotonic() < deadline:
            if read(root / "state.json", {}).get("restored"):
                return
            time.sleep(2)
        restore(root)
    else:
        raise SystemExit("prepare | verify | supervise | restore ROOT")


if __name__ == "__main__":
    main()
