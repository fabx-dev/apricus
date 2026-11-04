"""Snap M1 matrix driver: socket-RPC + gsettings + /proc. stdlib only.

Usage: /usr/bin/python3 snap_matrix.py ROOT ACTION [ARGS...]
Evidence: build/snap-live/matrix-NNN-ACTION.json (unique per sample).
Timings: T_call/T_reply/T_death/T_uri in ms; T_visible comes from user.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCK = f"/run/user/{os.getuid()}/snap.apricus/apricus-ipc/server.sock"
BUS = "io.github.fabx_dev.Apricus.server"
OPATH = "/io/github/fabx_dev/Apricus/server"
IFACE = "io.github.fabx_dev.apricus.server"


def rpc(attr, args=None, call=False):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(15)
        s.connect(SOCK)
        req = {"op": "call" if call else "get", "attr": attr}
        if args:
            req["args"] = args
        s.sendall((json.dumps(req) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def players():
    out = []
    for p in Path("/proc").glob("[0-9]*"):
        try:
            if (p / "comm").read_text().strip().startswith("apricus-player"):
                out.append(int(p.name))
        except OSError:
            pass
    return sorted(out)


def uri():
    return subprocess.check_output(
        ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"], text=True
    ).strip()


def save(root, action, record):
    n = len(list(root.glob(f"matrix-*-{action}.json"))) + 1
    p = root / f"matrix-{n:03d}-{action}.json"
    record.update(t=time.time(), action=action, sample=n)
    p.write_text(json.dumps(record, indent=2))
    print(f"{p.name}: {json.dumps(record)[:300]}")
    return record


def main():
    root = Path(sys.argv[1]).resolve()
    action = sys.argv[2]

    if action == "stop":
        before = players()
        u0 = uri()
        t_call = time.monotonic_ns()
        reply = rpc("stop_playback", call=True)
        t_reply = time.monotonic_ns()
        while any(Path(f"/proc/{p}").exists() for p in before):
            if (time.monotonic_ns() - t_call) / 1e6 > 15000:
                break
            time.sleep(0.02)
        t_death = time.monotonic_ns()
        u1 = uri()
        end = time.monotonic() + 5
        while time.monotonic() < end and uri() == u0:
            time.sleep(0.05)
        t_uri = time.monotonic_ns()
        state = rpc("playback_state")
        cfg = json.loads((Path.home() / "snap/apricus/x1/.config/apricus/config.json").read_text())
        save(
            root,
            "stop",
            dict(
                reply=reply,
                pids_before=before,
                pids_gone=not any(Path(f"/proc/{p}").exists() for p in before),
                t_reply_ms=(t_reply - t_call) / 1e6,
                t_death_ms=(t_death - t_call) / 1e6,
                t_uri_ms=(t_uri - t_call) / 1e6,
                uri_changed=u1 != u0,
                state=state,
                is_stopped=cfg.get("is_stopped"),
            ),
        )
    elif action == "resume":
        before = players()
        t_call = time.monotonic_ns()
        reply = rpc("resume_playback", call=True)
        t_reply = time.monotonic_ns()
        state, t_play = None, None
        end = time.monotonic() + 30
        while time.monotonic() < end:
            try:
                if "playing" in str(rpc("playback_state")):
                    state, t_play = "playing", time.monotonic_ns()
                    break
            except (OSError, ValueError):
                pass
            time.sleep(0.1)
        after = players()
        cfg = json.loads((Path.home() / "snap/apricus/x1/.config/apricus/config.json").read_text())
        save(
            root,
            "resume",
            dict(
                reply=reply,
                pids_before=before,
                pids_after=after,
                fresh=any(p not in before for p in after),
                t_reply_ms=(t_reply - t_call) / 1e6,
                t_playing_ms=(t_play - t_call) / 1e6 if t_play else None,
                state=state,
                is_stopped=cfg.get("is_stopped"),
            ),
        )
    elif action == "state":
        cfg = json.loads((Path.home() / "snap/apricus/x1/.config/apricus/config.json").read_text())
        save(
            root,
            f"state-{sys.argv[3]}",
            dict(
                playback=rpc("playback_state"),
                players=players(),
                is_stopped=cfg.get("is_stopped"),
                mode=cfg.get("mode"),
                static=cfg.get("is_static_wallpaper"),
                background=uri(),
            ),
        )
    elif action == "reload":
        reply = rpc("reload", call=True)
        time.sleep(2)
        cfg = json.loads((Path.home() / "snap/apricus/x1/.config/apricus/config.json").read_text())
        save(
            root,
            "reload",
            dict(
                reply=reply,
                players=players(),
                state=rpc("playback_state"),
                is_stopped=cfg.get("is_stopped"),
            ),
        )


if __name__ == "__main__":
    main()
