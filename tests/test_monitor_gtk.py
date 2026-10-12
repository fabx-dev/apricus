# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for version-agnostic MonitorInfo (GTK3 + GTK4, headless-safe)."""

from apricus.monitor import MonitorInfo, assign_keys


class FakeGeom:
    def __init__(self, x=0, y=0, w=100, h=100):
        self.x, self.y, self.width, self.height = x, y, w, h


class FakeMonitor3:
    """GTK3-shaped monitor."""

    def get_geometry(self):
        return FakeGeom()

    def get_width_mm(self):
        return 100

    def get_height_mm(self):
        return 100

    def get_model(self):
        return "Fake3"

    def is_primary(self):
        return True


class FakeMonitor4:
    """GTK4-shaped monitor (connector instead of model)."""

    def get_geometry(self):
        return FakeGeom()

    def get_connector(self):
        return "DP-9"


class FakeDisplay4:
    def get_monitors(self):
        return FakeListModel()


class FakeListModel:
    def __init__(self):
        self._items = [FakeMonitor4()]

    def get_n_items(self):
        return len(self._items)

    def get_item(self, i):
        return self._items[i]


class FakeDisplayModule:
    Display = None


def _fake_gdk4():
    class _D:
        @staticmethod
        def get_default():
            return FakeDisplay4()

    mod = FakeDisplayModule()
    mod.Display = _D
    return mod


def test_monitors_gtk4_list_model(monkeypatch):
    import apricus.monitor as M

    monkeypatch.setattr(M, "_gdk", _fake_gdk4)
    infos = MonitorInfo.monitors()
    assert [i["key"] for i in infos] == ["DP-9"]
    assert infos[0]["width"] == 100
    assert infos[0]["is_primary"] is False


def test_monitors_headless_none(monkeypatch):
    import apricus.monitor as M

    class _D:
        @staticmethod
        def get_default():
            return None

    mod = FakeDisplayModule()
    mod.Display = _D
    monkeypatch.setattr(M, "_gdk", lambda: mod)
    assert MonitorInfo.monitors() == []


def test_unique_count_headless(monkeypatch):
    import apricus.monitor as M

    class _D:
        @staticmethod
        def get_default():
            return None

    mod = FakeDisplayModule()
    mod.Display = _D
    monkeypatch.setattr(M, "_gdk", lambda: mod)
    assert MonitorInfo.get_unique_monitor_count() == 0


def test_assign_keys_still_stable():
    infos = [
        {"name": "A", "x": 0, "y": 0},
        {"name": "A", "x": 10, "y": 0},
    ]
    assert [i["key"] for i in assign_keys(infos)] == ["A#0", "A#1"]
