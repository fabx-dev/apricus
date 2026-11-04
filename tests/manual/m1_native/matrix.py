"""Unconfined functional-matrix driver: D-Bus + config/log observation only.

No Apricus imports, no settings writes beyond the app's own D-Bus API,
no production changes. Each action appends JSON evidence under the live root.
Usage: /usr/bin/python3 -S matrix.py ROOT ACTION [ARGS...]
"""

import json
import subprocess
import sys
import time
from pathlib import Path

BUS = "io.github.fabx_dev.Apricus.server"
PATH = "/io/github/fabx_dev/Apricus/server"
IFACE = "io.github.fabx_dev.apricus.server"


def call(method, *args):
    return subprocess.check_output(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            BUS,
            "--object-path",
            PATH,
            "--method",
            method,
            *args,
        ],
        text=True,
        timeout=20,
    ).strip()


def get(prop):
    return subprocess.check_output(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            BUS,
            "--object-path",
            PATH,
            "--method",
            "org.freedesktop.DBus.Properties.Get",
            IFACE,
            prop,
        ],
        text=True,
        timeout=10,
    ).strip()


def set_prop(prop, value):
    return subprocess.check_output(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            BUS,
            "--object-path",
            PATH,
            "--method",
            "org.freedesktop.DBus.Properties.Set",
            IFACE,
            prop,
            value,
        ],
        text=True,
        timeout=10,
    ).strip()


def player_pids():
    out = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            if (p / "comm").read_text().strip().startswith("apricus-player"):
                out.append(int(p.name))
        except OSError:
            pass
    return sorted(out)


def log_lines(root):
    p = root / "evidence/server.log"
    return p.read_text(errors="replace").splitlines() if p.exists() else []


def save(root, name, record):
    p = root / f"evidence/matrix-{name}.json"
    record.update(t=time.time(), name=name)
    p.write_text(json.dumps(record, indent=2))
    print(f"{name}: {json.dumps(record)[:400]}")
    return record


def wait_state(want, timeout=25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if want in get("playback_state"):
                return True
        except subprocess.SubprocessError:
            pass
        time.sleep(0.2)
    return False


def main():
    root = Path(sys.argv[1]).resolve()
    action = sys.argv[2]
    args = sys.argv[3:]

    if action == "stop":
        before = player_pids()
        t0 = time.monotonic_ns()
        reply = call(IFACE + ".stop_playback")
        end = time.monotonic() + 10
        while time.monotonic() < end and any(Path(f"/proc/{p}").exists() for p in before):
            time.sleep(0.05)
        gone = all(not Path(f"/proc/{p}").exists() for p in before)
        stopped = wait_state("stopped", 10)
        cfg = json.loads((Path.home() / ".config/apricus/config.json").read_text())
        save(
            root,
            "stop",
            dict(
                reply=reply,
                pids_before=before,
                pids_gone=gone,
                state_stopped=stopped,
                latency_ms=(time.monotonic_ns() - t0) / 1e6,
                is_stopped=cfg.get("is_stopped"),
            ),
        )
    elif action == "resume":
        before = player_pids()
        t0 = time.monotonic_ns()
        reply = call(IFACE + ".resume_playback")
        playing = wait_state("playing", 30)
        t_play = (time.monotonic_ns() - t0) / 1e6
        # First new perf frame line after resume.
        n0 = len(log_lines(root))
        first_frame_ms = None
        end = time.monotonic() + 30
        while time.monotonic() < end and first_frame_ms is None:
            for line in log_lines(root)[n0:]:
                if "first frame" in line:
                    first_frame_ms = (time.monotonic_ns() - t0) / 1e6
                    break
            time.sleep(0.2)
        after = player_pids()
        cfg = json.loads((Path.home() / ".config/apricus/config.json").read_text())
        save(
            root,
            "resume",
            dict(
                reply=reply,
                pids_before=before,
                pids_after=after,
                fresh_player=any(p not in before for p in after),
                state_playing=playing,
                resume_to_playing_ms=t_play,
                resume_to_frame_log_ms=first_frame_ms,
                is_stopped=cfg.get("is_stopped"),
            ),
        )
    elif action == "state":
        cfg = json.loads((Path.home() / ".config/apricus/config.json").read_text())
        save(
            root,
            f"state-{args[0]}",
            dict(
                playback=get("playback_state"),
                players=player_pids(),
                is_stopped=cfg.get("is_stopped"),
                mode=cfg.get("mode"),
                static=cfg.get("is_static_wallpaper"),
                background=subprocess.check_output(
                    ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"], text=True
                ).strip(),
            ),
        )
    elif action == "reload":
        reply = call(IFACE + ".reload")
        time.sleep(2)
        cfg = json.loads((Path.home() / ".config/apricus/config.json").read_text())
        save(
            root,
            "reload-while-stopped",
            dict(
                reply=reply,
                players=player_pids(),
                state=get("playback_state"),
                is_stopped=cfg.get("is_stopped"),
            ),
        )
    elif action == "dnd_repeat":
        # Double STOP then double RESUME idempotency.
        r = {}
        r["stop1"] = call(IFACE + ".stop_playback")
        r["stop2"] = call(IFACE + ".stop_playback")
        time.sleep(1)
        cfg = json.loads((Path.home() / ".config/apricus/config.json").read_text())
        r["stopped_twice"] = dict(
            players=player_pids(), state=get("playback_state"), is_stopped=cfg.get("is_stopped")
        )
        r["resume1"] = call(IFACE + ".resume_playback")
        ok = wait_state("playing", 30)
        r["resume2"] = call(IFACE + ".resume_playback")
        time.sleep(1)
        cfg = json.loads((Path.home() / ".config/apricus/config.json").read_text())
        r["resumed_twice"] = dict(
            players=player_pids(),
            state=get("playback_state"),
            is_stopped=cfg.get("is_stopped"),
            playing_reached=ok,
        )
        save(root, "double-stop-resume", r)
    else:
        raise SystemExit(f"unknown action {action}")


if __name__ == "__main__":
    main()
