"""Sidecar cache for ffprobe results (Fase 1b).

Probing every video on every startup blocks first frame on subprocesses.
Results are cached in CONFIG_DIR/video-info.json keyed by absolute path and
validated against mtime+size, so warm starts (the login case) cost a stat()
instead of two ffprobe runs. Never raises: failures yield None fields.
"""

import json
import logging
import os
import subprocess

from hidamari import commons
from hidamari.commons import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

CACHE_FILENAME = "video-info.json"
FFPROBE_TIMEOUT = 20


def _cache_file():
    return os.path.join(commons.CONFIG_DIR, CACHE_FILENAME)


def _load():
    try:
        with open(_cache_file(), encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(cache):
    try:
        os.makedirs(commons.CONFIG_DIR, exist_ok=True)
        tmp = _cache_file() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f)
        os.replace(tmp, _cache_file())
    except OSError as e:
        logger.warning(f"[MediaCache] save failed: {e}")


def probe_video(video_path):
    """Return (width, height, duration) for a video file.

    Width/height are ints or None, duration is float seconds or None.
    Cached hits avoid ffprobe entirely; misses run a single ffprobe call
    for both stream dims and format duration.
    """
    try:
        abspath = os.path.abspath(video_path)
        st = os.stat(abspath)
    except OSError:
        return None, None, None
    fingerprint = {"mtime": st.st_mtime, "size": st.st_size}

    cache = _load()
    entry = cache.get(abspath)
    if (
        isinstance(entry, dict)
        and entry.get("mtime") == fingerprint["mtime"]
        and entry.get("size") == fingerprint["size"]
    ):
        return entry.get("width"), entry.get("height"), entry.get("duration")

    width, height, duration = None, None, None
    try:
        out = subprocess.check_output(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height:format=duration",
                "-of",
                "json",
                abspath,
            ],
            shell=False,
            encoding="utf-8",
            timeout=FFPROBE_TIMEOUT,
        )
        info = json.loads(out)
        streams = info.get("streams") or [{}]
        width = streams[0].get("width")
        height = streams[0].get("height")
        duration = info.get("format", {}).get("duration")
        duration = float(duration) if duration is not None else None
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError) as e:
        logger.warning(f"[MediaCache] ffprobe failed for {abspath}: {e}")

    cache[abspath] = {
        "mtime": fingerprint["mtime"],
        "size": fingerprint["size"],
        "width": width,
        "height": height,
        "duration": duration,
    }
    _save(cache)
    return width, height, duration
