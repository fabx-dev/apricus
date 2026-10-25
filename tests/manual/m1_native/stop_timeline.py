"""STOP timeline diagnosis: no production changes, no settings writes.

Records GNOME state before STOP, the expected static asset, the moment the
player dies, and every subsequent picture-uri change. Run externally while M1
is playing; it only issues the normal stop_playback D-Bus call once.
"""

import json
import subprocess
import sys
import time
from pathlib import Path


def gsettings(schema, key):
    return subprocess.check_output(["gsettings", "get", schema, key], text=True, timeout=10).strip()


def snapshot(label):
    return {
        "t": time.time(),
        "label": label,
        "background": gsettings("org.gnome.desktop.background", "picture-uri"),
        "background_dark": gsettings("org.gnome.desktop.background", "picture-uri-dark"),
        "screensaver": gsettings("org.gnome.desktop.screensaver", "picture-uri"),
        "primary": gsettings("org.gnome.desktop.background", "primary-color"),
        "options": gsettings("org.gnome.desktop.background", "picture-options"),
    }


def png_info(path):
    info = {"exists": path.is_file()}
    if path.is_file():
        data = path.read_bytes()
        info["size"] = len(data)
        info["signature"] = data[:8] == b"\x89PNG\r\n\x1a\n"
        if len(data) >= 29:
            import struct as st

            w, h, bit, ctype, _, _, _ = st.unpack(">IIBBBBB", data[16:29])
            info.update(width=w, height=h, bit_depth=bit, color_type=ctype)
    return info


def main():
    root = Path(sys.argv[1]).resolve()
    out = root / "evidence/stop-timeline.jsonl"
    native_config = json.loads((Path.home() / ".config/apricus/config.json").read_text())
    source = (native_config.get("data_source") or {}).get("Default", "")
    import hashlib
    import os

    digest = hashlib.sha1(os.path.abspath(source).encode()).hexdigest()[:16]
    expected = Path.home() / ".config/apricus" / f"static-{digest}.png"
    records = []
    records.append(snapshot("before-stop"))
    records[-1]["expected_static"] = str(expected)
    records[-1]["expected_png"] = png_info(expected)
    records[-1]["selected_source"] = source
    records[-1]["is_stopped_before"] = native_config.get("is_stopped")

    t0 = time.monotonic_ns()
    reply = subprocess.check_output(
        [
            "gdbus",
            "call",
            "--session",
            "--dest",
            "io.github.fabx_dev.Apricus.server",
            "--object-path",
            "/io/github/fabx_dev/Apricus/server",
            "--method",
            "io.github.fabx_dev.apricus.server.stop_playback",
        ],
        text=True,
        timeout=15,
    ).strip()
    records.append(snapshot("immediately-after-dbus-reply"))
    records[-1]["stop_reply"] = reply
    records[-1]["stop_roundtrip_ms"] = (time.monotonic_ns() - t0) / 1e6

    # Poll for 5s: player death vs URI application order.
    for i in range(50):
        time.sleep(0.1)
        row = snapshot(f"+{(i + 1) * 100}ms")
        alive = []
        for p in Path("/proc").glob("[0-9]*"):
            try:
                if (p / "comm").read_text().strip().startswith("apricus-player"):
                    alive.append(int(p.name))
            except OSError:
                pass
        row["player_pids"] = alive
        row["expected_png"] = png_info(expected)
        records.append(row)
        if i in (0, 4, 9, 19, 49):
            row["marker"] = True
    with out.open("w") as f:
        for row in records:
            f.write(json.dumps(row) + "\n")
    print(json.dumps(records[0], indent=2))
    print(json.dumps(records[1], indent=2))
    print(json.dumps(records[-1], indent=2))
    print(f"Timeline rows: {len(records)} -> {out}")


if __name__ == "__main__":
    main()
