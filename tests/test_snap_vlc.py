"""VLC transport regression: preserve native pause, do not relax denials."""

import errno
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from apricus import snap_vlc
from apricus.player.video_player import PlayerWindow


def test_native_and_flatpak_environment_is_untouched():
    for env in ({}, {"FLATPAK_ID": "io.github.fabx_dev.Apricus", "LD_PRELOAD": "/other.so"}):
        original = env.copy()
        assert not snap_vlc.prepare_snap_vlc(env)
        assert env == original


def test_snap_prepares_children_before_forkserver_and_preserves_other_preloads(tmp_path):
    library = tmp_path / "usr/lib/apricus" / snap_vlc.LIBRARY
    library.parent.mkdir(parents=True)
    library.touch()
    env = {"SNAP": str(tmp_path), "LD_PRELOAD": "/existing.so:/other.so"}
    assert snap_vlc.prepare_snap_vlc(env)
    assert env["LD_PRELOAD"] == str(library) + " /existing.so:/other.so"
    assert env[snap_vlc.NATIVE_HTTP_ENV] == "1"
    assert snap_vlc.prepare_snap_vlc(env)
    assert env["LD_PRELOAD"].count(str(library)) == 1


def test_missing_snap_library_fails_loudly_instead_of_reenabling_broken_avio(tmp_path):
    env = {"SNAP": str(tmp_path)}
    with pytest.raises(RuntimeError, match="compatibility library is missing"):
        snap_vlc.prepare_snap_vlc(env)
    assert "LD_PRELOAD" not in env


def test_main_configures_compatibility_before_server_can_start_children(monkeypatch):
    from apricus import __main__ as entry

    events = []
    monkeypatch.setattr(entry, "prepare_snap_vlc", lambda: events.append("compat"))
    monkeypatch.setattr(entry.server, "main", lambda *args: events.append("server"))
    monkeypatch.setattr(sys, "argv", ["apricus", "-b"])
    monkeypatch.setenv("GDK_BACKEND", "x11")
    monkeypatch.setenv("VLC_VERBOSE", "-1")
    entry.main()
    assert events == ["compat", "server"]


@pytest.mark.parametrize("snap", [False, True])
def test_video_and_audio_keep_native_urls_and_local_files(monkeypatch, snap):
    if snap:
        monkeypatch.setenv("SNAP", "/snap/apricus/x8")
    else:
        monkeypatch.delenv("SNAP", raising=False)
    media_calls, slaves = [], []
    obj = SimpleNamespace(
        _PlayerWindow__vlc_widget=SimpleNamespace(
            instance=SimpleNamespace(media_new=lambda *args: media_calls.append(args)),
            player=SimpleNamespace(add_slave=lambda *args: slaves.append(args)),
        )
    )
    for value in ("https://cdn/video.mp4", "http://cdn/audio.m4a", "/home/user/local.mp4"):
        PlayerWindow.media_new(obj, value)
        PlayerWindow.add_audio_track(obj, value)
        assert media_calls[-1] == (value,)
        assert slaves[-1][1] == value


def test_repeated_paused_picture_is_not_resume_despite_increasing_display_counter():
    from manual.snap_youtube_frame_continuity import observe_picture

    state = {"last": b"frame90", "unique": 1, "first": None, "waiting": True, "paused": b"frame90"}
    for _ in range(450):
        observe_picture(state, b"frame90")
    assert state["first"] is None
    assert state["unique"] == 1
    observe_picture(state, b"frame91")
    assert state["first"] == b"frame91"


@pytest.mark.parametrize(
    "before,after,want", [(90, 0, False), (90, 89, False), (90, 91, True), (449, 0, True)]
)
def test_pixel_oracle_rejects_replaying_prefix_even_if_clock_echoed_seek(before, after, want):
    from manual.snap_youtube_frame_continuity import pixel_continuity

    assert pixel_continuity(before, after, 450) is want


@pytest.fixture(scope="module")
def preload_libraries(tmp_path_factory):
    compiler = shutil.which("cc")
    if not compiler:
        pytest.skip("C compiler required for syscall-compatibility regression")
    root = tmp_path_factory.mktemp("vlc-syscall")
    source = Path(__file__).resolve().parents[1] / "src/compat/vlc_fastopen.c"
    shim = root / "compat.so"
    subprocess.run(
        [
            compiler,
            "-shared",
            "-fPIC",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(source),
            "-o",
            str(shim),
            "-ldl",
        ],
        check=True,
        capture_output=True,
    )
    # Deterministic next-symbol provider: no actual connection or send is made.
    stub = root / "kernel_stub.c"
    stub.write_text("""
#include <errno.h>
#include <stdlib.h>
#include <sys/socket.h>
ssize_t sendmsg(int fd, const struct msghdr *msg, int flags) {
    (void)fd; (void)msg; (void)flags;
    const char *result = getenv("MOCK_SEND_RESULT");
    if (result) return atoi(result);
    errno = atoi(getenv("MOCK_SEND_ERRNO"));
    return -1;
}
""")
    provider = root / "kernel_stub.so"
    subprocess.run(
        [
            compiler,
            "-shared",
            "-fPIC",
            "-Wall",
            "-Wextra",
            "-Werror",
            str(stub),
            "-o",
            str(provider),
        ],
        check=True,
        capture_output=True,
    )
    return shim, provider


@pytest.mark.parametrize(
    "family,kind,fast,error,result,want",
    [
        ("inet", "stream", True, errno.EACCES, None, errno.EOPNOTSUPP),
        ("inet6", "stream", True, errno.EACCES, None, errno.EOPNOTSUPP),
        ("inet", "stream", False, errno.EACCES, None, errno.EACCES),
        ("inet", "stream", True, errno.EPERM, None, errno.EPERM),
        ("inet", "stream", True, errno.ECONNREFUSED, None, errno.ECONNREFUSED),
        ("inet", "datagram", True, errno.EACCES, None, errno.EACCES),
        ("unix", "stream", True, errno.EACCES, None, errno.EACCES),
        ("inet", "stream", True, errno.EACCES, 5, None),
    ],
)
def test_only_denied_tcp_fastopen_selects_existing_vlc_fallback(
    preload_libraries, family, kind, fast, error, result, want
):
    shim, provider = preload_libraries
    env = os.environ.copy()
    env["LD_PRELOAD"] = f"{shim}:{provider}"
    env["MOCK_SEND_ERRNO"] = str(error)
    env["CASE_FAMILY"] = family
    env["CASE_KIND"] = kind
    env["CASE_FAST"] = str(int(fast))
    if result is not None:
        env["MOCK_SEND_RESULT"] = str(result)
    else:
        env.pop("MOCK_SEND_RESULT", None)
    code = """
import json, os, socket
family = {"inet": socket.AF_INET, "inet6": socket.AF_INET6, "unix": socket.AF_UNIX}[os.environ["CASE_FAMILY"]]
kind = socket.SOCK_STREAM if os.environ["CASE_KIND"] == "stream" else socket.SOCK_DGRAM
address = ("127.0.0.1", 9) if family == socket.AF_INET else (("::1", 9) if family == socket.AF_INET6 else "/unused")
s = socket.socket(family, kind)
flags = socket.MSG_NOSIGNAL | (socket.MSG_FASTOPEN if os.environ["CASE_FAST"] == "1" else 0)
try:
    value = s.sendmsg([b"proof"], [], flags, address)
    print(json.dumps({"result": value, "errno": None}))
except OSError as error:
    print(json.dumps({"result": -1, "errno": error.errno}))
finally:
    s.close()
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    observed = json.loads(proc.stdout)
    assert observed["errno"] == want
    assert observed["result"] == (result if result is not None else -1)
