# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for ApricusServer.feeling_lucky (no display required)."""

from unittest.mock import patch

from apricus import server as srv


def _server_stub():
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.config = {"mode": "MODE_VIDEO", "data_source": {"M1": "/a.mp4", "Default": "/a.mp4"}}
    calls = []
    s.video = lambda path, monitor=None: calls.append(path)
    s._save_config = lambda: None
    s._load_config = lambda: None
    return s, calls


def _run(monitors, files):
    s, calls = _server_stub()
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_video_paths", return_value=list(files)),
    ):
        MM.return_value.get_monitors.return_value = {m: object() for m in monitors}
        s.feeling_lucky()
    return calls


def test_empty_library_does_not_crash_nor_play():
    # Regression: UnboundLocalError writing data_source.Default = null (#272)
    assert _run(["M1"], []) == []


def test_current_video_is_excluded():
    assert _run(["M1"], ["/a.mp4"]) == []


def test_picks_the_other_video():
    assert _run(["M1"], ["/a.mp4", "/b.mp4"]) == ["/b.mp4"]


def test_single_other_video_plays():
    assert _run(["M1"], ["/b.mp4"]) == ["/b.mp4"]
