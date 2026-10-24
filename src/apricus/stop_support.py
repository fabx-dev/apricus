# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""STOP lifecycle helpers. No GTK imports, bus names or background services."""

import base64
import hashlib
import os
import signal

from apricus.commons import CONFIG_DIR, MODE_PLAYLIST, MODE_WEBPAGE


def static_cache_path(source, web=False):
    if web:
        digest = hashlib.sha256((source or "").encode()).hexdigest()[:16]
    else:
        digest = hashlib.sha1(os.path.abspath(source).encode()).hexdigest()[:16]
    return os.path.join(CONFIG_DIR, f"static-{digest}.png")


def stopped_background(config):
    """Cache-only: never launch a decoder or fetch a network resource in STOP.

    Match the existing playlist first-item and WebKit snapshot cache conventions.
    A cache miss uses a neutral PNG, not a frame of a different wallpaper.
    Caller must check is_static_wallpaper before applying this image.
    """
    source = (config.get("data_source") or {}).get("Default", "")
    if config.get("mode") == MODE_PLAYLIST:
        queues = config.get("playlist") or {}
        source = next(
            (q[0] for q in [queues.get("Default"), *queues.values()] if isinstance(q, list) and q),
            "",
        )
    source = source or ""
    cached = static_cache_path(source, web=config.get("mode") == MODE_WEBPAGE)
    if source and os.path.isfile(cached):
        return cached
    os.makedirs(CONFIG_DIR, exist_ok=True)
    fallback = os.path.join(CONFIG_DIR, "stopped-background.png")
    if not os.path.isfile(fallback):
        # 1x1 opaque neutral PNG; no GDK dependency in the server/forkserver.
        data = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGPQ0dEBAAEMAIW1"
            "C8ikAAAAAElFTkSuQmCC"
        )
        with open(fallback + ".tmp", "wb") as stream:
            stream.write(data)
        os.replace(fallback + ".tmp", fallback)
    return fallback


def finish_process(process, group=None):
    """Reap only our multiprocessing child and terminate its dedicated session.

    The child trampoline calls setsid before importing VLC/WebKit or spawning
    FFmpeg. Never signal Apricus/GNOME/VLC by name or touch the user's session.
    A child that has not reached setsid yet is terminated individually.
    """
    if process is None:
        return
    if group is None:
        try:
            if os.getpgid(process.pid) == process.pid:
                group = process.pid
        except ProcessLookupError:
            pass

    def send(sig):
        if group and group != os.getpgrp():
            try:
                os.killpg(group, sig)
            except ProcessLookupError:
                pass
        elif process.is_alive():
            if sig == signal.SIGKILL:
                process.kill()
            else:
                process.terminate()

    send(signal.SIGTERM)
    process.join(timeout=2)
    # Descendants can outlive the main child (e.g. WebKit network process).
    send(signal.SIGKILL)
    process.join(timeout=2)
    if process.is_alive():
        raise RuntimeError("Wallpaper player did not stop")
