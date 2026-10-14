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

import os
import pathlib
import subprocess
import sys

import pytest

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
        self.stream_calls = []
        self.webpage_calls = []

    def playlist(self, paths, monitor):
        self.playlist_calls.append((list(paths), monitor))

    def video(self, path, monitor):
        self.video_calls.append((path, monitor))

    def stream(self, url, monitor=""):
        self.stream_calls.append((url, monitor))

    def webpage(self, url, monitor=""):
        self.webpage_calls.append((url, monitor))


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

            @staticmethod
            def _sanitize_stream_entries(entries):
                from apricus.utils import ConfigUtil as RealConfigUtil

                return RealConfigUtil._sanitize_stream_entries(entries)

            @staticmethod
            def _sanitize_web_entries(entries):
                from apricus.utils import ConfigUtil as RealConfigUtil

                return RealConfigUtil._sanitize_web_entries(entries)

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
    obj._monitor_choices = lambda: []
    obj.on_library_apply()
    assert server.video_calls == [("/only.mp4", "")]
    assert shown == []


@check
def apply_single_with_monitors_opens_chooser():
    obj, server = _panel(_base_config(), [])
    obj._lib_selected_paths = lambda *a: ["/only.mp4"]
    chosen = []
    obj._show_monitor_chooser = lambda path: chosen.append(path)
    obj.on_library_apply()
    assert chosen == ["/only.mp4"], chosen
    assert server.video_calls == []


@check
def apply_single_with_one_monitor_still_opens_chooser():
    obj, server = _panel(_base_config(), [])
    obj._lib_selected_paths = lambda *a: ["/only.mp4"]
    obj._monitor_choices = lambda: [("QEMU Monitor", "QEMU Monitor")]
    chosen = []
    obj._show_monitor_chooser = lambda path: chosen.append(path)
    obj.on_library_apply()
    assert chosen == ["/only.mp4"], chosen
    assert server.video_calls == []


@check
def monitor_menu_items_list_all_plus_monitors():
    obj, _ = _panel(_base_config(), [])
    obj._monitor_choices = lambda: [("HDMI-1", "Acme 24"), ("DP-1", "Acme 27")]
    assert obj._monitor_menu_items("/x.mp4") == [
        ("Set for All", ""),
        ("Set for Acme 24", "HDMI-1"),
        ("Set for Acme 27", "DP-1"),
    ]


@check
def apply_video_to_monitor_calls_server_and_toasts():
    obj, server = _panel(_base_config(), [])
    seen = []

    class _Overlay:
        def add_toast(self, toast):
            seen.append(toast.get_title())

    obj.toasts = _Overlay()
    obj._apply_video_to_monitor("/v.mp4", "HDMI-1")
    assert server.video_calls == [("/v.mp4", "HDMI-1")], server.video_calls
    assert seen == ["Wallpaper applied on HDMI-1"], seen


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
def apply_without_server_explains_itself():
    obj, server = _panel(_base_config(), [], server=None)
    obj._lib_selected_paths = lambda *a: ["/only.mp4"]
    shown = []
    obj._show_error = lambda message: shown.append(message)
    obj.on_library_apply()
    assert server is None
    assert shown and "Background service" in shown[0], shown


@check
def play_pause_without_server_explains_itself():
    obj, _ = _panel(_base_config(), [], server=None)
    shown = []
    obj._show_error = lambda message: shown.append(message)
    obj.on_play_pause()
    assert shown and "Background service" in shown[0], shown


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


@check
def sidebar_sections_are_four_in_order():
    names = [name for name, _, _ in app.ControlPanel.SECTIONS]
    assert names == [
        "library",
        "playlist",
        "now-playing",
        "streaming",
        "webpage",
        "settings",
    ], names


@check
def sidebar_selection_routes_to_stack():
    shown = []

    class _FakeStack:
        def set_visible_child_name(self, name):
            shown.append(name)

    class _FakeRow:
        def get_name(self):
            return "playlist"

    obj, _ = _panel(_base_config(), [])
    obj.stack = _FakeStack()
    obj._on_sidebar_selected(None, _FakeRow())
    assert shown == ["playlist"], shown
    obj._on_sidebar_selected(None, None)
    assert shown == ["playlist"], "null row must not switch"


@check
def select_section_falls_back_to_library():
    selected = []

    class _FakeSidebar:
        def get_row_at_index(self, index):
            return f"row{index}"

        def select_row(self, row):
            selected.append(row)

    obj, _ = _panel(_base_config(), [])
    obj.sidebar = _FakeSidebar()
    obj._select_section("bogus")
    assert selected == ["row0"], selected
    obj._select_section("settings")
    assert selected == ["row0", "row5"], selected


@check
def now_playing_text_shows_mode_and_current():
    text = app.ControlPanel._now_playing_text("MODE_VIDEO", "/vids/a.mp4")
    assert "Single video" in text and "a.mp4" in text, text
    assert "/vids/a.mp4" not in text, "full paths must not clutter the UI"
    assert "MODE_VIDEO" not in text, "raw mode codes must never reach the UI"
    text = app.ControlPanel._now_playing_text("MODE_NULL", "")
    assert text == "Static wallpaper", text
    assert app.ControlPanel._display_source("/v/dir/a.mp4") == "a.mp4"
    assert app.ControlPanel._display_source("https://www.youtube.com/watch?v=x") == (
        "www.youtube.com"
    )
    assert app.ControlPanel._mode_text("MODE_PLAYLIST") == "Playlist (muted)"
    assert app.ControlPanel._mode_text("BOGUS") == "Static wallpaper"
    assert app.ControlPanel._mode_icon("MODE_STREAM") == "network-transmit-receive-symbolic"


@check
def queue_row_position_validates():
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    obj, _ = _panel(_base_config(), ["/a.mp4", "/b.mp4"])

    class _Item:
        def __init__(self, pos, item):
            self._pos = pos
            self._item = item

        def get_position(self):
            return self._pos

        def get_item(self):
            return self._item

    assert obj._queue_row_position(_Item(1, object())) == 1
    assert obj._queue_row_position(_Item(Gtk.INVALID_LIST_POSITION, object())) is None
    assert obj._queue_row_position(_Item(0, None)) is None
    assert obj._queue_row_position(_Item(2, object())) is None


@check
def queue_row_buttons_route_to_handlers():
    obj, _ = _panel(_base_config(), ["/a.mp4", "/b.mp4"])
    selected = []
    called = []

    class _FakeSelection:
        def set_selected(self, pos):
            selected.append(pos)

    obj.queue_selection = _FakeSelection()
    obj.on_queue_up = lambda *a: called.append("up")
    obj.on_queue_down = lambda *a: called.append("down")
    obj.on_queue_remove = lambda *a: called.append("remove")

    class _Item:
        def get_position(self):
            return 0

        def get_item(self):
            return object()

    obj._on_queue_row_down(None, _Item())
    obj._on_queue_row_up(None, _Item())
    obj._on_queue_row_remove(None, _Item())
    assert selected == [0, 0, 0], selected
    assert called == ["down", "up", "remove"], called


@check
def sidebar_builds_four_rows_and_pages():
    import os

    assert os.environ.get("DISPLAY"), "needs a display (run under Xvfb)"
    from gi.repository import Adw

    Adw.init()
    obj = app.ControlPanel.__new__(app.ControlPanel)
    obj.version = "test"
    container = obj._build_sidebar()
    sidebar = container.get_first_child()
    rows = [sidebar.get_row_at_index(i) for i in range(6)]
    assert [r.get_name() for r in rows] == [
        "library",
        "playlist",
        "now-playing",
        "streaming",
        "webpage",
        "settings",
    ]
    assert obj.sidebar_dot is not None and obj.sidebar_status is not None
    assert obj.sidebar_version is not None
    assert "test" in obj.sidebar_version.get_text()
    # Sidebar slot must never claim extra width: nested rows contain
    # expanding children, so without pinning the whole sidebar swallows
    # free window space (compute_expand propagates up).
    assert obj.sidebar_box.get_hexpand_set() is True
    assert obj.sidebar_box.get_hexpand() is False
    assert obj.sidebar_status.get_hexpand() is False
    obj._library_page()
    assert obj.video_store is not None and obj.video_selection is not None
    obj._playlist_page()
    assert obj.queue_store is not None and obj.queue_selection is not None
    obj._now_playing_page()
    assert obj.now_playing_status is not None
    assert obj.np_title is not None and obj.np_badge is not None
    assert obj.np_play_btn is not None and obj.np_volume is not None
    assert obj.np_details is not None and obj.np_empty is not None
    obj._streaming_page()
    assert obj.stream_entry is not None
    obj._webpage_page()
    assert obj.webpage_entry is not None
    assert obj.web_hero_title is not None and obj.web_detect_row is not None
    assert obj.web_file_row is not None and obj.web_details is not None
    obj._settings_page()
    assert not hasattr(obj, "mute_switch"), "mute lives only in Now Playing"
    assert not hasattr(obj, "volume_scale"), "volume lives only in Now Playing"
    assert obj.static_switch is not None
    assert obj.pause_max_switch is not None and obj.mute_max_switch is not None
    assert obj.autostart_switch is not None


@check
def library_add_all_appends_store_paths():
    import apricus.gui.gtk4app as appmod

    class _Item:
        def __init__(self, path):
            self.path = path

    class _Store:
        def __init__(self, paths):
            self._paths = paths

        def get_n_items(self):
            return len(self._paths)

        def get_item(self, index):
            return _Item(self._paths[index])

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, server = _panel(_base_config(), ["/a.mp4"])
        obj.video_store = _Store(["/a.mp4", "/b.mp4", "/c.mp4"])
        obj.on_library_add_all()
        assert obj.playlist_paths == ["/a.mp4", "/b.mp4", "/c.mp4"], obj.playlist_paths
        assert server.playlist_calls == [(["/a.mp4", "/b.mp4", "/c.mp4"], "all")]
    finally:
        appmod.ConfigUtil = old


@check
def stream_url_check_accepts_and_rejects():
    import apricus.gui.gtk4app as appmod

    class _FakeYdl:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            if "bad" in url:
                raise ValueError("ERROR: Unsupported URL")

    class _FakeYtDlp:
        YoutubeDL = _FakeYdl

    old = appmod.yt_dlp
    appmod.yt_dlp = _FakeYtDlp()
    try:
        obj, _ = _panel(_base_config(), [])
        assert obj._check_stream_url("https://www.youtube.com/watch?v=ok") is None
        error = obj._check_stream_url("https://bad.example/x")
        assert error, "invalid URL must produce an error message"
    finally:
        appmod.yt_dlp = old


@check
def stream_callsites_share_js_runtime_options():
    """Both gtk4app yt-dlp call-sites must use the shared base_options()
    (regression: a new call-site forgetting the runtime silently degrades
    extraction). js_runtimes is pinned only when a Deno binary is known;
    otherwise the key is omitted so yt-dlp keeps its PATH discovery."""
    import apricus.gui.gtk4app as appmod
    from apricus.yt_utils import js_runtime_options

    seen = []

    class _RecordingYdl:
        def __init__(self, params):
            seen.append(dict(params))

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download=False):
            return {"title": "T", "formats": []}

    class _RecordingYtDlp:
        YoutubeDL = _RecordingYdl

    old = appmod.yt_dlp
    appmod.yt_dlp = _RecordingYtDlp()
    try:
        obj, _ = _panel(_base_config(), [])
        expected_js = js_runtime_options()
        obj._check_stream_url("https://www.youtube.com/watch?v=ok")
        assert seen and seen[-1].get("noplaylist") is True, seen
        assert seen[-1].get("remote_components") == [], seen
        if expected_js:
            assert seen[-1].get("js_runtimes") == expected_js, seen
        else:
            assert "js_runtimes" not in seen[-1], seen
        assert seen[-1].get("quiet") is not True
        obj.np_detail_rows = {}
        obj._info_np_stream("https://www.youtube.com/watch?v=ok", 1)
        assert seen and seen[-1].get("noplaylist") is True, seen
        assert seen[-1].get("quiet") is True, "info path keeps quiet"
    finally:
        appmod.yt_dlp = old


@check
def finish_stream_apply_records_and_opens_chooser():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, server = _panel(_base_config(), [])
        chosen = []
        obj._show_stream_monitor_chooser = lambda url: chosen.append(url)
        obj._finish_stream_apply("https://example.com/v", None)
        assert chosen == ["https://example.com/v"], chosen
        assert [e["url"] for e in store.config["stream_history"]] == [
            "https://example.com/v"
        ]
        assert server.stream_calls == [], "apply happens after monitor choice"
    finally:
        appmod.ConfigUtil = old


@check
def apply_stream_to_monitor_persists_and_calls_server():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, server = _panel(_base_config(), [])
        obj._apply_stream_to_monitor("https://example.com/v", "M1")
        assert store.config["mode"] == "MODE_STREAM", store.config["mode"]
        assert store.config["data_source"]["Default"] == "https://example.com/v"
        assert server.stream_calls == [("https://example.com/v", "M1")], (
            server.stream_calls
        )
    finally:
        appmod.ConfigUtil = old


@check
def finish_stream_apply_error_shows_no_server_call():
    obj, server = _panel(_base_config(), [])
    shown = []
    obj._show_error = lambda message: shown.append(message)
    assert obj._finish_stream_apply("https://bad.example/x", "Unsupported URL") is False
    assert shown and "Unsupported URL" in shown[0]
    assert server.stream_calls == []


@check
@check
@check
def set_webpage_source_records_and_opens_chooser():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, server = _panel(_base_config(), [])
        chosen = []
        obj._show_webpage_monitor_chooser = lambda source: chosen.append(source)
        obj._set_webpage_source("https://example.com/")
        assert chosen == ["https://example.com/"], chosen
        assert [e["url"] for e in store.config["web_history"]] == [
            "https://example.com/"
        ]
        assert server.webpage_calls == [], "apply happens after monitor choice"
    finally:
        appmod.ConfigUtil = old


@check
@check
def apply_webpage_to_monitor_persists_and_calls_server():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, server = _panel(_base_config(), [])
        obj._apply_webpage_to_monitor("https://example.com/", "M1")
        assert store.config["mode"] == "MODE_WEBPAGE", store.config["mode"]
        assert store.config["data_source"]["Default"] == "https://example.com/"
        assert server.webpage_calls == [("https://example.com/", "M1")], (
            server.webpage_calls
        )
    finally:
        appmod.ConfigUtil = old


@check
def video_dir_chosen_persists_and_refreshes():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, _ = _panel(_base_config(), [])
        refreshed = []
        obj._refresh_all = lambda: refreshed.append(True)

        class _FakeFile:
            def get_path(self):
                return "/tmp/videos"

        class _FakeDialog:
            def select_folder_finish(self, result):
                return _FakeFile()

        obj._on_video_dir_chosen(_FakeDialog(), None)
        assert store.config["video_dir"] == "/tmp/videos", store.config
        assert refreshed == [True]
    finally:
        appmod.ConfigUtil = old


@check
def lib_actions_gate_on_selection():
    obj, _ = _panel(_base_config(), [])
    states = {}

    class _Btn:
        def __init__(self, name):
            self._name = name

        def set_sensitive(self, value):
            states[self._name] = value

    obj.apply_btn = _Btn("apply")
    obj.add_split = _Btn("add")
    obj._update_lib_actions(0)
    assert states == {"apply": False, "add": False}, states
    obj._update_lib_actions(3)
    assert states == {"apply": True, "add": True}, states


@check
def source_row_shows_folder_or_empty():
    import apricus.gui.gtk4app as appmod

    obj, _ = _panel(_base_config(), [])
    shown = {}

    class _Row:
        def set_title(self, title):
            shown["title"] = title

        def set_subtitle(self, subtitle):
            shown["subtitle"] = subtitle

    obj.source_row = _Row()
    real = appmod.resolve_video_dir
    appmod.resolve_video_dir = lambda cfg: cfg.get("video_dir")
    try:
        obj.config = {"video_dir": "/v/videos"}
        obj._refresh_source_row(16)
        assert shown == {"title": "videos", "subtitle": "/v/videos — 16 videos"}, shown
        obj.config = {"video_dir": None}
        obj._refresh_source_row()
        assert shown["title"] == "No folder chosen", shown
    finally:
        appmod.resolve_video_dir = real


@check
def move_queue_item_reorders_and_reselects():
    obj, _ = _panel(_base_config(), ["/a", "/b", "/c"])
    selected = []

    class _Sel:
        def set_selected(self, pos):
            selected.append(pos)

    obj.queue_selection = _Sel()
    obj._reload_queue_store = lambda: None
    obj._refresh_playlist_status = lambda: None
    assert obj._move_queue_item(0, 2) == 2
    assert obj.playlist_paths == ["/b", "/c", "/a"]
    assert selected == [2]
    assert obj._move_queue_item(2, 2) == 2
    assert obj._move_queue_item(0, 99) == 2
    assert obj.playlist_paths == ["/c", "/a", "/b"]
    obj.playlist_paths = []
    assert obj._move_queue_item(0, 1) is None


@check
def drop_dest_math():
    m = app.ControlPanel._drop_dest
    assert m(0, 2, True) == 2 and m(0, 2, False) == 1
    assert m(3, 1, True) == 2 and m(3, 1, False) == 1
    assert m(1, 1, True) == 1


def _queue_stubs(obj, sel):
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    class _Sel:
        def __init__(self):
            self.sel = sel

        def get_selected(self):
            return self.sel

        def set_selected(self, pos):
            self.sel = pos

    obj.queue_selection = _Sel()
    obj._reload_queue_store = lambda: None
    obj._refresh_playlist_status = lambda: None
    obj.playlist_status = type("L", (), {"set_text": lambda *a: None})()
    states = {}

    class _Btn:
        def __init__(self, name):
            self._name = name

        def set_sensitive(self, value):
            states[self._name] = value

    obj.queue_remove_btn = _Btn("rm")
    obj.queue_clear_btn = _Btn("clear")
    return states


@check
def remove_offers_undo_and_restores():
    obj, _ = _panel(_base_config(), ["/a", "/b", "/c"])
    _queue_stubs(obj, 1)
    toasts = []

    class _Overlay:
        def add_toast(self, toast):
            toasts.append(toast)

    obj.toasts = _Overlay()
    obj.on_queue_remove()
    assert obj.playlist_paths == ["/a", "/c"], obj.playlist_paths
    assert len(toasts) == 1
    toasts[0].emit("button-clicked")
    assert obj.playlist_paths == ["/a", "/b", "/c"], obj.playlist_paths


@check
def clear_helper_empties_and_toasts():
    obj, _ = _panel(_base_config(), ["/a", "/b"])
    _queue_stubs(obj, 0)
    seen = []

    class _Overlay:
        def add_toast(self, toast):
            seen.append(toast.get_title())

    obj.toasts = _Overlay()
    obj._do_queue_clear()
    assert obj.playlist_paths == []
    assert seen == ["Playlist cleared"], seen


@check
def queue_actions_follow_selection_and_content():
    import gi

    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    obj, _ = _panel(_base_config(), ["/a"])
    states = _queue_stubs(obj, Gtk.INVALID_LIST_POSITION)
    obj._update_queue_actions()
    assert states == {"rm": False, "clear": True}, states
    obj.queue_selection.sel = 0
    obj._update_queue_actions()
    assert states == {"rm": True, "clear": True}, states
    obj.playlist_paths = []
    obj._update_queue_actions()
    assert states == {"rm": True, "clear": False}, states


@check
def refresh_now_playing_drives_hero_widgets():
    """The winning _refresh_now_playing must drive badge/title/visibility
    (guards against a stale duplicate shadowing it)."""
    obj, _ = _panel(_base_config(), [])
    texts, visibles = {}, {}

    class _W:
        def __init__(self, name):
            self._name = name

        def set_text(self, text):
            texts[self._name] = text

        def set_visible(self, visible):
            visibles[self._name] = visible

        def set_icon_name(self, name):
            pass

        def set_tooltip_text(self, text):
            pass

        def set_sensitive(self, value):
            pass

    obj.np_content = _W("content")
    obj.np_empty = _W("empty")
    obj.np_badge = _W("badge")
    obj.np_title = _W("title")
    obj.np_subtitle = _W("sub")
    obj.now_playing_status = _W("status")
    obj.np_play_btn = _W("play")
    obj.np_eq = _W("eq")
    obj.np_volume = _W("vol")
    obj.np_mute_btn = _W("mute")

    class _Srv:
        mode = "MODE_VIDEO"
        is_paused_by_user = False

    obj._server = lambda: _Srv()
    obj.config = {
        "mode": "MODE_NULL",
        "data_source": {"Default": "/v/a.mp4"},
        "playlist": {"Default": []},
        "playlist_shuffle": False,
        "video_dir": None,
        "is_mute": False,
        "audio_volume": 50,
    }
    obj.playlist_paths = []
    obj._np_current_path = None
    obj.np_generation = 0
    loaded = []
    obj._load_np_hero = lambda path, generation: loaded.append((path, generation))
    obj._refresh_np_transport = lambda server: None
    obj._refresh_np_details = lambda mode, current: None
    obj._refresh_np_queue = lambda mode: None
    obj._refresh_now_playing()
    assert texts["badge"] == "Playing", texts
    assert texts["title"] == "a.mp4", texts
    assert visibles["content"] is True and visibles["empty"] is False, visibles
    assert loaded == [("/v/a.mp4", 1)], loaded


@check
def now_playing_playlist_follows_queue_head():
    """In MODE_PLAYLIST the hero/title must follow playlist_paths[0]:
    the server never rewrites data_source.Default on playlist apply, so
    without this the hero keeps showing the previous single video."""

    def _obj(mode, default, queue):
        obj, _ = _panel(_base_config(), [])
        texts = {}

        class _W:
            def __init__(self, name):
                self._name = name

            def set_text(self, text):
                texts[self._name] = text

            def set_visible(self, visible):
                pass

            def set_icon_name(self, name):
                pass

            def set_tooltip_text(self, text):
                pass

            def set_sensitive(self, value):
                pass

        obj.np_content = _W("content")
        obj.np_empty = _W("empty")
        obj.np_badge = _W("badge")
        obj.np_title = _W("title")
        obj.np_subtitle = _W("sub")
        obj.now_playing_status = _W("status")
        obj.np_play_btn = _W("play")
        obj.np_eq = _W("eq")
        obj.np_volume = _W("vol")
        obj.np_mute_btn = _W("mute")

        class _Srv:
            pass

        srv = _Srv()
        srv.mode = mode
        srv.is_paused_by_user = False
        obj._server = lambda: srv
        obj.config = {
            "mode": mode,
            "data_source": {"Default": default},
            "playlist": {"Default": list(queue)},
            "playlist_shuffle": False,
            "video_dir": None,
            "is_mute": False,
            "audio_volume": 50,
        }
        obj.playlist_paths = list(queue)
        obj._np_current_path = None
        obj.np_generation = 0
        loaded = []
        obj._load_np_hero = lambda path, generation: loaded.append((path, generation))
        obj._refresh_np_transport = lambda server: None
        obj._refresh_np_details = lambda mode, current: None
        obj._refresh_np_queue = lambda mode: None
        obj._refresh_now_playing()
        return texts, loaded

    # Queue head wins over a stale Default.
    texts, loaded = _obj("MODE_PLAYLIST", "/v/old.mp4", ["/v/a.mp4", "/v/b.mp4"])
    assert texts["title"] == "a.mp4", texts
    assert loaded == [("/v/a.mp4", 1)], loaded
    # Empty queue falls back to Default (previous behavior preserved).
    texts, loaded = _obj("MODE_PLAYLIST", "/v/old.mp4", [])
    assert texts["title"] == "old.mp4", texts
    assert loaded == [("/v/old.mp4", 1)], loaded
    # Non-playlist modes still use Default.
    texts, loaded = _obj("MODE_VIDEO", "/v/old.mp4", ["/v/a.mp4"])
    assert texts["title"] == "old.mp4", texts
    assert loaded == [("/v/old.mp4", 1)], loaded


@check
def now_playing_helpers():
    badge = app.ControlPanel._np_badge
    assert badge("MODE_VIDEO", False) == "Playing"
    assert badge("MODE_VIDEO", True) == "Paused"
    assert badge("MODE_STREAM", False) == "Playing"
    assert badge("MODE_PLAYLIST", False) == "Playlist in loop"
    assert badge("MODE_WEBPAGE", False) == "Web page"
    assert badge("MODE_NULL", False) == "Static"
    assert badge("BOGUS", True) == "Static"
    fmt = app.ControlPanel._fmt_duration
    assert fmt(None) == "Live"
    assert fmt(65) == "01:05"
    assert fmt(3661) == "1:01:01"
    mute = app.ControlPanel._mute_icon
    assert mute(True, 80) == "audio-volume-muted-symbolic"
    assert mute(False, 0) == "audio-volume-muted-symbolic"
    assert mute(False, 20) == "audio-volume-low-symbolic"
    assert mute(False, 50) == "audio-volume-medium-symbolic"
    assert mute(False, 90) == "audio-volume-high-symbolic"
    thumb = app.ControlPanel._stream_thumb_url
    assert thumb({"thumbnail": "https://img/x.jpg"}) == "https://img/x.jpg"
    assert thumb({}) is None
    assert thumb(None) is None


@check
def show_hero_texture_swaps_visible_pic():
    obj, _ = _panel(_base_config(), [])
    painted = []
    shown = []

    class _Pic:
        def set_paintable(self, texture):
            painted.append(texture)

    class _Stack:
        def set_visible_child_name(self, name):
            shown.append(name)

    obj.np_pic_a = _Pic()
    obj.np_pic_b = _Pic()
    obj.np_stack = _Stack()
    obj._np_visible_pic = "a"
    obj.np_generation = 3
    marker = object()
    obj._show_hero_texture(marker, 3)
    assert painted == [marker] and shown == ["b"], (painted, shown)
    assert obj._np_visible_pic == "b"
    obj._show_hero_texture(marker, 2)  # stale generation ignored
    assert painted == [marker] and shown == ["b"]
    obj._show_hero_texture(None, 3)  # no texture ignored
    assert painted == [marker] and shown == ["b"]


@check
def stream_classify_and_errors():
    classify = app.ControlPanel._classify_stream_url
    assert classify("https://example.com/v.mp4") == "direct"
    assert classify("https://example.com/live.m3u8?x=1") == "direct"
    assert classify("https://www.youtube.com/watch?v=abc") == "page"
    assert classify("https://youtu.be/abc") == "page"
    assert classify("https://www.youtube.com/watch?v=abc&list=xyz") == "collection"
    assert classify("https://www.youtube.com/playlist?list=xyz") == "collection"
    assert classify("not a url") == "invalid"
    assert classify("") == "invalid"
    errors = app.ControlPanel._classify_stream_error
    assert "bot" in errors("ERROR: Sign in to confirm you're not a bot")
    assert "private" in errors("ERROR: Private video") or "login" in errors(
        "ERROR: Login required"
    )
    assert "DRM" in errors("ERROR: DRM protected, widevine needed")
    assert "404" in errors("ERROR: 404 Not Found")
    assert errors("ERROR: Unsupported URL") == "URL not supported."


@check
def stream_record_favorite_remove_roundtrip():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, _ = _panel(_base_config(), [])
        obj._record_stream_use("https://a.example/1", title="One", kind="page")
        obj._record_stream_use("https://b.example/2", kind="direct")
        history = store.config["stream_history"]
        assert [e["url"] for e in history] == [
            "https://b.example/2",
            "https://a.example/1",
        ], history
        obj._toggle_stream_favorite("https://a.example/1")
        assert store.config["stream_favorites"] == ["https://a.example/1"]
        obj._toggle_stream_favorite("https://a.example/1")
        assert store.config["stream_favorites"] == []
    finally:
        appmod.ConfigUtil = old


@check
def stream_unstar_keeps_history_and_clear_keeps_saved():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, _ = _panel(_base_config(), [])
        obj._record_stream_use("https://a.example/1", kind="page")
        obj._record_stream_use("https://b.example/2", kind="page")
        obj._toggle_stream_favorite("https://a.example/1")
        # Unstar: saved loses it, history keeps it.
        obj._toggle_stream_favorite("https://a.example/1")
        assert [e["url"] for e in store.config["stream_history"]] == [
            "https://b.example/2",
            "https://a.example/1",
        ]
        assert store.config["stream_favorites"] == []
        # Clear recents: favorited entries survive.
        obj._toggle_stream_favorite("https://b.example/2")
        obj._toast = lambda *a: None
        obj._reload_stream_lists = lambda: None
        obj.on_clear_stream_history()
        assert [e["url"] for e in store.config["stream_history"]] == [
            "https://b.example/2"
        ]
    finally:
        appmod.ConfigUtil = old


@check
def sidebar_streaming_selection_reloads_lists():
    shown = []
    reloaded = []

    class _FakeStack:
        def set_visible_child_name(self, name):
            shown.append(name)

    class _FakeRow:
        def get_name(self):
            return "streaming"

    obj, _ = _panel(_base_config(), [])
    obj.stack = _FakeStack()
    obj._reload_stream_lists = lambda: reloaded.append(True)
    obj._on_sidebar_selected(None, _FakeRow())
    assert shown == ["streaming"] and reloaded == [True]


@check
def webpage_helpers():
    assert (
        app.ControlPanel._web_domain("https://www.windy.com/?x=1") == "www.windy.com"
    )
    assert app.ControlPanel._web_domain("not a url") == ""
    assert app.ControlPanel._web_monogram("windy.com") == "WC"
    assert app.ControlPanel._web_monogram("sub.example.co.uk") == "SE"
    assert app.ControlPanel._web_monogram("") == ""
    extract = app.ControlPanel._extract_html_title
    assert extract(b"<html><head><title>  Hello  World </title></head>") == "Hello World"
    assert extract(b"<TITLE>A &amp; B</TITLE>") == "A & B"
    assert extract(b"<html>no title here") == ""
    assert extract(b"") == ""
    assert extract(None) == ""
    fmt = app.ControlPanel._format_file_size
    assert fmt(512) == "512 B"
    assert fmt(12288) == "12 KB"
    assert fmt(3145728) == "3.0 MB"
    assert fmt(None) == "—"


@check
def web_hero_sync_and_file_row():
    obj, _ = _panel(_base_config(), [])
    shown = {}

    class _L:
        def __init__(self, name):
            self._name = name

        def set_text(self, text):
            shown[self._name] = text

        def set_subtitle(self, text):
            shown[self._name] = text

        def set_title(self, title):
            shown[self._name + ":title"] = title

        def set_visible(self, visible):
            shown[self._name + ":visible"] = visible

        def set_tooltip_text(self, text):
            pass

    obj.web_hero_title = _L("htitle")
    obj.web_hero_sub = _L("hsub")
    obj.web_hero_icon = _L("icon")
    obj.web_file_row = _L("file")
    obj.config = {"video_dir": None}
    obj._update_web_hero("", "")
    assert shown["htitle"] == "Web Page"
    assert "Type an address" in shown["hsub"]
    obj._update_web_hero("https://www.windy.com/", "")
    assert shown["htitle"] == "www.windy.com"
    assert "mono" not in shown, "no monogram labels anymore"


@check
def web_record_star_clear_roundtrip():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, _ = _panel(_base_config(), [])
        obj._reload_web_lists = lambda: None
        obj._record_web_use("https://w.example/p", title="P")
        obj._record_web_use("/tmp/page.html")
        urls = [e["url"] for e in store.config["web_history"]]
        assert urls == ["/tmp/page.html", "https://w.example/p"], urls
        obj._toggle_web_favorite("https://w.example/p")
        assert store.config["web_favorites"] == ["https://w.example/p"]
        obj.on_clear_web_history()
        urls = [e["url"] for e in store.config["web_history"]]
        assert urls == ["https://w.example/p"], urls
        obj._toast = lambda *a: None
    finally:
        appmod.ConfigUtil = old


@check
def web_sanitize_accepts_html_paths():
    import apricus.utils as real_utils

    entries = real_utils.ConfigUtil._sanitize_web_entries(
        [
            {"url": "https://w.example/p", "last_used": 3},
            {"url": "/tmp/a.html", "last_used": 2},
            {"url": "/tmp/b.txt", "last_used": 1},
            {"url": "notaurl", "last_used": 4},
            "junk",
        ]
    )
    assert [e["url"] for e in entries] == [
        "https://w.example/p",
        "/tmp/a.html",
    ], entries


@check
def web_list_title_prefers_title_then_domain():
    obj, _ = _panel(_base_config(), [])
    assert obj._web_list_title({"url": "https://w.example/p", "title": "Hello"}) == "Hello"
    assert obj._web_list_title({"url": "https://www.windy.com/", "title": ""}) == (
        "www.windy.com"
    )
    assert obj._web_list_title({"url": "/tmp/my page.html", "title": ""}) == "my page.html"


@check
def stream_service_names():
    service = app.ControlPanel._service_name
    assert service("https://www.youtube.com/watch?v=x") == "YouTube"
    assert service("https://youtu.be/x") == "YouTube"
    assert service("https://www.twitch.tv/someone") == "Twitch"
    assert service("https://vimeo.com/123") == "Vimeo"
    assert service("https://www.twitch.tvx.com/") == ""
    assert service("not a url") == ""
    assert service("") == ""


@check
def stream_favicon_cache_miss_and_path():
    import os

    obj, _ = _panel(_base_config(), [])
    assert obj._cached_favicon("nonexistent.invalid") is None
    assert obj._cached_favicon("") is None
    path = app.ControlPanel._favicon_path("YouTube.COM")
    assert path.endswith(os.path.join("apricus", "favicons", "youtube.com.png")), path


@check
def plain_escapes_markup_and_busy_toggles_button():
    assert app.ControlPanel._plain("a & b") == "a &amp; b"
    assert app.ControlPanel._plain(None) == ""
    assert app.ControlPanel._plain("Orologio — minimale") == "Orologio — minimale"
    obj, _ = _panel(_base_config(), [])
    states = {}

    class _Btn:
        def set_sensitive(self, value):
            states["sensitive"] = value

        def set_label(self, label):
            states["label"] = label

    obj._set_apply_busy(_Btn(), True)
    assert states == {"sensitive": False, "label": "Checking…"}, states
    obj._set_apply_busy(_Btn(), False)
    assert states == {"sensitive": True, "label": "Apply"}, states
    obj._set_apply_busy(None, True)  # must not raise


@check
def favicon_states_with_mocks():
    """State machine: ok applies file, stale/entry-change/None keep fallback."""
    import logging

    import apricus.gui.gtk4app as appmod

    calls = []

    class _Icon:
        def set_from_file(self, path):
            calls.append(("file", path))

        def set_from_icon_name(self, name):
            calls.append(("icon", name))

    class _Spin:
        def set_spinning(self, active):
            calls.append(("spin", active))

    class _Entry:
        def __init__(self, text):
            self.text = text

        def get_text(self):
            return self.text

    obj, _ = _panel(_base_config(), [])
    obj.stream_type_icon = _Icon()
    obj.stream_type_spinner = _Spin()
    obj.stream_entry = _Entry("https://w.example/v")
    obj._favicon_host = None
    obj._favicon_generation = 0
    records = []

    class _Handler(logging.Handler):
        def emit(self, record):
            records.append((record.levelname, record.getMessage()))

    logger = logging.getLogger("Apricus")
    handler = _Handler()
    old_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        # Valid apply.
        obj._set_favicon_state("ok", path="/tmp/fav.png")
        assert ("file", "/tmp/fav.png") in calls
        assert any(lvl == "INFO" and "ok" in msg for lvl, msg in records), records
        # Fallback keeps a coherent icon, logged.
        obj._set_favicon_state("fallback", reason="http_404")
        assert ("icon", "web-browser-symbolic") in calls, calls
        assert any(lvl == "INFO" and "http_404" in msg for lvl, msg in records), records
        # Stale generation never touches the widget.
        before = len(calls)
        assert obj._apply_favicon("https://w.example/v", 999, "/tmp/fav.png") is False
        # Valid generation but no path: explicit fallback icon (logged).
        obj._favicon_generation = 7
        assert obj._apply_favicon("https://w.example/v", 7, None) is False
        assert ("icon", "web-browser-symbolic") in calls[before:], calls[before:]
        before = len(calls)
        # Entry changed in the meantime: keep current icon.
        obj.stream_entry = _Entry("https://other.example/")
        assert obj._apply_favicon("https://w.example/v", 7, "/tmp/fav.png") is False
        assert len(calls) == before
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


@check
def favicon_regression_persists_after_retype():
    """Open -> favicon applied -> retype/refresh -> cache reuse keeps it.

    Fails on the old behavior where every type/refresh reset the icon.
    """
    import apricus.gui.gtk4app as appmod

    calls = []

    class _Icon:
        def set_from_file(self, path):
            calls.append(("file", path))

        def set_from_icon_name(self, name):
            calls.append(("icon", name))

    class _Spin:
        def set_spinning(self, active):
            pass

    class _Row:
        def set_subtitle(self, text):
            pass

    class _Entry:
        def __init__(self):
            self.text = ""

        def get_text(self):
            return self.text

        def set_text(self, text):
            self.text = text

    class _Label:
        def set_text(self, text):
            pass

    obj, _ = _panel(_base_config(), [])
    obj.stream_type_icon = _Icon()
    obj.stream_type_spinner = _Spin()
    obj.stream_type_row = _Row()
    obj.stream_entry = _Entry()
    obj.stream_hero_title = _Label()
    obj.stream_hero_sub = _Label()
    obj._favicon_host = None
    obj._favicon_generation = 0
    real_cached = appmod.ControlPanel._cached_favicon
    appmod.ControlPanel._cached_favicon = lambda self, host: "/tmp/cached.png"
    try:
        obj.stream_entry.set_text("https://w.example/v")
        obj._on_stream_entry_changed(obj.stream_entry)
        assert ("file", "/tmp/cached.png") in calls, calls
        n_calls = len(calls)
        # Retype the same URL + explicit refresh: icon must survive.
        obj._on_stream_entry_changed(obj.stream_entry)
        obj._update_stream_type()
        assert calls[n_calls:] == [], calls[n_calls:]
    finally:
        appmod.ControlPanel._cached_favicon = real_cached


@check
def download_favicon_validates_and_normalizes():
    """Mocked HTTP + real decode: ok only for valid magic; transient retry."""
    import apricus.gui.gtk4app as appmod
    from gi.repository import GdkPixbuf

    pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, 64, 16)
    _ok, png = pixbuf.save_to_bufferv("png", [], [])

    class _Resp:
        def __init__(self, status=200, ctype="image/png", body=b""):
            self.status_code = status
            self.headers = {"Content-Type": ctype}
            self._body = body

        def iter_content(self, size):
            yield self._body

        def close(self):
            pass

    class _Requests:
        def __init__(self, script):
            self.script = list(script)

        def get(self, *a, **k):
            item = self.script.pop(0)
            if isinstance(item[1], BaseException):
                raise item[1]
            return item[1]

        @property
        def exceptions(self):
            import requests as real_requests

            return real_requests.exceptions

    import tempfile

    import requests as real_requests_mod

    tmp = tempfile.mkdtemp(prefix="apricus-favtest-")
    real_path = app.ControlPanel._favicon_path
    app.ControlPanel._favicon_path = staticmethod(lambda host: f"{tmp}/{host}.png")
    real_requests = appmod.requests
    import apricus.utils as _U

    real_public = _U.public_http_url
    # DNS gate is orthogonal here (fake hosts): accept, keep testing decode.
    _U.public_http_url = lambda url: url
    # fetch_public resolves `requests` lazily from the real module: route it
    # into the scripted fake (which tolerates the allow_redirects kwarg).
    real_get = real_requests_mod.get
    real_requests_mod.get = lambda *a, **k: appmod.requests.get(*a, **k)
    try:
        # 1. Valid PNG -> ok + normalized file on disk.
        appmod.requests = _Requests([("png", _Resp(200, "image/png", png))])
        obj, _ = _panel(_base_config(), [])
        path, reason = obj._download_favicon("w.example")
        assert reason == "ok" and path and path.endswith(".png"), (path, reason)
        import os

        assert os.path.getsize(path) > 0
        from gi.repository import GdkPixbuf as _Pb

        assert _Pb.Pixbuf.new_from_file(path).get_width() == 32
        # 2. HTML body with image content-type -> bad_magic, no file.
        appmod.requests = _Requests([("html", _Resp(200, "image/png", b"<html>"))])
        path, reason = obj._download_favicon("x.example")
        assert (path, reason) == (None, "bad_magic"), (path, reason)
        # 3. 404 -> http_404 (no retry inside download).
        appmod.requests = _Requests([("404", _Resp(404, "text/plain", b"no"))])
        path, reason = obj._download_favicon("y.example")
        assert (path, reason) == (None, "http_404"), (path, reason)
        # 4. Timeout is transient (retry happens in _fetch, not here).
        appmod.requests = _Requests([("t/o", real_requests_mod.Timeout("slow"))])
        path, reason = obj._download_favicon("z.example")
        assert (path, reason) == (None, "timeout"), (path, reason)
    finally:
        appmod.requests = real_requests
        app.ControlPanel._favicon_path = real_path
        real_requests_mod.get = real_get
        _U.public_http_url = real_public


@check
def hero_parity_streaming_webpage():
    """Structural parity: both heroes share builder, geometry and classes."""
    import os

    assert os.environ.get("DISPLAY"), "needs a display (run under Xvfb)"
    from gi.repository import Adw, Gtk, Pango

    Adw.init()
    obj = app.ControlPanel.__new__(app.ControlPanel)
    # Hold page references: dropping them lets GC unparent the widgets.
    obj._held_pages = [obj._streaming_page(), obj._webpage_page()]

    def signature(frame, name):
        box = frame.get_child()
        assert type(box).__name__ == "Box", (name, type(box).__name__)
        assert box.get_orientation() == Gtk.Orientation.VERTICAL, name
        assert box.get_spacing() == 8, name
        assert box.get_halign() == Gtk.Align.FILL, name
        assert box.has_css_class("web-hero"), name
        kids = []
        child = box.get_first_child()
        while child is not None:
            kids.append(child)
            child = child.get_next_sibling()
        assert [type(k).__name__ for k in kids] == ["Image", "Label", "Label"], (
            name,
            [type(k).__name__ for k in kids],
        )
        icon, title, sub = kids
        assert icon.get_pixel_size() == 45, name
        for label, cls in ((title, "title-4"), (sub, "dim-label")):
            assert label.has_css_class(cls), (name, cls)
            assert label.get_xalign() == 0, name
            assert label.get_ellipsize() == Pango.EllipsizeMode.END, name
        for label in (title, sub):
            assert not label.has_css_class("web-hero-title"), (name, "monogram gone")
        return kids

    s_kids = signature(obj.stream_hero_icon.get_parent().get_parent(), "streaming")
    w_kids = signature(obj.web_hero_icon.get_parent().get_parent(), "webpage")
    s_frame = obj.stream_hero_icon.get_parent().get_parent()
    w_frame = obj.web_hero_icon.get_parent().get_parent()
    assert s_frame.has_css_class("card") and w_frame.has_css_class("card")
    s_min, _, _, _ = s_frame.measure(Gtk.Orientation.HORIZONTAL, -1)
    w_min, _, _, _ = w_frame.measure(Gtk.Orientation.HORIZONTAL, -1)
    assert abs(s_min - w_min) <= 40, (s_min, w_min)
    for page in obj._held_pages:
        node = page.get_child()
        while node is not None and type(node).__name__ != "Box":
            node = node.get_first_child()
        assert node is not None
        req = node.get_size_request()
        assert (req.width, req.height) == (
            app.ControlPanel.PAGE_MIN_WIDTH,
            -1,
        ), (req.width, req.height)


@check
def web_file_row_ignores_non_webpage_sources():
    """Video files or other modes must not leak into the Local web page row."""
    obj, _ = _panel(_base_config(), [])
    shown = {}

    class _Row:
        def set_title(self, title):
            shown["title"] = title

        def set_subtitle(self, subtitle):
            shown["subtitle"] = subtitle

    obj.web_file_row = _Row()
    obj.config = {
        "mode": "MODE_VIDEO",
        "data_source": {"Default": "/v/movie.mp4"},
        "video_dir": None,
    }
    obj._refresh_web_file_row()
    assert shown["title"] == "No page chosen", shown
    assert "HTML web page" in shown["subtitle"], shown


@check
def sidebar_status_text_maps_states():
    css, text = app.ControlPanel._sidebar_status_text(False, "MODE_VIDEO")
    assert (css, text) == ("error", "Server down"), (css, text)
    css, text = app.ControlPanel._sidebar_status_text(True, "MODE_PLAYLIST")
    assert (css, text) == ("success", "Ready — Playlist (muted)"), (css, text)
    css, text = app.ControlPanel._sidebar_status_text(True, "BOGUS")
    assert (css, text) == ("success", "Ready — Static wallpaper"), (css, text)


@check
def about_dialog_carries_identity_and_license():
    import os

    assert os.environ.get("DISPLAY"), "needs a display (run under Xvfb)"
    from gi.repository import Adw, Gtk

    Adw.init()
    obj = app.ControlPanel.__new__(app.ControlPanel)
    obj.version = "1.0"
    dialog = obj._make_about_dialog()
    assert dialog.get_application_name() == "Apricus"
    assert dialog.get_version() == "1.0"
    assert dialog.get_website() == "https://github.com/fabx-dev/apricus"
    assert dialog.get_license_type() == Gtk.License.GPL_3_0
    assert "Community fork of Hidamari" in dialog.get_comments()
    assert not dialog.get_copyright()
    assert any("fabx-dev" in dev for dev in dialog.get_developers())


@check
def sidebar_status_toggles_eq_and_dot():
    obj, _ = _panel(_base_config(), [])
    vis = {}

    class _W:
        def __init__(self, name):
            self._name = name

        def set_visible(self, visible):
            vis[self._name] = visible

        def set_text(self, text):
            pass

        def set_tooltip_text(self, text):
            pass

        def add_css_class(self, cls):
            pass

        def remove_css_class(self, cls):
            pass

    obj.sidebar_dot = _W("dot")
    obj.sidebar_status = _W("label")
    obj.sidebar_eq = _W("eq")
    obj._animations_enabled = lambda: True

    class _Srv:
        mode = "MODE_VIDEO"
        is_paused_by_user = False

    obj._refresh_sidebar_status(_Srv(), "MODE_VIDEO")
    assert vis == {"eq": True, "dot": False}, vis
    obj._refresh_sidebar_status(None, "MODE_NULL")
    assert vis == {"eq": False, "dot": True}, vis


@check
def autostart_enabled_reflects_disk():
    import tempfile

    import apricus.gui.gtk4app as appmod

    real = appmod.AUTOSTART_DESKTOP_PATH
    tmp = tempfile.mkdtemp(prefix="apricus-autostart-")
    appmod.AUTOSTART_DESKTOP_PATH = tmp + "/io.github.fabx_dev.Apricus.desktop"
    try:
        obj, _ = _panel(_base_config(), [])
        assert obj._autostart_enabled() is False
        open(appmod.AUTOSTART_DESKTOP_PATH, "w").write("[Desktop Entry]\n")
        assert obj._autostart_enabled() is True
    finally:
        appmod.AUTOSTART_DESKTOP_PATH = real


@check
def autostart_toggle_roundtrip_and_rollback():
    import apricus.gui.gtk4app as appmod

    real_path = appmod.AUTOSTART_DESKTOP_PATH
    real_setup = appmod.setup_autostart
    tmp = __import__("tempfile").mkdtemp(prefix="apricus-autostart-")
    appmod.AUTOSTART_DESKTOP_PATH = tmp + "/io.github.fabx_dev.Apricus.desktop"

    class _Switch:
        def __init__(self, active):
            self.active = active

        def get_active(self):
            return self.active

        def set_active(self, value):
            self.active = bool(value)

    def fake_setup(want):
        if want:
            open(appmod.AUTOSTART_DESKTOP_PATH, "w").write("x")
        else:
            import os

            if os.path.isfile(appmod.AUTOSTART_DESKTOP_PATH):
                os.remove(appmod.AUTOSTART_DESKTOP_PATH)

    appmod.setup_autostart = fake_setup
    try:
        obj, _ = _panel(_base_config(), [])
        seen_toasts, seen_errors = [], []
        obj._toast = lambda msg: seen_toasts.append(msg)
        obj._show_error = lambda msg: seen_errors.append(msg)
        switch = _Switch(True)
        obj._on_autostart_toggled(switch, None)
        assert obj._autostart_enabled() is True
        assert seen_toasts == ["Startup enabled"] and not seen_errors
        switch = _Switch(False)
        obj._on_autostart_toggled(switch, None)
        assert obj._autostart_enabled() is False
        assert seen_toasts[-1] == "Startup disabled"
        # Failure rolls the switch back and reports.
        def boom(want):
            raise OSError("read-only")

        appmod.setup_autostart = boom
        switch = _Switch(True)
        obj._on_autostart_toggled(switch, None)
        assert switch.get_active() is False
        assert seen_errors and not obj._autostart_enabled()
    finally:
        appmod.AUTOSTART_DESKTOP_PATH = real_path
        appmod.setup_autostart = real_setup


@check
def settings_flag_persists_and_calls_server():
    import apricus.gui.gtk4app as appmod

    class _Switch:
        def __init__(self, active):
            self.active = active

        def get_active(self):
            return self.active

    class _Server:
        pass

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, _ = _panel(_base_config(), [])
        server = _Server()
        obj._server = lambda: server
        obj._on_settings_flag(_Switch(True), None, "is_static_wallpaper")
        assert server.is_static_wallpaper is True
        assert store.config["is_static_wallpaper"] is True
        # Server absent: still persists locally.
        obj._server = lambda: None
        obj._on_settings_flag(_Switch(False), None, "is_pause_when_maximized")
        assert store.config["is_pause_when_maximized"] is False
    finally:
        appmod.ConfigUtil = old


@check
def settings_has_no_mute_volume_or_lucky():
    """Volume/Mute live only in Now Playing; Feeling Lucky was never a switch."""
    import os

    assert os.environ.get("DISPLAY"), "needs a display (run under Xvfb)"
    from gi.repository import Adw, Gtk

    Adw.init()
    obj = app.ControlPanel.__new__(app.ControlPanel)
    obj.version = "test"
    obj.config = dict(_base_config())
    obj.config["video_dir"] = None
    page = obj._settings_page()
    labels = []

    def walk(widget):
        if isinstance(widget, (Gtk.Label, Gtk.Button)):
            text = widget.get_label() if isinstance(widget, Gtk.Button) else widget.get_text()
            if text:
                labels.append(text)
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(page)
    joined = "\n".join(labels)
    assert "Mute" not in labels, labels
    assert "Volume" not in labels, labels
    assert "Lucky" not in joined and "lucky" not in joined, labels


@check
def stream_thumb_id_table():
    thumb_id = app.ControlPanel._youtube_thumb_id
    assert thumb_id("https://youtu.be/abc123XYZ_-") == "abc123XYZ_-"
    assert thumb_id("https://www.youtube.com/watch?v=abc123XYZ_-&t=10") == "abc123XYZ_-"
    assert thumb_id("https://www.youtube.com/shorts/abc123XYZ_-") == "abc123XYZ_-"
    assert thumb_id("https://www.youtube.com/live/abc123XYZ_-") == "abc123XYZ_-"
    assert thumb_id("https://www.twitch.tv/someone") == ""
    assert thumb_id("https://example.com/v.mp4") == ""
    assert thumb_id("") == ""
    assert thumb_id(None) == ""


@check
def stream_row_fetch_respects_type_and_size():
    import apricus.gui.gtk4app as appmod
    import requests as real_requests

    calls = []

    class _Resp:
        def __init__(self, body, ctype="image/jpeg"):
            self._body = body
            self.status_code = 200
            self.headers = {"Content-Type": ctype}

        def iter_content(self, size):
            calls.append("get")
            yield self._body

        def close(self):
            pass

    class _Requests:
        # fetch_public resolves requests lazily from the real module and
        # passes allow_redirects=False: accept and ignore extra kwargs.
        def get(self, url, **kwargs):
            assert "i.ytimg.com" in url, url
            return _Resp(b"\x89PNG\r\n\x1a\n" + b"0" * 100)

    real_get = real_requests.get
    real_requests.get = _Requests().get
    import apricus.utils as _U

    real_public = _U.public_http_url
    # DNS gating is covered by dedicated tests; here exercise type/size.
    _U.public_http_url = lambda url: url
    try:
        obj, _ = _panel(_base_config(), [])
        data = obj._fetch_row_thumb("https://youtu.be/abc123XYZ_-")
        assert data and data.startswith(b"\x89PNG"), data[:8] if data else None
        assert calls == ["get"]
        # Non-YouTube: no network at all.
        assert obj._fetch_row_thumb("https://www.twitch.tv/someone") is None
        assert calls == ["get"]
        # Oversize body: rejected.
        class _Big(_Requests):
            def get(self, url, **kwargs):
                return _Resp(b"0" * (512 * 1024 + 1))

        real_requests.get = _Big().get
        assert obj._fetch_row_thumb("https://youtu.be/abc123XYZ_-") is None
    finally:
        real_requests.get = real_get
        _U.public_http_url = real_public


@check
def stream_row_apply_validates_target():
    obj, _ = _panel(_base_config(), [])
    painted = []

    class _Pic:
        def set_filename(self, path):
            painted.append(("file", path))

        def set_visible(self, visible):
            painted.append(("visible", visible))

    class _Avatar:
        def set_visible(self, visible):
            painted.append(("avatar", visible))

    class _Row:
        def __init__(self, name, parent):
            self._name = name
            self._parent = parent

        def get_name(self):
            return self._name

        def get_parent(self):
            return self._parent

    obj._thumb_generation = 5
    row, pic, avatar = _Row("https://w.example/v", object()), _Pic(), _Avatar()
    row._thumb_picture, row._thumb_avatar = pic, avatar
    # Stale generation: untouched.
    assert obj._apply_row_thumb(4, row, "https://w.example/v", "/tmp/x.png") is False
    assert painted == []
    # Destroyed row: untouched.
    assert obj._apply_row_thumb(5, _Row("https://w.example/v", None), "https://w.example/v", "/tmp/x.png") is False
    assert painted == []
    # URL replaced: untouched.
    assert obj._apply_row_thumb(5, row, "https://other.example/", "/tmp/x.png") is False
    assert painted == []
    # Null path: untouched.
    assert obj._apply_row_thumb(5, row, "https://w.example/v", None) is False
    assert painted == []
    # Valid: file set, picture shown, avatar hidden.
    assert obj._apply_row_thumb(5, row, "https://w.example/v", "/tmp/x.png") is False
    assert ("file", "/tmp/x.png") in painted
    assert ("visible", True) in painted
    assert ("avatar", False) in painted


@check
def stream_row_widgets_have_thumb_slot_and_live_badge():
    import os

    assert os.environ.get("DISPLAY"), "needs a display (run under Xvfb)"
    from gi.repository import Adw, Gtk

    Adw.init()
    obj = app.ControlPanel.__new__(app.ControlPanel)
    row = obj._stream_row(
        {"url": "https://w.example/v", "title": "T", "site": "w", "kind": "page", "is_live": True},
        False,
    )
    pic = getattr(row, "_thumb_picture", None)
    assert pic is not None and isinstance(pic, Gtk.Picture)
    assert pic.get_size_request() == (64, 36)
    assert pic.get_visible() is False
    texts = []

    def walk(widget):
        if isinstance(widget, Gtk.Label) and widget.get_text():
            texts.append(widget.get_text())
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            walk(child)
            child = child.get_next_sibling()

    walk(row)
    assert "LIVE" in texts, texts
    plain = obj._stream_row(
        {"url": "https://w.example/v", "title": "T", "site": "w", "kind": "page"},
        False,
    )

    def walk2(widget, out):
        if isinstance(widget, Gtk.Label) and widget.get_text():
            out.append(widget.get_text())
        child = widget.get_first_child() if hasattr(widget, "get_first_child") else None
        while child is not None:
            walk2(child, out)
            child = child.get_next_sibling()

    texts2 = []
    walk2(plain, texts2)
    assert "LIVE" not in texts2, texts2


@check
def visible_stream_rows_on_mapped_window():
    """Real mapped window: visible-row computation must not raise and must
    find the rows (regression: wrong compute_point arity broke it live)."""
    import os
    import tempfile
    import time

    assert os.environ.get("DISPLAY"), "needs a display (run under Xvfb)"
    from gi.repository import Adw, GLib, Gtk

    from apricus.stream_thumbs import ThumbCache

    Adw.init()
    obj = app.ControlPanel.__new__(app.ControlPanel)
    obj.version = "test"
    obj.config = {
        "mode": "MODE_NULL",
        "data_source": {"Default": ""},
        "playlist": {"Default": []},
        "playlist_shuffle": False,
        "video_dir": None,
        "is_mute": False,
        "audio_volume": 50,
        "stream_history": [
            {
                "url": "https://w.example/1",
                "title": "One",
                "site": "w.example",
                "kind": "page",
                "last_used": 2,
            },
            {
                "url": "https://w.example/2",
                "title": "Two",
                "site": "w.example",
                "kind": "page",
                "last_used": 1,
            },
        ],
        "stream_favorites": [],
    }
    obj.playlist_paths = []
    obj._server = lambda: None
    obj._stream_thumb_cache = ThumbCache(tempfile.mkdtemp(prefix="apricus-thumbtest-"))
    page = obj._streaming_page()
    window = Adw.ApplicationWindow()
    window.set_default_size(900, 900)
    window.set_content(page)
    window.present()
    deadline = time.time() + 10
    rows = []
    while time.time() < deadline:
        while GLib.main_context_default().pending():
            GLib.main_context_default().iteration(False)
        rows = obj._visible_stream_rows()
        if len(rows) >= 2:
            break
        time.sleep(0.2)
    assert len(rows) == 2, [(r.get_name() if hasattr(r, "get_name") else None) for _, r, _ in rows]
    assert {url for _, _, url in rows} == {"https://w.example/1", "https://w.example/2"}
    obj._fetch_visible_thumbs()
    time.sleep(1)
    for _ in range(20):
        while GLib.main_context_default().pending():
            GLib.main_context_default().iteration(False)
    window.close()


@check
def stream_meta_title_persists_in_history():
    import apricus.gui.gtk4app as appmod

    store = _ConfigStore(_base_config())
    old = _patch_config(None, store)
    try:
        obj, _ = _panel(_base_config(), [])
        obj._record_stream_use("https://w.example/v", title="", kind="page")
        obj._update_stream_live_flag("https://w.example/v", False, "Real Title Here")
        entries = [e for e in store.config["stream_history"] if e["url"] == "https://w.example/v"]
        assert entries and entries[0]["title"] == "Real Title Here", entries
        # Empty title never wipes a known one.
        obj._update_stream_live_flag("https://w.example/v", True, "")
        entries = [e for e in store.config["stream_history"] if e["url"] == "https://w.example/v"]
        assert entries[0]["title"] == "Real Title Here", entries
        assert entries[0]["is_live"] is True
    finally:
        appmod.ConfigUtil = old


@check
def toast_without_overlay_is_noop():
    obj, _ = _panel(_base_config(), [])
    obj._toast("hello")  # must not raise without a ToastOverlay


@check
def capped_get_enforces_incremental_limit():
    """_capped_get must drop oversized bodies mid-stream (P1: unbounded
    downloads filled RAM before any post-hoc check)."""
    import apricus.gui.gtk4app as appmod

    class _Resp:
        closed = False

        def __init__(self, chunks):
            self._chunks = chunks
            self.status_code = 200
            self.headers = {}

        def iter_content(self, size):
            yield from self._chunks

        def close(self):
            self.closed = True

    made = []

    def fake_get(url, **kwargs):
        # fetch_public resolves requests at call time from the real module.
        assert kwargs.get("allow_redirects") is False, kwargs
        resp = _Resp(list(made.pop(0)))
        fake_get.last = resp
        return resp

    import requests as real_requests

    old = real_requests.get
    real_requests.get = fake_get
    try:
        made.append([b"x" * 100, b"y" * 100])
        assert appmod._capped_get("https://93.184.216.34/x", 5, 1024) == b"x" * 100 + b"y" * 100
        assert fake_get.last.closed, "socket must be closed"
        made.append([b"z" * 70000])
        assert appmod._capped_get("https://93.184.216.34/big", 5, 65536) is None
        assert fake_get.last.closed, "socket must be closed on overflow too"
    finally:
        real_requests.get = old


@check
def toast_records_message():
    obj, _ = _panel(_base_config(), [])
    seen = []

    class _Overlay:
        def add_toast(self, toast):
            seen.append(toast.get_title())

    obj.toasts = _Overlay()
    obj._toast("done")
    assert seen == ["done"], seen


@check
def now_playing_queue_rows_not_rebuilt_when_unchanged():
    """Repeated refreshes with the same queue must not destroy/recreate
    the Up-next rows (visible flash on every refresh, e.g. play/pause)."""

    class _Box:
        def __init__(self):
            self.children = ["row-0", "row-1"]
            self.removed = 0

        def get_first_child(self):
            return self.children[0] if self.children else None

        def remove(self, child):
            self.removed += 1
            self.children.remove(child)

        def append(self, child):
            self.children.append(child)

    class _Group:
        def __init__(self):
            self.visible = None

        def set_visible(self, visible):
            self.visible = visible

    obj, _ = _panel(_base_config(), [])
    obj.np_queue_group = _Group()
    obj.np_queue_box = _Box()
    obj.playlist_paths = ["/v/a.mp4", "/v/b.mp4"]
    obj._np_queue_shown = None
    obj._refresh_np_queue("MODE_PLAYLIST")
    first_children = list(obj.np_queue_box.children)
    assert obj.np_queue_box.removed > 0
    # Same queue again: no row is touched.
    obj.np_queue_box.removed = 0
    obj._refresh_np_queue("MODE_PLAYLIST")
    assert obj.np_queue_box.removed == 0
    assert list(obj.np_queue_box.children) == first_children
    # Changed queue: rebuilt.
    obj.playlist_paths = ["/v/b.mp4", "/v/a.mp4"]
    obj._refresh_np_queue("MODE_PLAYLIST")
    assert obj.np_queue_box.removed > 0


@check
def url_gates_reject_without_network():
    """SSRF gates: credentialed/non-http URLs and bad redirect landings
    are rejected before any socket is opened."""
    import apricus.gui.gtk4app as appmod

    def _bomb(*args, **kwargs):
        raise AssertionError("no network for rejected URLs")

    old_get, old_head = appmod.requests.get, appmod.requests.head
    appmod.requests.get = _bomb
    appmod.requests.head = _bomb
    try:
        obj, _ = _panel(_base_config(), [])
        assert obj._check_stream_url("http://user:pass@example.com/v") == "URL not supported."
        assert obj._check_stream_url("ftp://example.com/v") == "URL not supported."
        assert obj._check_direct_url("file:///etc/passwd") == "fallback"
        assert obj._check_direct_url("https://user@example.com/v.mp4") == "fallback"
        assert obj._check_web_url("http://user:pass@example.com/") is not None
        path, reason = obj._download_favicon("user@example.com")
        assert (path, reason) == (None, "error"), (path, reason)
        # RC2-SEC-03: non-public literals rejected before any socket.
        assert obj._check_stream_url("http://127.0.0.1/v") == (
            "Local network addresses are not supported."
        )
        assert obj._check_direct_url("http://10.0.0.1/v.mp4") == "fallback"
        assert obj._check_web_url("http://192.168.1.1/") is not None
    finally:
        appmod.requests.get, appmod.requests.head = old_get, old_head


@check
def redirect_landings_are_refused_without_contact():
    """Redirect chains are followed hop-by-hop; a hop to a non-public host
    stops the chain BEFORE any request to it (recorded request lists prove
    the forbidden host is never contacted). Covers favicon, row-thumb and
    _capped_get (hero/titles)."""
    import apricus.gui.gtk4app as appmod
    import requests as real_requests

    class _Resp:
        def __init__(self, status=200, ctype="image/png", body=b"", location=""):
            self.status_code = status
            self.headers = {"Content-Type": ctype}
            if location:
                self.headers["Location"] = location
            self._body = body

        def iter_content(self, size):
            yield self._body

        def close(self):
            pass

    requested = []

    def fake_get(url, **kwargs):
        requested.append(url)
        return fake_get.script.pop(0)

    old_get = real_requests.get
    real_requests.get = fake_get
    try:
        obj, _ = _panel(_base_config(), [])
        redir = _Resp(302, "text/html", b"", location="http://127.0.0.1:9000/x")

        # 1. favicon: public entry, redirect to loopback.
        fake_get.script = [redir]
        requested.clear()
        path, reason = obj._download_favicon("93.184.216.34")
        assert (path, reason) == (None, "error"), (path, reason)
        assert requested == ["https://93.184.216.34/favicon.ico"], requested

        # 2. row thumbnail: redirect from the fixed CDN host to loopback.
        fake_get.script = [redir]
        requested.clear()
        assert obj._fetch_row_thumb("https://youtu.be/0x5mf8BUJZY") is None
        assert requested == ["https://i.ytimg.com/vi/0x5mf8BUJZY/mqdefault.jpg"], requested

        # 2b. shared helper path (hero/titles) with the same shape.
        fake_get.script = [redir]
        requested.clear()
        assert appmod._capped_get("http://93.184.216.34/t.jpg", 5, 65536) is None
        assert requested == ["http://93.184.216.34/t.jpg"], requested

        # 3. legit redirect chain still lands data (no regression).
        fake_get.script = [
            _Resp(301, "text/html", b"", location="https://93.184.216.34/final"),
            _Resp(200, "image/jpeg", b"JPEGDATA"),
        ]
        requested.clear()
        assert appmod._capped_get("http://93.184.216.34/t.jpg", 5, 65536) == b"JPEGDATA"
        assert requested == [
            "http://93.184.216.34/t.jpg",
            "https://93.184.216.34/final",
        ], requested
    finally:
        real_requests.get = old_get


@check
def shell_refresh_reveals_banner_and_badges():
    import os
    import tempfile

    assert os.environ.get("DISPLAY"), "needs a display (run under Xvfb)"
    from gi.repository import Adw

    Adw.init()
    video_dir = tempfile.mkdtemp(prefix="apricus-shell-")
    obj = app.ControlPanel.__new__(app.ControlPanel)
    obj.version = "test"
    obj.config = {
        "mode": "MODE_NULL",
        "data_source": {"Default": ""},
        "playlist": {"Default": ["/a.mp4", "/b.mp4"]},
        "playlist_shuffle": False,
        "video_dir": video_dir,
        "is_mute": False,
        "audio_volume": 50,
    }
    obj.playlist_paths = ["/a.mp4", "/b.mp4"]
    obj._server = lambda: None
    obj._build_sidebar()
    obj._library_page()
    obj._playlist_page()
    obj._now_playing_page()
    obj._streaming_page()
    obj._webpage_page()
    obj._settings_page()
    banner = Adw.Banner()
    obj.server_banner = banner
    obj._refresh_all()
    assert banner.get_revealed(), "banner must show when the server is missing"
    assert not hasattr(obj, "sidebar_badges"), "badges removed for narrow sidebar"
    assert obj._app_logo_image(28).get_pixel_size() == 28


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


def test_apply_single_with_monitors_opens_chooser():
    _run("apply_single_with_monitors_opens_chooser")


def test_apply_single_with_one_monitor_still_opens_chooser():
    _run("apply_single_with_one_monitor_still_opens_chooser")


def test_monitor_menu_items_list_all_plus_monitors():
    _run("monitor_menu_items_list_all_plus_monitors")


def test_apply_video_to_monitor_calls_server_and_toasts():
    _run("apply_video_to_monitor_calls_server_and_toasts")


def test_apply_multi_selection_explains_itself():
    _run("apply_multi_explains_itself")


def test_apply_empty_selection_does_nothing():
    _run("apply_empty_does_nothing")


def test_apply_without_server_explains_itself():
    _run("apply_without_server_explains_itself")


def test_play_pause_without_server_explains_itself():
    _run("play_pause_without_server_explains_itself")


def test_apply_without_server_still_persists_queue():
    _run("apply_without_server_still_persists")


def _run_display(check):
    if not os.environ.get("DISPLAY"):
        pytest.skip("needs a display (run under Xvfb)")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src") + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(
        [sys.executable, "-c", DRIVER, check],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        env=env,
        timeout=120,
    )
    assert proc.returncode == 0, (
        f"isolated display check {check!r} failed:\n--- stdout ---\n{proc.stdout}"
        f"\n--- stderr ---\n{proc.stderr}"
    )


def test_sidebar_sections_are_four_in_order():
    _run("sidebar_sections_are_four_in_order")


def test_sidebar_selection_routes_to_stack():
    _run("sidebar_selection_routes_to_stack")


def test_select_section_falls_back_to_library():
    _run("select_section_falls_back_to_library")


def test_now_playing_text_shows_mode_and_current():
    _run("now_playing_text_shows_mode_and_current")


def test_queue_row_position_validates_targets():
    _run("queue_row_position_validates")


def test_queue_row_buttons_route_to_handlers():
    _run("queue_row_buttons_route_to_handlers")


def test_sidebar_builds_four_rows_and_pages():
    _run_display("sidebar_builds_four_rows_and_pages")


def test_library_add_all_appends_store_paths():
    _run("library_add_all_appends_store_paths")


def test_stream_url_check_accepts_and_rejects():
    _run("stream_url_check_accepts_and_rejects")


def test_stream_callsites_share_js_runtime_options():
    _run("stream_callsites_share_js_runtime_options")


def test_finish_stream_apply_persists_and_calls_server():
    _run("finish_stream_apply_records_and_opens_chooser")


def test_apply_stream_to_monitor_persists_and_calls_server():
    _run("apply_stream_to_monitor_persists_and_calls_server")


def test_finish_stream_apply_error_shows_no_server_call():
    _run("finish_stream_apply_error_shows_no_server_call")


def test_set_webpage_source_records_and_opens_chooser():
    _run("set_webpage_source_records_and_opens_chooser")


def test_apply_webpage_to_monitor_persists_and_calls_server():
    _run("apply_webpage_to_monitor_persists_and_calls_server")


def test_video_dir_chosen_persists_and_refreshes():
    _run("video_dir_chosen_persists_and_refreshes")


def test_lib_actions_gate_on_selection():
    _run("lib_actions_gate_on_selection")


def test_source_row_shows_folder_or_empty():
    _run("source_row_shows_folder_or_empty")


def test_toast_without_overlay_is_noop():
    _run("toast_without_overlay_is_noop")


def test_capped_get_enforces_incremental_limit():
    _run("capped_get_enforces_incremental_limit")


def test_url_gates_reject_without_network():
    _run("url_gates_reject_without_network")


def test_redirect_landings_are_refused_without_contact():
    _run("redirect_landings_are_refused_without_contact")


def test_stream_meta_title_persists_in_history():
    _run("stream_meta_title_persists_in_history")


def test_visible_stream_rows_on_mapped_window():
    _run_display("visible_stream_rows_on_mapped_window")


def test_stream_thumb_id_table():
    _run("stream_thumb_id_table")


def test_stream_row_fetch_respects_type_and_size():
    _run("stream_row_fetch_respects_type_and_size")


def test_stream_row_apply_validates_target():
    _run("stream_row_apply_validates_target")


def test_stream_row_widgets_have_thumb_slot_and_live_badge():
    _run_display("stream_row_widgets_have_thumb_slot_and_live_badge")


def test_autostart_enabled_reflects_disk():
    _run("autostart_enabled_reflects_disk")


def test_autostart_toggle_roundtrip_and_rollback():
    _run("autostart_toggle_roundtrip_and_rollback")


def test_settings_flag_persists_and_calls_server():
    _run("settings_flag_persists_and_calls_server")


def test_settings_has_no_mute_volume_or_lucky():
    _run_display("settings_has_no_mute_volume_or_lucky")


def test_sidebar_status_toggles_eq_and_dot():
    _run("sidebar_status_toggles_eq_and_dot")


def test_sidebar_status_text_maps_states():
    _run("sidebar_status_text_maps_states")


def test_about_dialog_carries_identity_and_license():
    _run_display("about_dialog_carries_identity_and_license")


def test_web_file_row_ignores_non_webpage_sources():
    _run("web_file_row_ignores_non_webpage_sources")


def test_hero_parity_streaming_webpage():
    _run_display("hero_parity_streaming_webpage")


def test_favicon_states_with_mocks():
    _run("favicon_states_with_mocks")


def test_favicon_regression_persists_after_retype():
    _run("favicon_regression_persists_after_retype")


def test_download_favicon_validates_and_normalizes():
    _run("download_favicon_validates_and_normalizes")


def test_plain_escapes_markup_and_busy_toggles_button():
    _run("plain_escapes_markup_and_busy_toggles_button")


def test_stream_service_names():
    _run("stream_service_names")


def test_stream_favicon_cache_miss_and_path():
    _run("stream_favicon_cache_miss_and_path")


def test_web_record_star_clear_roundtrip():
    _run("web_record_star_clear_roundtrip")


def test_web_sanitize_accepts_html_paths():
    _run("web_sanitize_accepts_html_paths")


def test_web_list_title_prefers_title_then_domain():
    _run("web_list_title_prefers_title_then_domain")


def test_web_hero_sync_and_file_row():
    _run("web_hero_sync_and_file_row")


def test_webpage_helpers():
    _run("webpage_helpers")


def test_stream_classify_and_errors():
    _run("stream_classify_and_errors")


def test_stream_record_favorite_remove_roundtrip():
    _run("stream_record_favorite_remove_roundtrip")


def test_stream_unstar_keeps_history_and_clear_keeps_saved():
    _run("stream_unstar_keeps_history_and_clear_keeps_saved")


def test_sidebar_streaming_selection_reloads_lists():
    _run("sidebar_streaming_selection_reloads_lists")


def test_refresh_now_playing_drives_hero_widgets():
    _run("refresh_now_playing_drives_hero_widgets")


def test_now_playing_playlist_follows_queue_head():
    _run("now_playing_playlist_follows_queue_head")


def test_now_playing_queue_rows_not_rebuilt_when_unchanged():
    _run_display("now_playing_queue_rows_not_rebuilt_when_unchanged")


def test_now_playing_helpers():
    _run("now_playing_helpers")


def test_show_hero_texture_swaps_visible_pic():
    _run("show_hero_texture_swaps_visible_pic")


def test_move_queue_item_reorders_and_reselects():
    _run("move_queue_item_reorders_and_reselects")


def test_drop_dest_math():
    _run("drop_dest_math")


def test_remove_offers_undo_and_restores():
    _run("remove_offers_undo_and_restores")


def test_clear_helper_empties_and_toasts():
    _run("clear_helper_empties_and_toasts")


def test_queue_actions_follow_selection_and_content():
    _run("queue_actions_follow_selection_and_content")


def test_toast_records_message():
    _run("toast_records_message")


def test_shell_refresh_reveals_banner_and_badges():
    _run_display("shell_refresh_reveals_banner_and_badges")
