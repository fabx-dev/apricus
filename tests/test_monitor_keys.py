# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for stable monitor keys (no display required)."""

from unittest.mock import patch

from apricus.monitor import assign_keys
from apricus.player.video_player import VideoPlayer
from apricus.utils import ConfigUtil


def test_unique_models_keep_bare_model():
    infos = [
        {"name": "HDMI", "x": 0, "y": 0},
        {"name": "eDP", "x": 1920, "y": 0},
    ]
    assert [i["key"] for i in assign_keys(infos)] == ["HDMI", "eDP"]


def test_duplicates_get_suffix_in_xy_order_and_keep_input_order():
    infos = [
        {"name": "Dell", "x": 1920, "y": 0},
        {"name": "Dell", "x": 0, "y": 0},
        {"name": "Solo", "x": 0, "y": 1080},
    ]
    keyed = assign_keys(infos)
    assert [i["key"] for i in keyed] == ["Dell#1", "Dell#0", "Solo"]
    assert [i["x"] for i in keyed] == [1920, 0, 0]


def test_source_for_priority():
    ds = {"Dell#0": "/new.mp4", "Dell": "/old.mp4", "Default": "/def.mp4"}
    assert VideoPlayer._source_for(ds, "Dell#0", "Dell") == ("/new.mp4", "Dell#0")
    assert VideoPlayer._source_for(ds, "Dell#1", "Dell") == ("/old.mp4", "Dell")
    assert VideoPlayer._source_for({"Dell#0": "", "Default": "/def.mp4"}, "Dell#0", "Dell") == (
        "/def.mp4",
        "Default",
    )
    assert VideoPlayer._source_for({}, "X", "Y") == ("", "Default")


def test_migrate_legacy_duplicate_keys():
    cu = ConfigUtil()
    cu.save = lambda cfg: None
    fake_infos = [
        {"name": "Dell", "key": "Dell#0", "x": 0, "y": 0},
        {"name": "Dell", "key": "Dell#1", "x": 1920, "y": 0},
    ]
    cfg = {"data_source": {"Dell": "/old.mp4", "Default": "/def.mp4"}}
    with patch("apricus.monitor.MonitorInfo.monitors", return_value=fake_infos):
        cu._migrateLegacyMonitorKeys(cfg)
    assert cfg["data_source"] == {
        "Default": "/def.mp4",
        "Dell#0": "/old.mp4",
        "Dell#1": "/old.mp4",
    }


def test_migrate_unique_model_is_noop():
    cu = ConfigUtil()
    cu.save = lambda cfg: None
    cfg = {"data_source": {"Solo": "/s.mp4", "Default": "/def.mp4"}}
    with patch(
        "apricus.monitor.MonitorInfo.monitors",
        return_value=[{"name": "Solo", "key": "Solo", "x": 0, "y": 0}],
    ):
        cu._migrateLegacyMonitorKeys(cfg)
    assert cfg["data_source"] == {"Solo": "/s.mp4", "Default": "/def.mp4"}
