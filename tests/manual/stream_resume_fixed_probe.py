"""Real updated PlayerWindow inside strict Snap; not installed-app acceptance.

Run with the worktree src first in PYTHONPATH and GDK_BACKEND=x11, through
the installed app's command chain. Never publishes IPC or writes preferences.
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
    print(json.dumps(values), flush=True)


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
        for cycle in range(6):
            main_call(lambda: window.pause_fade(1.5, 0.1))
            until(lambda: not window.fade.is_active and not window.is_playing())
            saved = main_call(lambda: window._stream_resume_time)
            t0 = time.monotonic()

            def resume_at_start(saved=saved):
                media = window.media_new(video)
                media.add_option("input-repeat=65535")
                if saved is not None:
                    media.add_option(f"start-time={saved / 1000:.3f}")
                window.set_media(media)
                window.add_audio_track(audio)
                window.play_fade(50, 1.5, 0.1)

            if os.environ.get("APRICUS_PROBE_START_TIME"):
                main_call(resume_at_start)
            else:
                main_call(lambda: window.play_fade(50, 1.5, 0.1))
            until(lambda: window.is_playing() and window._stream_resume_time is None)
            frames = main_call(displayed_frames)
            until(
                lambda saved=saved, frames=frames: (
                    window.is_playing()
                    and window._stream_resume_time is None
                    and player.get_time() >= (saved or 0) + 250
                    and displayed_frames() > frames
                )
            )
            emit(
                cycle=cycle + 1,
                elapsed=round(time.monotonic() - t0, 3),
                saved_ms=saved,
                actual_ms=main_call(player.get_time),
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
