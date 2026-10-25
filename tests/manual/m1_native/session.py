"""Unconfined source session supervision/recovery. Never reinstall Snap.

Recovery copy uses stdlib only, independent of GUI, venv and the source tree.
No production monkeypatch, settings backend replacement or filesystem policy.
"""

import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def read(path):
    return json.loads(path.read_text())


def write(path, value):
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, indent=2))
    temp.replace(path)


def identity(pid):
    try:
        fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        return dict(pid=pid, start=fields[19], ppid=int(fields[1]), state=fields[0])
    except OSError:
        return None


def same(row):
    now = identity(row["pid"])
    return now and now["start"] == row["start"]


def remember(root, pid):
    state = read(root / "state.json")
    descendants = {pid}
    all_rows = [identity(int(p.name)) for p in Path("/proc").glob("[0-9]*")]
    for _ in range(10):
        descendants.update(row["pid"] for row in all_rows if row and row["ppid"] in descendants)
    rows = {f"{row['pid']}:{row['start']}": row for row in state["processes"]}
    for row in all_rows:
        if row and row["pid"] in descendants:
            rows[f"{row['pid']}:{row['start']}"] = row
    state["processes"] = list(rows.values())
    write(root / "state.json", state)


def settings():
    schemas = {
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
    return {
        f"{schema}:{key}": subprocess.check_output(
            ["gsettings", "get", schema, key], text=True
        ).strip()
        for schema, keys in schemas.items()
        for key in keys
    }


def prepare(worktree):
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix="m1-native-live-", dir=worktree / "build"))
    (root / "backup").mkdir()
    (root / "evidence").mkdir()
    paths = []
    for label, path in (
        ("config", Path.home() / ".config/apricus"),
        ("cache", Path.home() / ".cache/apricus"),
    ):
        if path.is_symlink():
            raise RuntimeError("Review symlink before native recovery")
        existed = path.exists()
        if existed:
            shutil.copytree(path, root / "backup" / label, symlinks=True)
        paths.append(dict(label=label, path=str(path), existed=existed))
    write(
        root / "manifest.json",
        dict(
            paths=paths,
            settings=settings(),
            restore_missing_original_uri=True,
            snap_reinstall_authorized=False,
        ),
    )
    write(root / "state.json", dict(processes=[], restored=False))
    shutil.copy2(__file__, root / "recover.py")
    print(root)


def recover(root):
    with (root / "recovery.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = read(root / "state.json")
        if state["restored"]:
            return
        if state.get("server") and same(state["server"]):
            remember(root, state["server"]["pid"])
            # Graceful source server API; only when the exact recorded server
            # still owns the name (UID-scoped D-Bus ownership is checked).
            result = subprocess.run(
                [
                    "gdbus",
                    "call",
                    "--session",
                    "--dest",
                    "org.freedesktop.DBus",
                    "--object-path",
                    "/org/freedesktop/DBus",
                    "--method",
                    "org.freedesktop.DBus.GetConnectionUnixProcessID",
                    "io.github.fabx_dev.Apricus.server",
                ],
                capture_output=True,
                text=True,
            )
            if f"uint32 {state['server']['pid']}" in result.stdout:
                subprocess.run(
                    [
                        "gdbus",
                        "call",
                        "--session",
                        "--dest",
                        "io.github.fabx_dev.Apricus.server",
                        "--object-path",
                        "/io/github/fabx_dev/Apricus/server",
                        "--method",
                        "io.github.fabx_dev.apricus.server.quit",
                    ],
                    capture_output=True,
                    timeout=8,
                )
        state = read(root / "state.json")
        for sig, delay in ((signal.SIGTERM, 2), (signal.SIGKILL, 1)):
            for row in reversed(state["processes"]):
                if same(row):
                    try:
                        os.kill(row["pid"], sig)
                    except ProcessLookupError:
                        pass
            time.sleep(delay)
        remaining = [
            row for row in state["processes"] if same(row) and identity(row["pid"])["state"] != "Z"
        ]
        if remaining:
            raise RuntimeError(f"Recovery incomplete, live M1 remains: {remaining}")
        for row in state["processes"]:
            try:
                os.waitpid(row["pid"], os.WNOHANG)
            except ChildProcessError:
                pass
        manifest = read(root / "manifest.json")
        for item in manifest["paths"]:
            path = Path(item["path"])
            preserved = root / f"after-test-{item['label']}"
            if preserved.exists():
                raise RuntimeError("Partial recovery: review preserved data before retry")
            if path.exists():
                path.rename(preserved)
            if item["existed"]:
                shutil.copytree(root / "backup" / item["label"], path, symlinks=True)
        for compound, value in manifest["settings"].items():
            schema, key = compound.split(":", 1)
            if schema in (
                "org.gnome.desktop.background",
                "org.gnome.desktop.screensaver",
            ) and key.startswith("picture-uri"):
                subprocess.run(["gsettings", "set", schema, key, value], check=True)
        if settings() != manifest["settings"]:
            raise RuntimeError("Settings not restored exactly; no unrelated settings overwritten")
        state["restored"] = True
        state["snap_reinstalled"] = False
        write(root / "state.json", state)
        print(
            "RECOVERED: M1 stopped; native data/URI settings restored; Snap NOT reinstalled",
            flush=True,
        )


def supervise(root, worktree):
    # Refuse any still-installed Apricus Snap or another wallpaper process.
    if subprocess.run(["snap", "list", "apricus"], capture_output=True).returncode == 0:
        raise RuntimeError("Snap still installed")
    for path in Path("/proc").glob("[0-9]*/comm"):
        try:
            if path.stat().st_uid == os.getuid() and path.read_text().startswith("apricus"):
                raise RuntimeError("Concurrent Apricus process")
        except OSError:
            pass

    def interrupted(*args):
        raise KeyboardInterrupt("Supervisor interrupted")

    signal.signal(signal.SIGTERM, interrupted)
    process = None
    try:
        with (root / "evidence/server.log").open("w") as log:
            process = subprocess.Popen(
                [
                    str(worktree / ".venv/bin/python"),
                    "-S",
                    str(worktree / "tests/manual/m1_native/source_entry.py"),
                    "--run",
                ],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        state = read(root / "state.json")
        state["server"] = identity(process.pid)
        write(root / "state.json", state)
        expire = time.monotonic() + 1200
        while time.monotonic() < expire and not (root / "finish").exists():
            remember(root, process.pid)
            if process.poll() is not None:
                raise RuntimeError(f"M1 exited: {process.returncode}")
            log = (root / "evidence/server.log").read_text(errors="replace")
            # H.264 teardown lines and blue-background observations are recorded,
            # not auto-abort signals. Abort only on Python tracebacks (real crash
            # evidence), unexpected exit, or uncontrolled processes below.
            if "Traceback (most recent call last)" in log:
                raise RuntimeError("Source Python traceback; abort and recover")
            time.sleep(0.5)
    except BaseException as error:
        write(root / "failure.json", dict(error=repr(error)))
        print(f"FAIL: {error}", flush=True)
    finally:
        recover(root)
        if process:
            process.wait(timeout=10)


if __name__ == "__main__":
    if Path(__file__).name == "recover.py" and len(sys.argv) == 1:
        recover(Path(__file__).resolve().parent)
    else:
        action = sys.argv[1]
        worktree = Path(__file__).resolve().parents[3]
        if action == "prepare":
            prepare(worktree)
        elif action == "supervise":
            supervise(Path(sys.argv[2]).resolve(), worktree)
        elif action == "recover":
            recover(Path(sys.argv[2]).resolve())
        elif action == "watchdog":
            root = Path(sys.argv[2]).resolve()
            deadline = time.monotonic() + 1320
            while time.monotonic() < deadline:
                if read(root / "state.json")["restored"]:
                    break
                time.sleep(2)
            else:
                recover(root)
