# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK4 + Libadwaita control panel (migration target).

Talks to the UNCHANGED ApricusServer over the existing D-Bus API: no
backend or protocol changes. Started with APRICUS_GTK4=1 (see server).

Only GTK4-safe modules are imported here: commons, utils (ConfigUtil,
no Gtk at top level), mediacache. NEVER import apricus.gui.control,
apricus.menu or apricus.monitor (they pin GTK3/Gdk3 namespaces).
"""

import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from pydbus import SessionBus

from apricus.commons import (
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_VOLUME,
    DBUS_NAME_SERVER,
    LOGGER_NAME,
    MODE_PLAYLIST,
    PROJECT,
    resolve_video_dir,
)
from apricus.utils import ConfigUtil, get_video_paths, init_translations, setup_logging

logger = logging.getLogger(LOGGER_NAME)

APP_ID = f"{PROJECT}.gui4"


class ControlPanel(Adw.Application):
    def __init__(self, version, *args, **kwargs):
        super().__init__(
            *args,
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
            **kwargs,
        )
        self.version = version
        self.window = None
        self.config = ConfigUtil().load()

    def do_activate(self):
        if self.window is None:
            self.window = Adw.ApplicationWindow(application=self)
            self.window.set_title("Apricus")
            self.window.set_default_size(640, 420)
            self.stack = Adw.ViewStack()
            self.stack.add_titled(self._library_page(), "library", "Library")
            self.stack.add_titled(self._playlist_page(), "playlist", "Playlist")
            self.stack.add_titled(self._settings_page(), "settings", "Settings")
            switcher = Adw.ViewSwitcher()
            switcher.set_stack(self.stack)
            header = Adw.HeaderBar()
            header.set_title_widget(switcher)
            # Adw.ApplicationWindow content with a top header bar.
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(header)
            toolbar.set_content(self.stack)
            self.window.set_content(toolbar)
        self._refresh_all()
        self.window.present()

    def _server(self):
        try:
            return SessionBus().get(DBUS_NAME_SERVER)
        except GLib.Error:
            logger.error("[GUI4] Couldn't connect to server")
            return None

    # -- pages ---------------------------------------------------------

    def _library_page(self):
        from apricus.gui.gtk4lib import build_store, make_factory, selected_paths

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        self.library_status = Gtk.Label(label="")
        self.library_status.set_xalign(0)
        box.append(self.library_status)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        self.video_store = build_store([])
        self.video_selection = Gtk.MultiSelection.new(self.video_store)
        grid = Gtk.GridView(model=self.video_selection, factory=make_factory())
        grid.set_min_columns(2)
        grid.set_max_columns(6)
        scrolled.set_child(grid)
        box.append(scrolled)
        self.selection_label = Gtk.Label(label="")
        self.selection_label.set_xalign(0)
        box.append(self.selection_label)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.CENTER)
        apply_btn = Gtk.Button(label="Apply selected")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_library_apply)
        add_btn = Gtk.Button(label="Add to playlist")
        add_btn.connect("clicked", self.on_library_add)
        self.video_selection.connect("selection-changed", self._on_lib_selection)
        row.append(apply_btn)
        row.append(add_btn)
        play = Gtk.Button(label="Play/Pause")
        play.connect("clicked", self.on_play_pause)
        quit_btn = Gtk.Button(label="Quit")
        quit_btn.connect("clicked", lambda *_: self.quit())
        row.append(play)
        row.append(quit_btn)
        box.append(row)
        self._lib_selected_paths = selected_paths
        return box

    def _on_lib_selection(self, *_args):
        paths = self._lib_selected_paths(self.video_selection, self.video_store)
        n = len(paths)
        self.selection_label.set_text(
            "" if n == 0 else ("1 video selected" if n == 1 else f"{n} videos selected")
        )

    def on_library_apply(self, *_args):
        server = self._server()
        if server is None:
            return
        paths = self._lib_selected_paths(self.video_selection, self.video_store)
        if len(paths) == 1:
            server.video(paths[0], "")
            self._refresh_all()

    def on_library_add(self, *_args):
        server = self._server()
        paths = self._lib_selected_paths(self.video_selection, self.video_store)
        if not paths:
            return
        playlist = list((self.config.get("playlist") or {}).get("Default", []))
        for path in paths:
            if path not in playlist:
                playlist.append(path)
        self.config["playlist"]["Default"] = playlist
        from apricus.utils import ConfigUtil

        ConfigUtil().save(self.config)
        if server is not None:
            server.playlist(playlist, "all")
        self.config = ConfigUtil().load()
        self._refresh_all()

    def _playlist_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        self.playlist_status = Gtk.Label(label="")
        self.playlist_status.set_xalign(0)
        box.append(self.playlist_status)
        apply_btn = Gtk.Button(label="Apply playlist")
        apply_btn.add_css_class("suggested-action")
        apply_btn.set_halign(Gtk.Align.CENTER)
        apply_btn.connect("clicked", self.on_playlist_apply)
        box.append(apply_btn)
        return box

    def _settings_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        mute_row = Adw.ActionRow()
        mute_row.set_title("Mute")
        self.mute_switch = Gtk.Switch()
        self.mute_switch.set_valign(Gtk.Align.CENTER)
        self.mute_switch.connect("state-set", self.on_mute_toggled)
        mute_row.add_suffix(self.mute_switch)
        box.append(mute_row)
        vol_row = Adw.ActionRow()
        vol_row.set_title("Volume")
        self.volume_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume_scale.set_hexpand(True)
        self.volume_scale.set_valign(Gtk.Align.CENTER)
        self.volume_scale.connect("value-changed", self.on_volume_changed)
        vol_row.add_suffix(self.volume_scale)
        box.append(vol_row)
        return box

    # -- refresh + handlers ---------------------------------------------

    def _refresh_all(self):
        server = self._server()
        mode = server.mode if server is not None else self.config.get(CONFIG_KEY_MODE)
        n_videos = len(get_video_paths(resolve_video_dir(self.config)))
        self.library_status.set_text(
            f"Apricus {self.version} — mode: {mode}\n{n_videos} videos in library"
        )
        self._reload_library_store()
        playlist = (self.config.get("playlist") or {}).get("Default", [])
        self.playlist_status.set_text(
            f"{len(playlist)} videos in queue"
            + (" (muted by design)" if mode == MODE_PLAYLIST else "")
        )
        self.mute_switch.set_state(bool(self.config.get(CONFIG_KEY_MUTE)))
        self.volume_scale.set_value(int(self.config.get(CONFIG_KEY_VOLUME, 50)))

    def _reload_library_store(self):
        from apricus.gui.gtk4lib import VideoItem

        items = [VideoItem(p) for p in get_video_paths(resolve_video_dir(self.config))]
        self.video_store.splice(0, self.video_store.get_n_items(), items)
        self._on_lib_selection()

    def on_play_pause(self, *_args):
        server = self._server()
        if server is None:
            return
        prev = server.is_paused_by_user
        server.is_paused_by_user = not prev
        if not prev:
            server.pause_playback()
        else:
            server.start_playback()
        self._refresh_all()

    def on_playlist_apply(self, *_args):
        server = self._server()
        if server is None:
            return
        playlist = (self.config.get("playlist") or {}).get("Default", [])
        if playlist:
            server.playlist(playlist, "all")
        self.config = ConfigUtil().load()
        self._refresh_all()

    def on_mute_toggled(self, _switch, state):
        server = self._server()
        if server is not None:
            server.is_mute = state
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_MUTE] = state
        ConfigUtil().save(self.config)
        return False

    def on_volume_changed(self, scale):
        volume = int(scale.get_value())
        server = self._server()
        if server is not None:
            server.volume = volume
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_VOLUME] = volume
        ConfigUtil().save(self.config)


def main(version="devel", pkgdatadir="/usr/share/apricus", localedir="/usr/share/locale"):
    del pkgdatadir  # GTK4 UI has no compiled resources yet
    setup_logging()
    init_translations(localedir)
    Adw.init()
    app = ControlPanel(version)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
