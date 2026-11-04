"""Native HTTP pause continuity with real PlayerWindow inside strict Snap.

Run with the worktree src first in PYTHONPATH and GDK_BACKEND=x11, through
the installed app's command chain, with the packaged TCP fallback preloaded.
Never publishes IPC or writes preferences. Not installed-app acceptance.
"""

import json
import os
import threading
import time

import gi
import vlc

os.environ["GDK_BACKEND"] = "x11"
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

from apricus.commons import MODE_STREAM
from apricus.player.video_player import PlayerWindow
from apricus.yt_utils import get_best_audio, get_formats, get_optimal_video


def emit(**values):
    print(json.dumps({"mono": round(time.monotonic(), 3), **values}), flush=True)


def main_call(fn):
    done = threading.Event()
    box = []

    def invoke():
        try:
            box.append(fn())
        except Exception as exc:
            box.append(exc)
        finally:
            done.set()
        return False

    GLib.idle_add(invoke)
    if not done.wait(20):
        raise RuntimeError("main loop deadline exceeded")
    if isinstance(box[0], Exception):
        raise box[0]
    return box[0]


def until(predicate, deadline=15):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if main_call(predicate):
            return
        time.sleep(0.025)
    raise RuntimeError("observation deadline exceeded")


window = PlayerWindow("stream-resume-probe", 640, 360, mode=MODE_STREAM)
window.show_all()
player = window._PlayerWindow__vlc_widget.player


def displayed_frames():
    media = player.get_media()
    try:
        stats = vlc.MediaStats()
        return stats.displayed_pictures if media.get_stats(stats) else -1
    finally:
        media.release()


def media_pointer():
    media = player.get_media()
    try:
        return media._as_parameter_.value
    finally:
        media.release()


def run():
    passed = False
    try:
        formats = get_formats("https://youtu.be/Z4F3AXvrLKo")
        video, width, height = get_optimal_video(formats, 1080)
        audio = get_best_audio(formats)

        def setup():
            media = window.media_new(video)
            media.add_option("input-repeat=65535")
            window.set_media(media)
            window.add_audio_track(audio)
            window.centercrop(width, height)
            window.play_fade(50, 1.5, 0.1)

        initial = time.monotonic()
        main_call(setup)
        until(lambda: window.is_playing() and displayed_frames() > 0)
        emit(initial_first_display_sec=round(time.monotonic() - initial, 3))
        until(lambda: window.is_playing() and player.get_time() >= 2000)
        hold = float(os.environ.get("APRICUS_PROBE_HOLD_SECONDS", "0.5"))
        for cycle in range(int(os.environ.get("APRICUS_PROBE_CYCLES", "6"))):
            assert main_call(player.can_pause), "input is not genuinely pausable"
            identity = main_call(media_pointer)
            t_pause = time.monotonic()
            main_call(window.pause)
            until(lambda: not window.is_playing())
            pause_latency = round(time.monotonic() - t_pause, 3)
            assert main_call(player.get_state) == vlc.State.Paused
            assert not getattr(window, "_stream_restart", False), "recreation is not continuity"
            saved = main_call(player.get_time)
            # Intentional test hold, not a recovery delay in production.
            time.sleep(hold)
            assert main_call(player.get_time) == saved, "time advanced while paused"
            frames = main_call(displayed_frames)
            t0 = time.monotonic()
            main_call(lambda: window.play_fade(50, 1.5, 0.1))
            until(lambda frames=frames: window.is_playing() and displayed_frames() > frames)
            assert main_call(media_pointer) == identity, "media was replaced"
            actual = main_call(player.get_time)
            length = main_call(player.get_length)
            # VLC's get_time()/displayed counter are NOT a content oracle:
            # counters may include repeated paused pictures and time can lag
            # decoded output near EOF. Pixel continuity is tested separately
            # by snap_youtube_frame_continuity.py, never inferred here.
            emit(
                cycle=cycle + 1,
                pause_latency_sec=pause_latency,
                elapsed=round(time.monotonic() - t0, 3),
                saved_ms=saved,
                actual_ms=actual,
                declared_length_ms=length,
                hold_sec=hold,
                media_reused=True,
                error=window.playback_error(),
                displayed_frames=main_call(displayed_frames),
            )
        passed = True
    except Exception as exc:
        emit(failure=type(exc).__name__, message=str(exc))
    finally:
        emit(result="PASS" if passed else "FAIL", scope="PlayerWindow, not installed app")
        main_call(window.stop)
        main_call(lambda: (window.destroy(), Gtk.main_quit()))


threading.Thread(target=run, daemon=True).start()
Gtk.main()
