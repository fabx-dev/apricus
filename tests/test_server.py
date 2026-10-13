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


def _setup_stub(monitors):
    s = srv.ApricusServer.__new__(srv.ApricusServer)
    s.config = {
        "mode": "MODE_NULL",
        "data_source": {
            "M1": "/old.mp4",
            "M2": "/old.mp4",
            "M9-stale": "/old.mp4",
            "Default": "/old.mp4",
        },
        "is_show_systray": False,
    }
    s._load_config = lambda: None
    s._save_config = lambda: None
    s._try_switch_media = lambda mode: True
    s._refresh_systray = lambda mode: None
    return s


def test_video_all_refreshes_every_monitor_key():
    """Single-video apply to all monitors must not leave stale per-monitor
    entries shadowing Default in the player's key→model→Default priority."""
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {
            "M1": object(),
            "M2": object(),
            "M3-live": object(),
        }
        s._setup_player("MODE_VIDEO", "/new.mp4", "")
    assert s.config["data_source"] == {
        "M1": "/new.mp4",
        "M2": "/new.mp4",
        "M9-stale": "/new.mp4",
        "M3-live": "/new.mp4",
        "Default": "/new.mp4",
    }


def test_video_single_monitor_keeps_other_keys():
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {"M1": object(), "M2": object()}
        s._setup_player("MODE_VIDEO", "/new.mp4", "M1")
    assert s.config["data_source"] == {
        "M1": "/new.mp4",
        "M2": "/old.mp4",
        "M9-stale": "/old.mp4",
        "Default": "/new.mp4",
    }


def test_stream_all_refreshes_every_monitor_key():
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {
            "M1": object(),
            "M2": object(),
            "M3-live": object(),
        }
        s._setup_player("MODE_STREAM", "https://example.com/v", "")
    assert s.config["data_source"] == {
        "M1": "https://example.com/v",
        "M2": "https://example.com/v",
        "M9-stale": "https://example.com/v",
        "M3-live": "https://example.com/v",
        "Default": "https://example.com/v",
    }


def test_webpage_all_refreshes_every_monitor_key():
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {
            "M1": object(),
            "M2": object(),
        }
        s._setup_player("MODE_WEBPAGE", "https://example.com/", "")
    assert s.config["data_source"] == {
        "M1": "https://example.com/",
        "M2": "https://example.com/",
        "M9-stale": "https://example.com/",
        "Default": "https://example.com/",
    }


def test_webpage_single_monitor_keeps_other_keys():
    from unittest.mock import patch

    s = _setup_stub(["M1", "M2"])
    with (
        patch.object(srv, "Monitors") as MM,
        patch.object(srv, "get_instance", return_value=None),
    ):
        MM.return_value.get_monitors.return_value = {"M1": object(), "M2": object()}
        s._setup_player("MODE_WEBPAGE", "https://example.com/", "M2")
    assert s.config["data_source"] == {
        "M1": "/old.mp4",
        "M2": "https://example.com/",
        "M9-stale": "/old.mp4",
        "Default": "https://example.com/",
    }
