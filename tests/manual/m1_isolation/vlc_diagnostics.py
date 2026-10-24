# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Dormant helpers for a separately authorized future live run.

No import-time hooks, player startup, LibVLC import or option changes. Attach
after Instance creation and before creating/playing media. The public logging
API cannot recover messages emitted during Instance construction: retain stderr
and a syscall trace as well. This module is NOT enabled by this preflight.
"""

import ctypes
import json
import os
import time
from pathlib import Path


class NativeLog:
    """Public libvlc_log_set_file, with a real FILE* kept alive until unset.

    Call close BEFORE releasing the LibVLC Instance. No callbacks/va_list ABI,
    renderer changes, hardware overrides or diagnostic CLI flags are used.
    """

    def __init__(self, instance, directory, *, file_pointer_type=None):
        if file_pointer_type is None:
            # Import only on explicit future attachment, never during preflight.
            import vlc

            file_pointer_type = vlc.FILE_ptr
        directory = Path(directory).resolve(strict=True)
        self.libc = ctypes.CDLL(None, use_errno=True)
        self.libc.fopen.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
        self.libc.fopen.restype = ctypes.c_void_p
        self.libc.setvbuf.argtypes = [
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_size_t,
        ]
        self.libc.fclose.argtypes = [ctypes.c_void_p]
        path = directory / f"vlc-native-{os.getpid()}-{time.monotonic_ns()}.log"
        # Exclusive creation, current process umask and existing private root.
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        self.stream = self.libc.fopen(os.fsencode(path), b"a")
        if not self.stream:
            raise OSError(ctypes.get_errno(), "Cannot open native VLC log")
        self.instance = instance
        self.path = path
        try:
            if self.libc.setvbuf(self.stream, None, 2, 0):  # _IONBF on Linux/glibc
                raise RuntimeError("Cannot make diagnostic log unbuffered")
            instance.log_set_file(ctypes.cast(self.stream, file_pointer_type))
        except BaseException:
            self.libc.fclose(self.stream)
            self.stream = None
            raise

    def close(self):
        if self.stream is not None:
            self.instance.log_unset()
            self.libc.fclose(self.stream)
            self.stream = None


def snapshot(directory, *, media=None, state=None):
    """Call from the future player's own process; no settings or IPC writes."""
    directory = Path(directory).resolve(strict=True)
    record = dict(
        pid=os.getpid(),
        monotonic_ns=time.monotonic_ns(),
        state=state,
        maps=Path("/proc/self/maps").read_text(),
        status=Path("/proc/self/status").read_text(),
    )
    if media is not None:
        record["media_stats"] = repr(media.get_stats())
    path = directory / f"vlc-snapshot-{os.getpid()}-{record['monotonic_ns']}.json"
    path.write_text(json.dumps(record, indent=2))
    return path
