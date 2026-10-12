"""Regression tests for the GTK4 panel queue/apply flow (Fase H1).

Why subprocess isolation: Gtk 3.0 (pinned by apricus.gui.control, pulled in
via apricus.server) and Gtk 4.0 (pinned by apricus.gui.gtk4app) cannot
coexist in one process — whichever `gi.require_version` runs second raises
ValueError. So each check below runs in a fresh interpreter where only the
GTK4 panel module is imported. No GTK widget is ever instantiated (the panel
object is built without __init__, all collaborators stubbed), hence headless.

Covers:
- Problem 1: Apply playlist must send the queue edited in the UI
  (Up/Down/Remove/Clear/Add), not the stale config value, and persist it.
- Problem 2: Apply with a multi-selection must explain itself instead of
  silently doing nothing (GTK4) or silently applying only the first item
  (GTK3 behavior in control.py:on_set_as).
"""

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

DRIVER = r'''
import copy
import sys
import traceback

import apricus.gui.gtk4app as app


class _FakeServer:
    def __init__(self):
        self.playlist_calls = []
        self.video_calls = []

    def playlist(self, paths, monitor):
        self.playlist_calls.append((list(paths), monitor))

    def video(self, path, monitor):
        self.video_calls.append((path, monitor))


class _ConfigStore:
    """In-memory stand-in for ConfigUtil (save persists, load returns it)."""

    def __init__(self, config):
        self.config = copy.deepcopy(config)
        self.saves = []

    def as_cls(self):
        store = self

        class _FakeConfigUtil:
            def save(self, config):
                store.saves.append(copy.deepcopy(config))
                store.config = copy.deepcopy(config)

            def load(self):
                return copy.deepcopy(store.config)

        return _FakeConfigUtil


def _base_config():
    return {
        "mode": "MODE_VIDEO",
        "data_source": {"Default": ""},
        "playlist": {"Default": ["/a.mp4", "/b.mp4", "/c.mp4"]},
        "playlist_shuffle": False,
    }


def _panel(config, queue, server="stub"):
    obj = app.ControlPanel.__new__(app.ControlPanel)
    obj.config = copy.deepcopy(config)
    obj.playlist_paths = list(queue)
    obj.window = None
    # Attributes normally built by do_activate(); the stubbed selection
    # helper below ignores them, they just need to exist.
    obj.video_selection = None
    obj.video_store = None
    fake_server = _FakeServer() if server == "stub" else server
    obj._server = lambda: fake_server
    obj._refresh_all = lambda: None
    return obj, fake_server


def _patch_config(monkeypatch_target, store):
    import apricus.gui.gtk4app as appmod

    old = appmod.ConfigUtil
    appmod.ConfigUtil = store.as_cls()
    return old


CHECKS = {}


def check(fn):
    CHECKS[fn.__name__] = fn
    return fn


@check
def apply_sends_edited_queue():
    # Simulate UI edits: remove /b.mp4, move /c.mp4 first.
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, server = _panel(_base_config(), ["/c.mp4", "/a.mp4"])
        obj.on_playlist_apply()
        assert server.playlist_calls == [(["/c.mp4", "/a.mp4"], "all")], server.playlist_calls
        assert store.config["playlist"]["Default"] == ["/c.mp4", "/a.mp4"]
        assert store.saves, "edited queue must be persisted"
    finally:
        appmod.ConfigUtil = old


@check
def apply_empty_queue_sends_nothing():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, server = _panel(_base_config(), [])
        obj.on_playlist_apply()
        assert server.playlist_calls == []
        assert store.saves == []
    finally:
        appmod.ConfigUtil = old


@check
def apply_resyncs_from_persisted():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    base_cls = store.as_cls()

    class _CappingUtil(base_cls):
        def save(self, config):
            config = copy.deepcopy(config)
            config["playlist"]["Default"] = config["playlist"]["Default"][:1]
            super().save(config)

    old = appmod.ConfigUtil
    appmod.ConfigUtil = _CappingUtil
    try:
        obj, server = _panel(_base_config(), ["/a.mp4", "/b.mp4"])
        obj.on_playlist_apply()
        assert obj.playlist_paths == ["/a.mp4"], obj.playlist_paths
    finally:
        appmod.ConfigUtil = old


@check
def library_add_syncs_memory_queue():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, server = _panel(_base_config(), ["/a.mp4"])
        obj._lib_selected_paths = lambda *a: ["/b.mp4", "/a.mp4"]
        obj.on_library_add()
        assert obj.playlist_paths == ["/a.mp4", "/b.mp4"], obj.playlist_paths
        assert server.playlist_calls == [(["/a.mp4", "/b.mp4"], "all")]
        assert store.config["playlist"]["Default"] == ["/a.mp4", "/b.mp4"]
    finally:
        appmod.ConfigUtil = old


@check
def library_add_duplicate_not_duplicated():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, server = _panel(_base_config(), ["/a.mp4"])
        obj._lib_selected_paths = lambda *a: ["/a.mp4"]
        obj.on_library_add()
        assert obj.playlist_paths == ["/a.mp4"]
        assert server.playlist_calls == [(["/a.mp4"], "all")]
    finally:
        appmod.ConfigUtil = old


@check
def apply_single_calls_video_once():
    obj, server = _panel(_base_config(), [])
    obj._lib_selected_paths = lambda *a: ["/only.mp4"]
    shown = []
    obj._show_apply_needs_single = lambda n: shown.append(n)
    obj.on_library_apply()
    assert server.video_calls == [("/only.mp4", "")]
    assert shown == []


@check
def apply_multi_explains_itself():
    obj, server = _panel(_base_config(), [])
    obj._lib_selected_paths = lambda *a: ["/a.mp4", "/b.mp4", "/c.mp4"]
    shown = []
    obj._show_apply_needs_single = lambda n: shown.append(n)
    obj.on_library_apply()
    assert server.video_calls == []
    assert shown == [3]


@check
def apply_empty_does_nothing():
    obj, server = _panel(_base_config(), [])
    obj._lib_selected_paths = lambda *a: []
    shown = []
    obj._show_apply_needs_single = lambda n: shown.append(n)
    obj.on_library_apply()
    assert server.video_calls == []
    assert shown == []


@check
def apply_without_server_still_persists():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, _ = _panel(_base_config(), ["/x.mp4"], server=None)
        obj.on_playlist_apply()
        assert store.config["playlist"]["Default"] == ["/x.mp4"]
        assert store.saves
    finally:
        appmod.ConfigUtil = old


def main():
    name = sys.argv[1]
    try:
        CHECKS[name]()
    except Exception:
        traceback.print_exc()
        return 1
    print("PASS", name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _run(check):
    import os

    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    # No display needed: no widget is ever instantiated.
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    proc = subprocess.run(
        [sys.executable, "-c", DRIVER, check],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"isolated check {check!r} failed:\n--- stdout ---\n{proc.stdout}"
        f"\n--- stderr ---\n{proc.stderr}"
    )


def test_apply_sends_edited_queue_not_stale_config():
    _run("apply_sends_edited_queue")


def test_apply_empty_queue_sends_nothing():
    _run("apply_empty_queue_sends_nothing")


def test_apply_resyncs_queue_from_persisted():
    _run("apply_resyncs_from_persisted")


def test_library_add_updates_memory_queue_and_applies():
    _run("library_add_syncs_memory_queue")


def test_library_add_duplicate_not_duplicated():
    _run("library_add_duplicate_not_duplicated")


def test_apply_single_video_calls_video_once():
    _run("apply_single_calls_video_once")


def test_apply_multi_selection_explains_itself():
    _run("apply_multi_explains_itself")


def test_apply_empty_selection_does_nothing():
    _run("apply_empty_does_nothing")


def test_apply_without_server_still_persists_queue():
    _run("apply_without_server_still_persists")
