# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Test-only Linux filesystem write boundary, inherited across fork and exec.

No network/IPC restriction: the real GNOME session must remain visible.
ABI >= 3 is mandatory (TRUNCATE and REFER must be handled). No silent fallback.
Landlock does not mediate chmod/utime/xattr or writes via already-open FDs;
the adapter separately rejects Python metadata writes and inherited regular
write FDs outside the private profile/evidence directories.
"""

import ctypes
import os
import platform

WRITE = 1 << 1
TRUNCATE = 1 << 14
HANDLED = WRITE | sum(1 << bit for bit in range(4, 15))


class Ruleset(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class PathBeneath(ctypes.Structure):
    _pack_ = 1
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int32)]


def restrict_writes(roots):
    # Numbers checked against asm/unistd_64.h on the test host. Do not guess
    # syscall numbers on another architecture.
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise RuntimeError("This test guard is implemented/checked only on Linux x86_64")
    libc = ctypes.CDLL(None, use_errno=True)

    def call(number, *args):
        result = libc.syscall(number, *args)
        if result < 0:
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        return result

    abi = call(444, ctypes.c_void_p(), ctypes.c_size_t(0), ctypes.c_uint(1))
    if abi < 3:
        raise RuntimeError(f"Landlock ABI {abi}: require >= 3")
    attr = Ruleset(HANDLED)
    ruleset = call(444, ctypes.byref(attr), ctypes.sizeof(attr), 0)
    try:
        entries = [(root, HANDLED) for root in roots]
        # Device I/O, not persistent application files. Do not allow /run,
        # /tmp, HOME, /dev/shm or any host config/cache tree for filesystem writes.
        entries.append(("/dev/null", WRITE))
        for device_dir in ("/dev/dri", "/dev/snd"):
            if os.path.isdir(device_dir):
                entries.append((device_dir, WRITE))
        for path, rights in entries:
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = PathBeneath(rights, fd)
                call(445, ruleset, 1, ctypes.byref(rule), 0)
            finally:
                os.close(fd)
        if libc.prctl(38, 1, 0, 0, 0):  # PR_SET_NO_NEW_PRIVS, this process only
            error = ctypes.get_errno()
            raise OSError(error, os.strerror(error))
        call(446, ruleset, 0)
    finally:
        os.close(ruleset)
    return abi
