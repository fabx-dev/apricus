# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline VLC decode diagnostic: dummy interface/output, no Apricus/window.

Not a reproduction of the embedded renderer. Retain complete -vvv stderr and
syscall traces; never change production options, Snap or LibVLC.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

from diagnose_dconf import snapshot
from launcher import WORKTREE


def main():
    os.umask(0o077)
    root = Path(tempfile.mkdtemp(prefix="m1-codec-", dir=WORKTREE / "build"))
    before = snapshot()
    media = Path.home() / "Video/Apricus/Hawaii-Ocean-2K-AVC.mov"
    args = [
        "/usr/bin/vlc",
        "-I",
        "dummy",
        "--vout=dummy",
        "--no-audio",
        "--no-media-library",
        "--no-video-title-show",
        "--no-disable-screensaver",
        "--play-and-exit",
        "--stop-time=3",
        "-vvv",
        str(media),
    ]
    results = {}
    for label in ("host", "guarded"):
        profile = root / label
        for part in ("config", "cache", "data", "tmp"):
            (profile / part).mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update(
            XDG_CACHE_HOME=str(profile / "cache"),
            XDG_DATA_HOME=str(profile / "data"),
            TMPDIR=str(profile / "tmp"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        command = [*args[:1], f"--config={profile / 'config/vlcrc'}", *args[1:]]
        if label == "guarded":
            code = (
                f"import os,sys; sys.path.insert(0,{str(Path(__file__).parent.resolve())!r}); "
                f"from landlock_guard import restrict_writes; restrict_writes(({str(profile)!r},)); "
                f"os.execv({command[0]!r},{command!r})"
            )
            command = ["/usr/bin/python3", "-c", code]
        command = [
            "strace",
            "-f",
            "-yy",
            "-s",
            "256",
            "-o",
            str(root / f"{label}.trace"),
            "-e",
            "trace=openat,memfd_create,ftruncate,mmap,shmget,shmat",
            *command,
        ]
        with (
            (profile / "vlc.stdout").open("w") as stdout,
            (profile / "vlc.stderr").open("w") as stderr,
        ):
            result = subprocess.run(
                command, env=env, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, timeout=40
            )
        results[label] = dict(returncode=result.returncode, command=command)
    after = snapshot()
    (root / "report.json").write_text(
        json.dumps(
            dict(
                before=before,
                after=after,
                host_state_unchanged=before == after,
                results=results,
                no_apricus_launched=True,
                video_output="dummy",
                embedded_renderer_tested=False,
            ),
            indent=2,
        )
    )
    print(root)
    print("Host/Snap/settings/protected files unchanged:", before == after)
    for label in results:
        print(label, results[label]["returncode"])


if __name__ == "__main__":
    main()
