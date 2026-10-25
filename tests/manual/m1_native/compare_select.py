"""2x2 selection-persistence comparison: x3 vs M1 snap, GUI closed/open.

stdlib only, socket RPC. Usage:
  /usr/bin/python3 compare_select.py ROOT ARTIFACT_LABEL
Phases: 1) 10x video(test media); 2) 5x [webpage(local) (+STOP on M1) + video(test media)].
Evidence: ROOT/matrix-compare-NNN.json (unique per attempt) + phase summaries.
Set APRICUS_TEST_VIDEO and APRICUS_TEST_WEB_FIXTURE to operator-selected paths.
GUI handling (open/close) is done by the operator between script calls.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

SOCK = f"/run/user/{os.getuid()}/snap.apricus/apricus-ipc/server.sock"
WEBFIX = os.environ.get("APRICUS_TEST_WEB_FIXTURE")
HAWAII = os.environ.get("APRICUS_TEST_VIDEO")


def rpc(attr, args=None, call=False, op=None, val=None):
    with socket.socket(socket.AF_UNIX) as s:
        s.settimeout(20)
        s.connect(SOCK)
        req = {"op": op or ("call" if call else "get"), "attr": attr}
        if args:
            req["args"] = args
        if val is not None:
            req["val"] = val
        s.sendall((json.dumps(req) + "\n").encode())
        data = b""
        while b"\n" not in data:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    return json.loads(data)


def cfg():
    for base in (
        Path.home() / "snap/apricus/x1/.config/apricus/config.json",
        Path.home() / "snap/apricus/x3/.config/apricus/config.json",
    ):
        if base.exists():
            return json.loads(base.read_text()), str(base)
    raise RuntimeError("no snap profile config found")


def background():
    return subprocess.check_output(
        ["gsettings", "get", "org.gnome.desktop.background", "picture-uri"], text=True
    ).strip()


def save_count(root):
    n = 0
    for f in ("server.log", "server2.log"):
        p = root / f
        if p.exists():
            n += p.read_text(errors="replace").count("[Config] Saved")
    return n


def warn_count(root):
    n = 0
    for f in ("server.log", "server2.log"):
        p = root / f
        if p.exists():
            n += p.read_text(errors="replace").count("Default source is empty")
    return n


def attempt(root, action, label):
    before_saves = save_count(root)
    before_warns = warn_count(root)
    t0 = time.monotonic()
    response = rpc(action[0], args=action[1], call=True)
    t_reply = (time.monotonic() - t0) * 1000
    time.sleep(0.2)
    c0, _ = cfg()
    time.sleep(1.0)
    c1, _ = cfg()
    time.sleep(29.0)
    c2, _ = cfg()
    rec = dict(
        attempt=label,
        action=action[0],
        response=response,
        reply_ms=round(t_reply, 1),
        src_now=c0["data_source"].get("Default"),
        src_1s=c1["data_source"].get("Default"),
        src_30s=c2["data_source"].get("Default"),
        mode=c2.get("mode"),
        stopped=c2.get("is_stopped"),
        new_saves=save_count(root) - before_saves,
        new_warnings=warn_count(root) - before_warns,
        background=background(),
    )
    i = len(list(root.glob("matrix-compare-*.json"))) + 1
    (root / f"matrix-compare-{i:03d}.json").write_text(json.dumps(rec, indent=2))
    print(
        f"[{i:03d}] {action[0]} {str(c0['data_source'].get('Default'))[-40:]} | +30s {str(c2['data_source'].get('Default'))[-40:]} | saves+{rec['new_saves']} warn+{rec['new_warnings']}"
    )
    return rec


def main():
    if not WEBFIX or not HAWAII:
        raise SystemExit("Set APRICUS_TEST_WEB_FIXTURE and APRICUS_TEST_VIDEO explicitly")
    root = Path(sys.argv[1]).resolve()
    package = sys.argv[2]  # x3 | m1
    phase = sys.argv[3]  # 1 | 2
    if phase == "1":
        for i in range(10):
            attempt(root, ("video", [HAWAII, ""]), f"p1-{i + 1}")
            time.sleep(1)
    else:
        Path(WEBFIX).write_text(
            "<!doctype html><html><head><title>m1 cmp</title></head><body>CMP</body></html>"
        )
        for i in range(5):
            attempt(root, ("webpage", [f"file://{WEBFIX}", ""]), f"p2-web-{i + 1}")
            if package == "m1":
                rpc("stop_playback", call=True)
                time.sleep(1)
            attempt(root, ("video", [HAWAII, ""]), f"p2-vid-{i + 1}")
            time.sleep(1)
    print("phase done")


if __name__ == "__main__":
    main()
