"""Bounded LibVLC comparison; run inside the installed Snap, not a release test.

No Apricus IPC/config writes, no signed URLs in output. One resolution per run.
Polling deadlines below are observation limits, never production retry delays.
"""

import json
import os
import threading
import time

import gi

os.environ["GDK_BACKEND"] = "x11"
gi.require_version("Gtk", "3.0")
import vlc
from gi.repository import Gtk

from apricus.player.video_player import _vlc_options
from apricus.yt_utils import get_best_audio, get_formats, get_optimal_video


def emit(**values):
    print(json.dumps(values), flush=True)


formats = get_formats("https://youtu.be/Z4F3AXvrLKo")
video, _, _ = get_optimal_video(formats, 1080)
audio = get_best_audio(formats)
prefix = "" if os.environ.get("APRICUS_PROBE_NATIVE_HTTP") else "avio://"
instance = vlc.Instance(_vlc_options(False, None))
player = instance.media_player_new()
window = Gtk.Window()
area = Gtk.DrawingArea()
window.add(area)
window.set_default_size(640, 360)
area.connect("realize", lambda w: player.set_xwindow(w.get_window().get_xid()))
window.show_all()


def wait_state(state, limit=15):
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        if player.get_state() == state:
            return True
        time.sleep(0.05)
    return False


def new_media():
    media = instance.media_new(prefix + video)
    media.add_option("input-repeat=65535")
    return media


def run():
    try:
        emit(vlc=vlc.libvlc_get_version().decode())
        methods = (
            ("play",)
            if not prefix
            else ("play", "set_pause", "stop_play", "set_media_only", "new_media")
        )
        for method in methods:
            player.stop()
            media = new_media()
            player.set_media(media)
            player.add_slave(vlc.MediaSlaveType(1), prefix + audio, True)
            player.play()
            if not wait_state(vlc.State.Playing):
                emit(method=method, initial=str(player.get_state()))
                continue
            # Require advancing media time, not just a Playing notification.
            start = player.get_time()
            deadline = time.monotonic() + 10
            while player.get_time() <= start and time.monotonic() < deadline:
                time.sleep(0.05)
            emit(
                method=method,
                before=str(player.get_state()),
                can_pause=player.can_pause(),
                seekable=player.is_seekable(),
                media_time=player.get_time(),
                length=player.get_length(),
            )
            player.pause()
            paused = wait_state(vlc.State.Paused, 5)
            paused_time = player.get_time()
            t0 = time.monotonic()
            if method == "play":
                result = player.play()
            elif method == "set_pause":
                result = player.set_pause(0)
            elif method == "stop_play":
                player.stop()
                result = player.play()
            elif method == "set_media_only":
                player.set_media(media)
                result = player.play()
            else:
                player.stop()
                player.set_media(new_media() if method == "new_media" else media)
                if method != "same_media_no_slave":
                    player.add_slave(vlc.MediaSlaveType(1), prefix + audio, True)
                result = player.play()
            resumed = wait_state(vlc.State.Playing)
            stamp = player.get_time()
            deadline = time.monotonic() + 5
            while resumed and player.get_time() <= stamp and time.monotonic() < deadline:
                time.sleep(0.05)
            emit(
                method=method,
                paused=paused,
                result=result,
                state=str(player.get_state()),
                advancing=player.get_time() > stamp,
                paused_time=paused_time,
                first_resumed_time=stamp,
                elapsed=round(time.monotonic() - t0, 3),
            )
            media.release()
    finally:
        player.stop()
        player.release()
        instance.release()
        from gi.repository import GLib

        GLib.idle_add(Gtk.main_quit)


threading.Thread(target=run, daemon=True).start()
Gtk.main()
