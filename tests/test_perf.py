"""Tests for hidamari.perf (no display required)."""

import os
import subprocess
import sys
import time

from hidamari import perf
from hidamari.commons import CONFIG_DIR
from hidamari.player.video_player import VideoPlayer


def test_start_time_is_process_start():
    assert isinstance(perf.start_time(), float)
    assert perf.start_time() <= time.monotonic()
    assert perf.elapsed() >= 0


def test_t0_propagates_via_env_to_children():
    # Children must share the login-timescale clock, not restart it.
    t0 = time.monotonic() - 42.0
    code = (
        "import os, time; "
        "from hidamari import perf; "
        "print(repr(perf.start_time())); "
        "print(repr(perf.elapsed()))"
    )
    env = dict(os.environ, HIDAMARI_T0=repr(t0), PYTHONPATH="src")
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
    ).stdout.split()
    assert abs(float(out[0]) - t0) < 0.001
    assert abs(float(out[1]) - 42.0) < 5.0


def test_static_cache_path_is_stable_and_distinct():
    a = VideoPlayer._static_cache_path("/v/a.mp4")
    assert a == VideoPlayer._static_cache_path("/v/a.mp4")
    assert a != VideoPlayer._static_cache_path("/v/b.mp4")
    assert a.startswith(CONFIG_DIR) and a.endswith(".png")
