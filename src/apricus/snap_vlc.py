"""Snap-only compatibility for VLC 3 TCP Fast Open; no sandbox policy changes."""

import os

NATIVE_HTTP_ENV = "APRICUS_VLC_TCP_FALLBACK"
LIBRARY = "libapricus-vlc-fastopen.so"


def prepare_snap_vlc(env=None):
    """Configure fresh forkserver/player interpreters BEFORE any are started.

    The server itself does not load VLC. LD_PRELOAD is consumed by the dynamic
    loader of its fresh child interpreters; changing it after forkserver startup
    would be too late. Native/Flatpak environments are left completely alone.
    """
    env = os.environ if env is None else env
    snap = env.get("SNAP")
    if not snap:
        return False
    library = os.path.join(snap, "usr", "lib", "apricus", LIBRARY)
    if not os.path.isfile(library):
        raise RuntimeError("Snap VLC TCP compatibility library is missing; rebuild the Snap")
    preload = env.get("LD_PRELOAD", "")
    if library not in preload.replace(":", " ").split():
        env["LD_PRELOAD"] = library + (" " + preload if preload else "")
    env[NATIVE_HTTP_ENV] = "1"
    return True
