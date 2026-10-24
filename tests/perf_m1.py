#!/usr/bin/env python3
# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in real M1 benchmark. Run ONLY inside private Xvfb + dbus-run-session.

Refuses to run without an isolation marker. Uses a generated local silent clip,
software rendering and private config/cache/Pulse addresses; no host GNOME I/O.
JSON results are written to the isolated config parent, not the repository.
"""

import copy
import functools
import http.server
import json
import os
import pathlib
import statistics
import subprocess
import sys
import threading
import time
from types import SimpleNamespace


class QuietHTTP(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def copyfile(self, source, outputfile):
        try:
            super().copyfile(source, outputfile)
        except (BrokenPipeError, ConnectionResetError):
            pass  # Expected: STOP tears down the HTTP media connection.


def video_target():
    # Stream fixture: real HTTP + VLC, deterministic resolver. This does NOT
    # validate remote yt-dlp/authentication or weaken production URL checks.
    os.setsid()
    from apricus.player import video_player as video
    from apricus.utils import ConfigUtil

    if ConfigUtil().load()["mode"] == "MODE_STREAM":
        video.get_formats = lambda _: []
        video.get_optimal_video = lambda *_: (os.environ["M1_STREAM_URL"], 1280, 720)
        video.get_best_audio = lambda _: os.environ["M1_STREAM_URL"]
    video.main()


def tree(pid):
    result = {pid}
    changed = True
    while changed:
        changed = False
        for path in pathlib.Path("/proc").glob("[0-9]*/status"):
            try:
                fields = dict(line.split(":", 1) for line in path.read_text().splitlines())
                child = int(path.parent.name)
                if int(fields["PPid"]) in result and child not in result:
                    result.add(child)
                    changed = True
            except (OSError, ValueError, KeyError):
                pass
    return result


def snapshot(pid):
    totals = dict(rss_kib=0, pss_kib=0, vsz_kib=0, threads=0, cpu_ticks=0, processes=0)
    for child in tree(pid):
        try:
            root = pathlib.Path(f"/proc/{child}")
            fields = dict(line.split(":", 1) for line in (root / "status").read_text().splitlines())
            stat = (root / "stat").read_text().rsplit(")", 1)[1].split()
            totals["cpu_ticks"] += int(stat[11]) + int(stat[12])
            totals["rss_kib"] += int(fields.get("VmRSS", "0 kB").split()[0])
            totals["vsz_kib"] += int(fields.get("VmSize", "0 kB").split()[0])
            totals["threads"] += int(fields["Threads"])
            totals["processes"] += 1
            try:
                rollup = dict(
                    line.split(":", 1)
                    for line in (root / "smaps_rollup").read_text().splitlines()
                    if ":" in line
                )
                totals["pss_kib"] += int(rollup["Pss"].split()[0])
            except (OSError, KeyError):
                totals["pss_unavailable"] = True
        except (OSError, ValueError):
            pass
    return totals


def sample(pid):
    a = snapshot(pid)
    start = time.monotonic()
    time.sleep(0.3)
    b = snapshot(pid)
    b["cpu_percent_one_core"] = (
        100
        * (b["cpu_ticks"] - a["cpu_ticks"])
        / os.sysconf("SC_CLK_TCK")
        / (time.monotonic() - start)
    )
    return b


def wait_for(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if predicate():
                return
        except Exception:
            pass
        time.sleep(0.05)
    raise RuntimeError("Timed out waiting for player")


def main():
    assert os.environ.get("APRICUS_M1_ISOLATED") == "1", "Private Xvfb + bus required"
    assert os.environ.get("XDG_CURRENT_DESKTOP") == "M1_TEST", "Do not use host GNOME"
    from pydbus import SessionBus

    from apricus import server
    from apricus.commons import CONFIG_TEMPLATE, DBUS_NAME_PLAYER, DBUS_NAME_SERVER
    from apricus.utils import ConfigUtil

    if "--server" in sys.argv:
        sys.argv[:] = [sys.argv[0]]
        server._video_player_target = video_target
        server.main("m1-test", "", "", SimpleNamespace(p=0, reset=False, background=True))
        return

    root = pathlib.Path(os.environ["XDG_CONFIG_HOME"]).parent
    clip = root / "clip.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1280x720:rate=30",
            "-t",
            "8",
            "-c:v",
            "mpeg4",
            "-q:v",
            "5",
            str(clip),
        ],
        check=True,
        timeout=60,
    )
    config = copy.deepcopy(CONFIG_TEMPLATE)
    config.update(
        mode="MODE_VIDEO",
        data_source={"Default": str(clip)},
        playlist={"Default": [str(clip), str(clip)]},
        is_static_wallpaper=False,
        is_mute=True,
        is_pause_when_maximized=False,
    )
    ConfigUtil().save(config)
    httpd = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHTTP, directory=root)
    )
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    os.environ["M1_STREAM_URL"] = f"http://127.0.0.1:{httpd.server_port}/clip.mp4"
    log = open(root / "server.log", "w")
    proc = subprocess.Popen([sys.executable, __file__, "--server"], stdout=log, stderr=log)
    bus = SessionBus()
    results = []
    try:

        def ready():
            nonlocal proxy
            proxy = bus.get(DBUS_NAME_SERVER)
            return proxy.playback_state == "playing"

        proxy = None
        wait_for(ready)
        proxy.is_paused_by_user = True
        proxy.pause_playback()
        wait_for(lambda: proxy.playback_state == "paused")
        paused = sample(proc.pid)
        proxy.is_paused_by_user = False
        proxy.start_playback()
        wait_for(lambda: proxy.playback_state == "playing")
        for index in range(50):
            mode = "MODE_VIDEO" if index < 25 else "MODE_PLAYLIST"
            if index == 25:
                proxy.playlist([str(clip), str(clip)], "all")
                wait_for(lambda: proxy.playback_state == "playing")
            before = sample(proc.pid)
            old_pid = bus.dbus.GetConnectionUnixProcessID(DBUS_NAME_PLAYER)
            start = time.monotonic()
            proxy.stop_playback()
            stop_ms = (time.monotonic() - start) * 1000
            assert proxy.playback_state == "stopped"
            proxy.stop_playback()
            stopped = sample(proc.pid)
            assert not bus.dbus.NameHasOwner(DBUS_NAME_PLAYER)
            assert not pathlib.Path(f"/proc/{old_pid}").exists()
            proxy.start_playback()  # must NOT clear manual STOP
            assert proxy.playback_state == "stopped"
            start = time.monotonic()
            proxy.resume_playback()
            proxy.resume_playback()
            wait_for(lambda: proxy.playback_state == "playing")
            assert bus.dbus.GetConnectionUnixProcessID(DBUS_NAME_PLAYER) != old_pid
            resume_ms = (time.monotonic() - start) * 1000
            resumed = sample(proc.pid)
            results.append(
                dict(
                    cycle=index + 1,
                    mode=mode,
                    before=before,
                    stopped=stopped,
                    resumed=resumed,
                    stop_ms=stop_ms,
                    resume_ms=resume_ms,
                )
            )
            print(f"cycle {index + 1}: stop={stop_ms:.1f}ms resume={resume_ms:.1f}ms", flush=True)
        extra = []
        (root / "results.json").write_text(
            json.dumps(dict(cycles=results, paused=paused), indent=2)
        )
        page = root / "page.html"
        page.write_text("<html><body><h1>M1 isolated web fixture</h1></body></html>")
        for mode, source, apply in (
            ("web", str(page), proxy.webpage),
            ("stream-http-fixture", os.environ["M1_STREAM_URL"], proxy.stream),
        ):
            proxy.stop_playback()
            apply(source, "all")
            assert proxy.playback_state == "stopped"
            proxy.resume_playback()
            wait_for(lambda: proxy.playback_state == "playing")
            old_pid = bus.dbus.GetConnectionUnixProcessID(DBUS_NAME_PLAYER)
            descendants = tree(old_pid)
            proxy.stop_playback()
            wait_for(
                lambda pids=descendants: all(not pathlib.Path(f"/proc/{p}").exists() for p in pids)
            )
            extra.append(dict(mode=mode, stopped_pids=sorted(descendants)))
        proxy.show_gui()
        gui_name = "io.github.fabx_dev.Apricus.gui4"
        wait_for(lambda: bus.dbus.NameHasOwner(gui_name))
        gui_pid = bus.dbus.GetConnectionUnixProcessID(gui_name)
        proxy.resume_playback()
        wait_for(lambda: proxy.playback_state == "playing")
        proxy.stop_playback()
        assert bus.dbus.GetConnectionUnixProcessID(gui_name) == gui_pid
        # Restart of the SERVER must preserve STOP (no player bus owner).
        proxy.quit()
        proc.wait(timeout=10)
        proc = subprocess.Popen([sys.executable, __file__, "--server"], stdout=log, stderr=log)
        wait_for(lambda: bus.get(DBUS_NAME_SERVER).playback_state == "stopped")
        assert not bus.dbus.NameHasOwner(DBUS_NAME_PLAYER)
        data = dict(
            cycles=results,
            paused=paused,
            extra_modes=extra,
            gui_survived_stop=True,
            restart_stayed_stopped=True,
            note="Xvfb; real local VLC video/playlist; no GNOME or real confined package",
        )
        (root / "results.json").write_text(json.dumps(data, indent=2))
        for phase in ("before", "stopped", "resumed"):
            print(
                phase,
                {
                    key: round(statistics.mean(r[phase][key] for r in results), 2)
                    for key in results[0][phase]
                },
            )
    finally:
        try:
            bus.get(DBUS_NAME_SERVER).quit()
        except Exception:
            proc.terminate()
        proc.wait(timeout=10)
        log.close()
        httpd.shutdown()


if __name__ == "__main__":
    main()
