"""Compare paused/first-new decoded pixels against the same YouTube reference.

Run in strict Snap with the TCP fallback preloaded and worktree PYTHONPATH.
No app IPC/config writes or desktop capture. Downloads the fixed 15 s reference
once (max 16 MiB). Deadlines and pause holds are diagnostic, not production.
"""

import ctypes
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

import requests
import vlc

from apricus.commons import MODE_STREAM
from apricus.player.video_player import PlayerWindow
from apricus.yt_utils import get_best_audio, get_formats, get_optimal_video

WIDTH, HEIGHT = 128, 72
POINTS = [(y * WIDTH + x) for y in range(4, HEIGHT, 8) for x in range(4, WIDTH, 8)]


def emit(**values):
    print(json.dumps(values), flush=True)


def sample(pixels, stride):
    order = (2, 1, 0) if stride == 4 else (0, 1, 2)
    return bytes(pixels[p * stride + channel] for p in POINTS for channel in order)


def until(predicate, seconds=20):
    deadline = time.monotonic() + seconds
    while not predicate():
        if time.monotonic() >= deadline:
            raise RuntimeError("observation deadline")
        time.sleep(0.01)


class Transport:
    play = PlayerWindow.play
    pause = PlayerWindow.pause
    is_playing = PlayerWindow.is_playing
    fade = SimpleNamespace(cancel=lambda: None)


def pixel_continuity(before, after, count):
    # Reference scaling can match neighbouring source frames. A prior
    # keyframe/replayed prefix must fail even if time later reaches the target.
    return (after - before) % count in (0, 1, 2, 3)


def observe_picture(state, pixels):
    if pixels != state["last"]:
        state["unique"] += 1
        state["last"] = pixels
    if state["waiting"] and state["first"] is None and pixels != state["paused"]:
        state["first"] = pixels


def main():
    formats = get_formats("https://youtu.be/Z4F3AXvrLKo")
    video, _, _ = get_optimal_video(formats, 1080)
    audio = get_best_audio(formats)
    directory = os.environ.get("SNAP_USER_COMMON") or "/tmp/opencode"
    with tempfile.TemporaryDirectory(prefix="apricus-frame-reference-", dir=directory) as tmp:
        path = Path(tmp) / "reference.mp4"
        with requests.get(video, stream=True, timeout=(5, 15)) as response, path.open("wb") as out:
            response.raise_for_status()
            count = 0
            for chunk in response.iter_content(65536):
                count += len(chunk)
                if count > 16 * 1024 * 1024:
                    raise RuntimeError("reference exceeded diagnostic size limit")
                out.write(chunk)
        decoded = subprocess.check_output(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(path),
                "-an",
                "-vf",
                f"scale={WIDTH}:{HEIGHT}",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ],
            timeout=30,
        )
        size = WIDTH * HEIGHT * 3
        references = [sample(decoded[i : i + size], 3) for i in range(0, len(decoded), size)]
        assert 1 < len(references) < 1000

        instance = vlc.Instance("--no-interact", "--aout=pulse", "--no-disable-screensaver")
        player = instance.media_player_new()
        buffers = {}
        state = {"last": None, "unique": 0, "first": None, "waiting": False, "paused": None}

        @vlc.CallbackDecorators.VideoLockCb
        def lock(_opaque, planes):
            buffer = ctypes.create_string_buffer(WIDTH * HEIGHT * 4)
            pointer = ctypes.addressof(buffer)
            buffers[pointer] = buffer
            planes[0] = pointer
            return pointer

        @vlc.CallbackDecorators.VideoDisplayCb
        def display(_opaque, picture):
            pixels = sample(buffers.pop(picture).raw, 4)
            observe_picture(state, pixels)

        player.video_set_callbacks(lock, None, display, None)
        player.video_set_format("RV32", WIDTH, HEIGHT, WIDTH * 4)
        window = Transport()
        window._mode = MODE_STREAM
        window._stream_restart = False
        window._stream_resume_time = None
        window._PlayerWindow__vlc_widget = SimpleNamespace(player=player)
        captures = []
        media = instance.media_new(video)
        media.add_option("input-repeat=65535")
        player.set_media(media)
        player.add_slave(vlc.MediaSlaveType(1), audio, True)
        try:
            window.play()
            cycles = int(os.environ.get("APRICUS_PROBE_CYCLES", "6"))
            hold = float(os.environ.get("APRICUS_PROBE_HOLD_SECONDS", "0.5"))
            for cycle in range(1, cycles + 1):
                until(lambda cycle=cycle: state["unique"] >= cycle * 90)
                assert player.can_pause(), "not a pausable input"
                window.pause()
                until(lambda: player.get_state() == vlc.State.Paused)
                unique_at_pause = state["unique"]
                time.sleep(hold)  # intentional pause, includes any in-flight display completion
                assert state["unique"] <= unique_at_pause + 1, (
                    "decoded content advanced during pause"
                )
                state["paused"] = state["last"]
                state["first"] = None
                state["waiting"] = True
                saved = player.get_time()
                start = time.monotonic()
                window.play()
                until(lambda: state["first"] is not None)
                elapsed = time.monotonic() - start
                state["waiting"] = False
                captures.append(
                    (state["paused"], state["first"], elapsed, saved, player.get_time())
                )
                assert not window._stream_restart, "input recreation is not continuity"
        finally:
            player.stop()
            player.release()
            media.release()
            instance.release()

        def closest(pixels):
            distances = [
                sum(abs(a - b) for a, b in zip(pixels, ref, strict=True)) for ref in references
            ]
            index = min(range(len(distances)), key=distances.__getitem__)
            return index, distances[index]

        passed = True
        for cycle, (paused, resumed, elapsed, before_ms, after_ms) in enumerate(captures, 1):
            before, error_before = closest(paused)
            after, error_after = closest(resumed)
            delta = (after - before) % len(references)
            continuous = pixel_continuity(before, after, len(references))
            passed = passed and continuous
            emit(
                cycle=cycle,
                paused_reference_frame=before,
                first_new_reference_frame=after,
                frame_delta=delta,
                reference_matching="approximate scaled RGB; exact marker oracle separate",
                rgb_distance_before=error_before,
                rgb_distance_after=error_after,
                resume_new_picture_sec=round(elapsed, 3),
                clock_before_ms=before_ms,
                clock_after_ms=after_ms,
                continuity=continuous,
            )
        emit(
            result="PASS" if passed else "FAIL",
            reference_frames=len(references),
            scope="decoded content, standalone real VLC; installed-app acceptance pending",
        )
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
