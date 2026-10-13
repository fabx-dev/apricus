# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK4 + Libadwaita control panel (migration target).

Talks to the UNCHANGED ApricusServer over the existing D-Bus API: no
backend or protocol changes. Started with APRICUS_GTK4=1 (see server).

Only GTK4-safe modules are imported here: commons, utils (ConfigUtil,
no Gtk at top level), mediacache, monitor (major-version agnostic: it
reuses the already-loaded Gdk 4.0). NEVER import apricus.gui.control,
apricus.menu or apricus.player (they pin GTK3/Gdk3 namespaces or spawn
playback in the panel process).
"""

import logging
import os
import re
import sys
import threading
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
import requests
import yt_dlp
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk
from pydbus import SessionBus

from apricus.commons import (
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_PLAYLIST,
    CONFIG_KEY_STREAM_FAVORITES,
    CONFIG_KEY_STREAM_HISTORY,
    CONFIG_KEY_VIDEO_DIR,
    CONFIG_KEY_VOLUME,
    DBUS_NAME_SERVER,
    LOGGER_NAME,
    MODE_NULL,
    MODE_PLAYLIST,
    MODE_STREAM,
    MODE_VIDEO,
    MODE_WEBPAGE,
    PROJECT,
    resolve_video_dir,
)
from apricus.utils import ConfigUtil, get_video_paths, init_translations, setup_logging

logger = logging.getLogger(LOGGER_NAME)

APP_ID = f"{PROJECT}.gui4"


class ControlPanel(Adw.Application):
    # Human-readable mode labels (mode codes like MODE_VIDEO are
    # implementation details, never shown raw).
    MODE_LABELS = {
        MODE_VIDEO: "Single video",
        MODE_STREAM: "Streaming",
        MODE_WEBPAGE: "Web page",
        MODE_PLAYLIST: "Playlist (muted)",
        MODE_NULL: "Static wallpaper",
    }
    MODE_ICONS = {
        MODE_VIDEO: "media-playback-start-symbolic",
        MODE_STREAM: "network-transmit-receive-symbolic",
        MODE_WEBPAGE: "web-browser-symbolic",
        MODE_PLAYLIST: "list-queue-symbolic",
        MODE_NULL: "video-display-symbolic",
    }

    # Sidebar sections in order: (stack child name, title, icon name).
    SECTIONS = (
        ("library", "Library", "folder-videos-symbolic"),
        ("playlist", "Playlist", "view-list-symbolic"),
        ("now-playing", "Now Playing", "media-playback-start-symbolic"),
        ("streaming", "Streaming", "network-wireless-symbolic"),
        ("webpage", "Web Page", "web-browser-symbolic"),
        ("settings", "Settings", "emblem-system-symbolic"),
    )

    def __init__(self, version, *args, **kwargs):
        # Overridable for tests/smoke drivers: the session bus allows a
        # single owner per application ID, so parallel instances (e.g. an
        # Xvfb smoke driver next to the live panel) need distinct IDs.
        kwargs.setdefault("application_id", APP_ID)
        super().__init__(
            *args,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
            **kwargs,
        )
        self.version = version
        self.window = None
        self.toasts = None
        self.config = ConfigUtil().load()
        self.playlist_paths = list((self.config.get("playlist") or {}).get("Default", []))

    def do_activate(self):
        if self.window is None:
            self.window = Adw.ApplicationWindow(application=self)
            self.window.set_title("Apricus")
            self.window.set_default_size(900, 600)
            self.stack = Adw.ViewStack()
            self.stack.add_titled(self._library_page(), "library", "Library")
            self.stack.add_titled(self._playlist_page(), "playlist", "Playlist")
            self.stack.add_titled(self._now_playing_page(), "now-playing", "Now Playing")
            self.stack.add_titled(self._streaming_page(), "streaming", "Streaming")
            self.stack.add_titled(self._webpage_page(), "webpage", "Web Page")
            self.stack.add_titled(self._settings_page(), "settings", "Settings")
            self.stack.set_hexpand(True)
            self.sidebar = self._build_sidebar()
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            content.append(self.sidebar)
            content.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
            content.append(self.stack)
            self.toasts = Adw.ToastOverlay()
            self.toasts.set_child(content)
            header = Adw.HeaderBar()
            brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            brand.append(self._app_logo_image(28))
            brand.append(Gtk.Label(label="Apricus"))
            header.set_title_widget(brand)
            # Adw.ApplicationWindow content with a top header bar.
            toolbar = Adw.ToolbarView()
            toolbar.add_top_bar(header)
            self.server_banner = Adw.Banner()
            self.server_banner.set_title("Background service unreachable")
            self.server_banner.set_button_label("Retry")
            self.server_banner.connect("button-clicked", lambda *_: self._refresh_all())
            toolbar.add_top_bar(self.server_banner)
            toolbar.set_content(self.toasts)
            self.window.set_content(toolbar)
            self._load_css()
        self._refresh_all()
        # Dev/test aid: preselect a section (e.g. screenshots).
        initial_tab = os.environ.get("APRICUS_GTK4_TAB")
        self._select_section(initial_tab or "library")
        self.window.present()

    def _build_sidebar(self):
        sidebar = Gtk.ListBox()
        sidebar.add_css_class("navigation-sidebar")
        self.sidebar_rows = {}
        self.sidebar_badges = {}
        for name, title, icon_name in self.SECTIONS:
            row = Adw.ActionRow()
            row.set_name(name)
            row.set_title(title)
            icon = Gtk.Image.new_from_icon_name(icon_name)
            row.add_prefix(icon)
            if name in ("library", "playlist"):
                badge = Gtk.Label(label="")
                badge.add_css_class("caption")
                badge.add_css_class("sidebar-badge")
                row.add_suffix(badge)
                self.sidebar_badges[name] = badge
            self.sidebar_rows[name] = row
            sidebar.append(row)
        sidebar.connect("row-selected", self._on_sidebar_selected)
        return sidebar

    @staticmethod
    def _app_logo_path():
        return Path(__file__).resolve().parent.parent.parent.parent / "res" / "apricus.svg"

    def _app_logo_image(self, size):
        icon_name = "io.github.fabx_dev.Apricus"
        try:
            has_icon = Gtk.IconTheme.get_for_display(Gdk.Display.get_default()).has_icon(icon_name)
        except Exception:  # noqa: BLE001 - theme lookup must never break startup
            has_icon = False
        if has_icon:
            image = Gtk.Image.new_from_icon_name(icon_name)
        else:
            logo = self._app_logo_path()
            if logo.exists():
                image = Gtk.Image.new_from_file(str(logo))
            else:
                image = Gtk.Image.new_from_icon_name("video-display-symbolic")
        image.set_pixel_size(size)
        return image

    @staticmethod
    def _load_css():
        css = Gtk.CssProvider()
        css.load_from_data(
            b".sidebar-badge { background: alpha(@accent_bg_color, 0.18); "
            b"border-radius: 999px; padding: 1px 8px; }"
            b".np-caption { background: alpha(black, 0.55); border-radius: 12px; "
            b"padding: 12px; }"
            b".np-caption .title-1 { color: white; }"
            b".np-caption .title-4 { color: alpha(white, 0.85); }"
            b".np-badge { background: alpha(white, 0.22); color: white; "
            b"border-radius: 999px; padding: 1px 10px; }"
            b".np-accent { color: @accent_color; }"
            b".np-eq-bar { background: white; border-radius: 2px; "
            b"animation: np-eq 0.9s ease-in-out infinite alternate; }"
            b".np-eq-bar:nth-child(2) { animation-delay: 0.15s; }"
            b".np-eq-bar:nth-child(3) { animation-delay: 0.3s; }"
            b".np-eq-bar:nth-child(4) { animation-delay: 0.45s; }"
            b".np-eq-bar:nth-child(5) { animation-delay: 0.6s; }"
            b".np-eq-bar:nth-child(6) { animation-delay: 0.75s; }"
            b".np-eq-bar:nth-child(7) { animation-delay: 0.9s; }"
            b"@keyframes np-eq { from { opacity: 0.35; } to { opacity: 1; } }"
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _toast(self, message):
        overlay = getattr(self, "toasts", None)
        if overlay is None:
            return
        overlay.add_toast(Adw.Toast.new(message))

    def _refresh_badges(self, n_videos, n_queue):
        badges = getattr(self, "sidebar_badges", None)
        if not badges:
            return
        library = badges.get("library")
        if library is not None:
            library.set_text(str(n_videos))
        playlist = badges.get("playlist")
        if playlist is not None:
            playlist.set_text(str(n_queue))
            playlist.set_visible(n_queue > 0)

    def _on_sidebar_selected(self, _sidebar, row):
        if row is not None:
            self.stack.set_visible_child_name(row.get_name())
            if row.get_name() == "streaming":
                self._reload_stream_lists()

    def _select_section(self, name):
        names = [section for section, _, _ in self.SECTIONS]
        target = name if name in names else "library"
        row = self.sidebar.get_row_at_index(names.index(target))
        self.sidebar.select_row(row)

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
        hint = Gtk.Label(label="Click to select. Ctrl+click or Shift+click selects more than one.")
        hint.set_xalign(0)
        hint.add_css_class("dim-label")
        box.append(hint)
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
        source_row = Adw.ActionRow()
        source_row.set_title("No folder chosen")
        source_row.set_subtitle("Choose the folder scanned for local videos")
        choose_btn = Gtk.Button(label="Choose…")
        choose_btn.set_tooltip_text("Choose the folder scanned for local videos")
        choose_btn.set_valign(Gtk.Align.CENTER)
        choose_btn.connect("clicked", self.on_choose_video_dir)
        source_row.add_suffix(choose_btn)
        open_btn = Gtk.Button()
        open_btn.set_icon_name("folder-open-symbolic")
        open_btn.set_tooltip_text("Open the video folder in the file manager")
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", self.on_open_video_dir)
        source_row.add_suffix(open_btn)
        self.source_row = source_row
        box.append(source_row)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.FILL)
        apply_btn = Gtk.Button(label="Apply selected")
        apply_btn.add_css_class("suggested-action")
        apply_btn.set_tooltip_text("Use the selected video as live wallpaper")
        apply_btn.connect("clicked", self.on_library_apply)
        self.apply_btn = apply_btn
        add_split = Adw.SplitButton()
        add_split.set_child(Gtk.Label(label="Add to playlist"))
        add_split.set_tooltip_text("Add the selected videos to the playlist queue")
        add_split.connect("clicked", self.on_library_add)
        add_menu = Gtk.Popover()
        add_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        add_selected_btn = Gtk.Button(label="Add selected")
        add_selected_btn.add_css_class("flat")
        add_selected_btn.connect("clicked", lambda *_a: (add_menu.popdown(), self.on_library_add()))
        add_all_btn = Gtk.Button(label="Add all")
        add_all_btn.add_css_class("flat")
        add_all_btn.set_tooltip_text("Add every video in the library to the playlist queue")
        add_all_btn.connect("clicked", lambda *_a: (add_menu.popdown(), self.on_library_add_all()))
        add_box.append(add_selected_btn)
        add_box.append(add_all_btn)
        add_menu.set_child(add_box)
        add_split.set_popover(add_menu)
        self.add_split = add_split
        self.video_selection.connect("selection-changed", self._on_lib_selection)
        row.append(apply_btn)
        lib_spacer = Gtk.Box()
        lib_spacer.set_hexpand(True)
        row.append(lib_spacer)
        row.append(add_split)
        play = Gtk.Button(label="Play/Pause")
        play.set_tooltip_text("Toggle playback")
        play.connect("clicked", self.on_play_pause)
        row.append(play)
        quit_btn = Gtk.Button(label="Quit")
        quit_btn.set_tooltip_text("Close the panel. Wallpaper keeps running")
        quit_btn.connect("clicked", lambda *_: self.quit())
        row.append(quit_btn)
        more_btn = Gtk.MenuButton()
        more_btn.set_icon_name("view-more-symbolic")
        more_btn.set_tooltip_text("More library actions")
        more_menu = Gtk.Popover()
        more_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        refresh_btn = Gtk.Button(label="Refresh")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text("Rescan the source folder")
        refresh_btn.connect("clicked", lambda *_a: (more_menu.popdown(), self.on_refresh_library()))
        more_box.append(refresh_btn)
        more_menu.set_child(more_box)
        more_btn.set_popover(more_menu)
        row.append(more_btn)
        box.append(row)
        refresh_shortcut = Gtk.Shortcut.new(
            Gtk.KeyvalTrigger.new(Gdk.KEY_r, Gdk.ModifierType.CONTROL_MASK),
            Gtk.CallbackAction.new(lambda *_a: (self.on_refresh_library(), True)),
        )
        shortcuts = Gtk.ShortcutController()
        shortcuts.add_shortcut(refresh_shortcut)
        box.add_controller(shortcuts)
        self._lib_selected_paths = selected_paths
        self._refresh_source_row()
        return box

    def _on_lib_selection(self, *_args):
        paths = self._lib_selected_paths(self.video_selection, self.video_store)
        n = len(paths)
        self.selection_label.set_text(
            "" if n == 0 else ("1 video selected" if n == 1 else f"{n} videos selected")
        )
        self._update_lib_actions(n)

    def _update_lib_actions(self, n_selected):
        can_act = n_selected > 0
        for attr in ("apply_btn", "add_split"):
            widget = getattr(self, attr, None)
            if widget is not None:
                widget.set_sensitive(can_act)

    def _refresh_source_row(self, n_videos=None):
        row = getattr(self, "source_row", None)
        if row is None:
            return
        video_dir = resolve_video_dir(getattr(self, "config", None) or {})
        if video_dir is None:
            row.set_title("No folder chosen")
            row.set_subtitle("Choose the folder scanned for local videos")
        else:
            row.set_title(os.path.basename(video_dir) or video_dir)
            subtitle = video_dir
            if n_videos is not None:
                subtitle += f" — {n_videos} videos"
            row.set_subtitle(subtitle)

    def _show_server_missing(self):
        self._show_error("Background service unreachable.\nStart Apricus to apply wallpapers.")

    def on_library_apply(self, *_args):
        server = self._server()
        if server is None:
            self._show_server_missing()
            return
        paths = self._lib_selected_paths(self.video_selection, self.video_store)
        if len(paths) == 1:
            self._show_monitor_chooser(paths[0])
        elif len(paths) > 1:
            # Single-video apply has no multi-selection semantics (unlike the
            # playlist queue). Unlike the GTK3 panel — which silently applies
            # only the first item — say so explicitly instead of failing
            # silently.
            self._show_apply_needs_single(len(paths))

    def _monitor_choices(self):
        """[(key, label)] for the monitor chooser, same keys as the server."""
        from apricus.monitor import Monitors

        try:
            monitors = Monitors().get_monitors()
        except Exception as e:  # noqa: BLE001 - no monitor list, apply to all
            logger.warning(f"[GUI4] Could not list monitors: {e}")
            return []
        return [(key, mon.name or key) for key, mon in monitors.items()]

    def _monitor_menu_items(self, _path):
        """Pure label list for the chooser: [("Set for All", "")] + monitors."""
        return [("Set for All", "")] + [
            (f"Set for {label}", key) for key, label in self._monitor_choices()
        ]

    def _show_monitor_chooser(self, path):
        if not self._monitor_choices():
            self._apply_video_to_monitor(path, "")
            return
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, key in self._monitor_menu_items(path):
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.connect(
                "clicked",
                lambda *_a, k=key: (popover.popdown(), self._apply_video_to_monitor(path, k)),
            )
            box.append(btn)
        popover.set_child(box)
        popover.set_parent(self.apply_btn)
        popover.popup()

    def _apply_video_to_monitor(self, path, monitor):
        server = self._server()
        if server is None:
            return
        logger.info(f"[GUI4] Local Video Set To {path} For Monitor {monitor or 'all'}")
        server.video(path, monitor)
        self.config = ConfigUtil().load()
        self._refresh_all()
        target = "all monitors" if not monitor else monitor
        self._toast(f"Wallpaper applied on {target}")

    def _show_apply_needs_single(self, count):
        dialog = Adw.AlertDialog(
            heading="Apply needs a single video",
            body=(
                f"You selected {count} videos, but Apply sets one wallpaper.\n"
                "Select just one video to apply it, or add them to the "
                "playlist to play them all in a loop."
            ),
        )
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.connect("response", lambda *_: dialog.close())
        dialog.present(self.window)

    def _persist_queue_to_config(self):
        playlist = self.config.get(CONFIG_KEY_PLAYLIST) or {}
        self.config[CONFIG_KEY_PLAYLIST] = playlist
        playlist["Default"] = list(self.playlist_paths)
        ConfigUtil().save(self.config)

    def _apply_queue(self):
        """Persist the edited queue, hand it to the server, reload, refresh.

        The in-memory queue (edited via Up/Down/Remove/Clear/Add) is the
        source of truth, mirroring the GTK3 panel: the server persists it
        again on playlist() and plays it.
        """
        if not self.playlist_paths:
            return
        logger.info(f"[GUI4] Playlist apply ({len(self.playlist_paths)} videos)")
        self._persist_queue_to_config()
        server = self._server()
        if server is not None:
            server.playlist(self.playlist_paths, "all")
        self.config = ConfigUtil().load()
        self.playlist_paths = list((self.config.get(CONFIG_KEY_PLAYLIST) or {}).get("Default", []))
        self._refresh_all()
        self._toast(f"Playlist applied — {len(self.playlist_paths)} videos in queue")

    def on_library_add(self, *_args):
        paths = self._lib_selected_paths(self.video_selection, self.video_store)
        self._add_paths_to_queue(paths)

    def on_library_add_all(self, *_args):
        store = self.video_store
        paths = [store.get_item(i).path for i in range(store.get_n_items())]
        self._add_paths_to_queue(paths)

    def _add_paths_to_queue(self, paths):
        if not paths:
            return
        for path in paths:
            if path not in self.playlist_paths:
                self.playlist_paths.append(path)
        self._apply_queue()

    def on_choose_video_dir(self, *_args):
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose video folder")
        dialog.select_folder(self.window, None, self._on_video_dir_chosen)

    def _on_video_dir_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error as e:
            logger.debug("[GUI4] Folder chooser dismissed: %s", e.message)
            return
        path = folder.get_path()
        if not path:
            return
        logger.info(f"[GUI4] Video folder: {path}")
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_VIDEO_DIR] = path
        ConfigUtil().save(self.config)
        self._refresh_all()

    def on_open_video_dir(self, *_args):
        video_dir = resolve_video_dir(self.config)
        if video_dir is None:
            self.on_choose_video_dir()
            return
        try:
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(video_dir))
        except GLib.Error as e:
            logger.warning(f"[GUI4] Could not open video folder: {e.message}")

    def on_refresh_library(self, *_args):
        self._refresh_all()

    def _playlist_page(self):
        from apricus.gui.gtk4lib import VideoItem

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(16)
        box.set_margin_bottom(16)
        box.set_margin_start(16)
        box.set_margin_end(16)
        self.playlist_status = Gtk.Label(label="")
        self.playlist_status.set_xalign(0)
        box.append(self.playlist_status)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        self.queue_store = Gio.ListStore.new(VideoItem)
        self.queue_selection = Gtk.SingleSelection.new(self.queue_store)
        self.queue_selection.set_autoselect(False)
        self.queue_selection.set_can_unselect(True)
        listview = Gtk.ListView(model=self.queue_selection, factory=self._queue_factory())
        scrolled.set_child(listview)
        box.append(scrolled)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.FILL)
        apply_btn = Gtk.Button(label="Apply playlist")
        apply_btn.add_css_class("suggested-action")
        apply_btn.set_tooltip_text("Apply current order to playback")
        apply_btn.connect("clicked", self.on_playlist_apply)
        row.append(apply_btn)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        self.shuffle_check = Gtk.ToggleButton(label="Shuffle")
        self.shuffle_check.set_tooltip_text("Shuffle playlist order randomly")
        self.shuffle_check.connect("toggled", self.on_shuffle_toggled)
        rm_btn = Gtk.Button(label="Remove selected")
        rm_btn.set_tooltip_text("Remove the selected video from the playlist")
        rm_btn.connect("clicked", self.on_queue_remove)
        self.queue_remove_btn = rm_btn
        clear_btn = Gtk.Button(label="Clear")
        clear_btn.add_css_class("destructive-action")
        clear_btn.set_tooltip_text("Remove all videos from the playlist")
        clear_btn.connect("clicked", self.on_queue_clear)
        self.queue_clear_btn = clear_btn
        for w in (self.shuffle_check, rm_btn, clear_btn):
            row.append(w)
        self.queue_selection.connect("selection-changed", self._on_queue_selection)
        box.append(row)
        alt_up = Gtk.Shortcut.new(
            Gtk.KeyvalTrigger.new(Gdk.KEY_Up, Gdk.ModifierType.ALT_MASK),
            Gtk.CallbackAction.new(lambda *_a: (self._move_selected(-1), True)),
        )
        alt_down = Gtk.Shortcut.new(
            Gtk.KeyvalTrigger.new(Gdk.KEY_Down, Gdk.ModifierType.ALT_MASK),
            Gtk.CallbackAction.new(lambda *_a: (self._move_selected(1), True)),
        )
        queue_shortcuts = Gtk.ShortcutController()
        queue_shortcuts.add_shortcut(alt_up)
        queue_shortcuts.add_shortcut(alt_down)
        box.add_controller(queue_shortcuts)
        self._update_queue_actions()
        return box

    def _queue_factory(self):
        from apricus.gui.gtk4lib import VideoItem, load_texture_async  # noqa: F401 (type reference)

        factory = Gtk.SignalListItemFactory()

        def _setup(_factory, item):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            num = Gtk.Label()
            num.set_width_chars(3)
            thumb = Gtk.Picture()
            thumb.set_size_request(96, 54)
            thumb.set_can_shrink(True)
            name = Gtk.Label()
            name.set_xalign(0)
            name.set_hexpand(True)
            from gi.repository import Pango

            name.set_ellipsize(Pango.EllipsizeMode.END)
            row.append(num)
            row.append(thumb)
            row.append(name)
            for icon_name, tooltip, handler in (
                ("go-up-symbolic", "Move up", self._on_queue_row_up),
                ("go-down-symbolic", "Move down", self._on_queue_row_down),
                ("user-trash-symbolic", "Remove", self._on_queue_row_remove),
            ):
                btn = Gtk.Button()
                btn.set_icon_name(icon_name)
                btn.set_tooltip_text(tooltip)
                btn.set_valign(Gtk.Align.CENTER)
                btn.connect("clicked", handler, item)
                row.append(btn)
            more_btn = Gtk.MenuButton()
            more_btn.set_icon_name("view-more-symbolic")
            more_btn.set_tooltip_text("More actions")
            more_btn.set_valign(Gtk.Align.CENTER)
            more_menu = Gtk.Popover()
            more_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            for label, tip, handler in (
                ("Move to top", "Move to the first position", self._on_queue_row_top),
                ("Move to bottom", "Move to the last position", self._on_queue_row_bottom),
                (
                    "Show in folder",
                    "Open the containing folder in the file manager",
                    self._on_queue_row_show_in_folder,
                ),
                ("Remove", "Remove from the playlist", self._on_queue_row_remove),
            ):
                entry = Gtk.Button(label=label)
                entry.add_css_class("flat")
                entry.set_tooltip_text(tip)
                entry.connect(
                    "clicked",
                    lambda *_a, h=handler: (more_menu.popdown(), h(None, item)),
                )
                more_box.append(entry)
            more_menu.set_child(more_box)
            more_btn.set_popover(more_menu)
            row.append(more_btn)
            drag = Gtk.DragSource.new()
            drag.set_actions(Gdk.DragAction.MOVE)
            drag.connect("prepare", self._on_queue_drag_prepare, item)
            drag.connect("drag-end", self._on_queue_drag_end)
            row.add_controller(drag)
            drop = Gtk.DropTarget.new(str, Gdk.DragAction.MOVE)
            drop.connect("drop", self._on_queue_row_drop, item)
            row.add_controller(drop)
            item.set_child(row)
            item._num = num
            item._thumb = thumb
            item._name = name
            item._bound_path = None

        def _bind(_factory, item):
            video = item.get_item()
            if video is None:
                return
            pos = item.get_position()
            item._num.set_text("" if pos == Gtk.INVALID_LIST_POSITION else str(pos + 1))
            item._name.set_text(video.name)
            item._bound_path = video.path
            if video.texture is not None:
                item._thumb.set_paintable(video.texture)
                return
            item._thumb.set_paintable(None)

            def _paint(texture):
                if item._bound_path != video.path:
                    return
                item._thumb.set_paintable(texture)

            load_texture_async(video, _paint)

        factory.connect("setup", _setup)
        factory.connect("bind", _bind)
        return factory

    def _queue_row_position(self, list_item):
        """Validate a per-row action target; None when the row is stale."""
        pos = list_item.get_position()
        if pos == Gtk.INVALID_LIST_POSITION:
            return None
        if list_item.get_item() is None:
            return None
        if pos >= len(self.playlist_paths):
            return None
        return pos

    def _on_queue_row_up(self, _button, list_item):
        pos = self._queue_row_position(list_item)
        if pos is not None:
            self.queue_selection.set_selected(pos)
            self.on_queue_up()

    def _on_queue_row_down(self, _button, list_item):
        pos = self._queue_row_position(list_item)
        if pos is not None:
            self.queue_selection.set_selected(pos)
            self.on_queue_down()

    def _on_queue_row_remove(self, _button, list_item):
        pos = self._queue_row_position(list_item)
        if pos is not None:
            self.queue_selection.set_selected(pos)
            self.on_queue_remove()

    def _on_queue_row_top(self, _button, list_item):
        pos = self._queue_row_position(list_item)
        if pos is not None:
            self._move_queue_item(pos, 0)

    def _on_queue_row_bottom(self, _button, list_item):
        pos = self._queue_row_position(list_item)
        if pos is not None:
            self._move_queue_item(pos, len(self.playlist_paths) - 1)

    def _on_queue_row_show_in_folder(self, _button, list_item):
        pos = self._queue_row_position(list_item)
        if pos is None:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(
                GLib.filename_to_uri(os.path.dirname(self.playlist_paths[pos]))
            )
        except GLib.Error as e:
            logger.warning(f"[GUI4] Could not open containing folder: {e.message}")

    def _queue_selected_index(self):
        selected = self.queue_selection.get_selected()
        if selected == Gtk.INVALID_LIST_POSITION:
            return None
        return selected

    def _reload_queue_store(self):
        from apricus.gui.gtk4lib import VideoItem

        items = [VideoItem(p) for p in self.playlist_paths]
        self.queue_store.splice(0, self.queue_store.get_n_items(), items)

    def on_queue_up(self, *_args):
        idx = self._queue_selected_index()
        if idx is not None:
            self._move_queue_item(idx, idx - 1)

    def on_queue_down(self, *_args):
        idx = self._queue_selected_index()
        if idx is not None:
            self._move_queue_item(idx, idx + 1)

    def _move_selected(self, delta):
        idx = self._queue_selected_index()
        if idx is not None:
            self._move_queue_item(idx, idx + delta)

    def _move_queue_item(self, src, dest):
        """Move one queue entry, clamp, reselect. Returns new index or None."""
        n = len(self.playlist_paths)
        if n == 0 or src is None or dest is None:
            return None
        src = max(0, min(src, n - 1))
        dest = max(0, min(dest, n - 1))
        if src == dest:
            return src
        path = self.playlist_paths.pop(src)
        self.playlist_paths.insert(dest, path)
        self._reload_queue_store()
        self.queue_selection.set_selected(dest)
        self._refresh_playlist_status()
        return dest

    @staticmethod
    def _drop_dest(src, target_pos, lower_half):
        """Final index when dropping src onto target_pos (post-pop semantics
        of _move_queue_item). Pure helper, unit-tested."""
        if src < target_pos:
            return target_pos if lower_half else target_pos - 1
        if src > target_pos:
            return target_pos + 1 if lower_half else target_pos
        return target_pos

    def _on_queue_drag_prepare(self, _source, _x, _y, list_item):
        pos = self._queue_row_position(list_item)
        if pos is None:
            return None
        value = GObject.Value(str, str(pos))
        return Gdk.ContentProvider.new_for_value(value)

    def _on_queue_drag_end(self, *_args):
        pass

    def _on_queue_row_drop(self, _target, value, x, y, list_item):
        try:
            src = int(value)
        except (TypeError, ValueError):
            return False
        target_pos = self._queue_row_position(list_item)
        if target_pos is None:
            return False
        child = list_item.get_child()
        height = child.get_allocation().height if child is not None else 0
        lower_half = height > 0 and y > height / 2
        self._move_queue_item(src, self._drop_dest(src, target_pos, lower_half))
        return True

    def on_queue_remove(self, *_args):
        idx = self._queue_selected_index()
        if idx is None or idx >= len(self.playlist_paths):
            return
        path = self.playlist_paths.pop(idx)
        self._reload_queue_store()
        self._refresh_playlist_status()
        self._update_queue_actions()
        self._toast_with_undo(
            f"Removed {os.path.basename(path)}",
            lambda: self._undo_remove(idx, path),
        )

    def _undo_remove(self, idx, path):
        self.playlist_paths.insert(min(idx, len(self.playlist_paths)), path)
        self._reload_queue_store()
        if self.playlist_paths:
            self.queue_selection.set_selected(min(idx, len(self.playlist_paths) - 1))
        self._refresh_playlist_status()
        self._update_queue_actions()

    def _toast_with_undo(self, message, on_undo):
        overlay = getattr(self, "toasts", None)
        if overlay is None:
            return None
        toast = Adw.Toast.new(message)
        toast.set_button_label("Undo")
        toast.connect("button-clicked", lambda *_: on_undo())
        overlay.add_toast(toast)
        return toast

    def on_queue_clear(self, *_args):
        if not self.playlist_paths:
            return
        dialog = Adw.AlertDialog(
            heading="Clear playlist?",
            body=(
                f"This will remove all {len(self.playlist_paths)} videos "
                "from the playlist. This action cannot be undone."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("clear", "Clear")
        dialog.set_response_appearance("clear", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, r: self._do_queue_clear() if r == "clear" else None)
        dialog.present(self.window)

    def _do_queue_clear(self):
        self.playlist_paths = []
        self._reload_queue_store()
        self._refresh_playlist_status()
        self._update_queue_actions()
        self._toast("Playlist cleared")

    def _on_queue_selection(self, *_args):
        self._update_queue_actions()

    def _update_queue_actions(self):
        has_selection = self._queue_selected_index() is not None
        has_items = bool(getattr(self, "playlist_paths", None))
        remove_btn = getattr(self, "queue_remove_btn", None)
        if remove_btn is not None:
            remove_btn.set_sensitive(has_selection)
        clear_btn = getattr(self, "queue_clear_btn", None)
        if clear_btn is not None:
            clear_btn.set_sensitive(has_items)

    def on_shuffle_toggled(self, check):
        self.config["playlist_shuffle"] = bool(check.get_active())
        from apricus.utils import ConfigUtil

        ConfigUtil().save(self.config)

    def _refresh_playlist_status(self):
        n = len(self.playlist_paths)
        server = self._server()
        mode = server.mode if server is not None else self.config.get(CONFIG_KEY_MODE)
        if n == 0:
            text = "Queue empty — add videos from Library"
        elif mode == MODE_PLAYLIST:
            text = f"{n} videos in queue (muted by design)"
        else:
            text = f"{n} videos in queue (paused)"
        self.playlist_status.set_text(text)

    @staticmethod
    def _clamp_page(box, maximum_size=700):
        clamp = Adw.Clamp()
        clamp.set_maximum_size(maximum_size)
        clamp.set_child(box)
        return clamp

    def _now_playing_page(self):
        from gi.repository import Pango

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        self.np_generation = 0
        self._np_current_path = None
        # Content (hero + transport + details + queue), hidden on empty state.
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        hero = Gtk.Frame()
        hero.add_css_class("card")
        overlay = Gtk.Overlay()
        self.np_stack = Gtk.Stack()
        self.np_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.np_stack.set_transition_duration(400)
        self.np_pic_a = self._np_picture()
        self.np_pic_b = self._np_picture()
        self.np_stack.add_named(self.np_pic_a, "a")
        self.np_stack.add_named(self.np_pic_b, "b")
        self._np_visible_pic = "a"
        overlay.set_child(self.np_stack)
        caption = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        caption.add_css_class("np-caption")
        caption.set_valign(Gtk.Align.END)
        caption.set_margin_start(12)
        caption.set_margin_end(12)
        caption.set_margin_bottom(12)
        badge_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.np_badge = Gtk.Label(label="")
        self.np_badge.add_css_class("np-badge")
        badge_row.append(self.np_badge)
        self.np_eq = self._np_equalizer()
        badge_row.append(self.np_eq)
        caption.append(badge_row)
        self.np_title = Gtk.Label(label="")
        self.np_title.add_css_class("title-1")
        self.np_title.set_xalign(0)
        self.np_title.set_ellipsize(Pango.EllipsizeMode.END)
        caption.append(self.np_title)
        # Kept for headless-tested text logic (mirrors title + subtitle).
        self.now_playing_status = Gtk.Label(label="")
        self.now_playing_status.set_visible(False)
        caption.append(self.now_playing_status)
        self.np_subtitle = Gtk.Label(label="")
        self.np_subtitle.add_css_class("title-4")
        self.np_subtitle.set_xalign(0)
        caption.append(self.np_subtitle)
        overlay.add_overlay(caption)
        hero.set_child(overlay)
        content.append(hero)
        transport = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        transport.set_halign(Gtk.Align.FILL)
        self.np_play_btn = Gtk.Button()
        self.np_play_btn.add_css_class("circular")
        self.np_play_btn.add_css_class("suggested-action")
        self.np_play_btn.set_tooltip_text("Play / Pause")
        self.np_play_btn.connect("clicked", self.on_play_pause)
        transport.append(self.np_play_btn)
        self.np_mute_btn = Gtk.ToggleButton()
        self.np_mute_btn.add_css_class("flat")
        self.np_mute_btn.set_tooltip_text("Mute / Unmute")
        self.np_mute_btn.connect("toggled", self.on_np_mute_toggled)
        mute_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.np_mute_icon = Gtk.Image()
        self.np_mute_label = Gtk.Label(label="Mute")
        mute_box.append(self.np_mute_icon)
        mute_box.append(self.np_mute_label)
        self.np_mute_btn.set_child(mute_box)
        transport.append(self.np_mute_btn)
        self.np_volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.np_volume.set_size_request(220, -1)
        self.np_volume.set_valign(Gtk.Align.CENTER)
        self.np_volume.set_tooltip_text("Volume")
        self.np_volume.connect("value-changed", self.on_volume_changed)
        vol_icon = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        vol_icon.set_valign(Gtk.Align.CENTER)
        transport.append(vol_icon)
        transport.append(self.np_volume)
        vol_spacer = Gtk.Box()
        vol_spacer.set_hexpand(True)
        transport.append(vol_spacer)
        content.append(transport)
        self.np_details = Adw.PreferencesGroup()
        self.np_details.set_title("Details")
        self.np_detail_rows = {}
        self.np_detail_icons = {}
        for key, title, icon_name in (
            ("line1", "Resolution", "video-display-symbolic"),
            ("line2", "Duration", "alarm-symbolic"),
            ("line3", "Format", "text-x-generic-symbolic"),
            ("line4", "Folder", "folder-symbolic"),
        ):
            action_row = Adw.ActionRow()
            action_row.set_title(title)
            icon = Gtk.Image.new_from_icon_name(icon_name)
            action_row.add_prefix(icon)
            self.np_details.add(action_row)
            self.np_detail_rows[key] = action_row
            self.np_detail_icons[key] = icon
        self.np_folder_btn = Gtk.Button()
        self.np_folder_btn.set_icon_name("folder-open-symbolic")
        self.np_folder_btn.set_tooltip_text("Open the containing folder")
        self.np_folder_btn.set_valign(Gtk.Align.CENTER)
        self.np_folder_btn.connect("clicked", self.on_np_open_folder)
        self.np_detail_rows["line4"].add_suffix(self.np_folder_btn)
        content.append(self.np_details)
        self.np_queue_group = Adw.PreferencesGroup()
        self.np_queue_group.set_title("Up next")
        self.np_queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.np_queue_group.add(self.np_queue_box)
        content.append(self.np_queue_group)
        self.np_content = content
        box.append(content)
        self.np_empty = Adw.StatusPage()
        self.np_empty.set_icon_name("video-display-symbolic")
        self.np_empty.set_title("Nothing playing")
        self.np_empty.set_description("Choose a video from the library to start watching.")
        open_lib_btn = Gtk.Button(label="Open library")
        open_lib_btn.add_css_class("suggested-action")
        open_lib_btn.connect("clicked", lambda *_: self._select_section("library"))
        self.np_empty.add_css_class("compact")
        box.append(self.np_empty)
        self.np_empty.set_child(open_lib_btn)
        return self._clamp_page(box, 800)

    @staticmethod
    def _np_picture():
        picture = Gtk.Picture()
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(640, 360)
        picture.set_can_shrink(True)
        return picture

    @staticmethod
    def _np_equalizer():
        eq = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        eq.set_valign(Gtk.Align.CENTER)
        for _ in range(7):
            bar = Gtk.Box()
            bar.set_size_request(3, 12)
            bar.add_css_class("np-eq-bar")
            eq.append(bar)
        return eq

    @staticmethod
    def _fmt_duration(seconds):
        """mm:ss or h:mm:ss; 'Live' for None."""
        if seconds is None:
            return "Live"
        total = int(seconds)
        hours, rest = divmod(total, 3600)
        minutes, secs = divmod(rest, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{secs:02d}"
        return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def _np_badge(mode, paused):
        if mode == MODE_PLAYLIST:
            return "Playlist in loop"
        if mode == MODE_WEBPAGE:
            return "Web page"
        if mode in (MODE_VIDEO, MODE_STREAM):
            return "Paused" if paused else "Playing"
        return "Static"

    def _refresh_now_playing(self):
        server = self._server()
        mode = server.mode if server is not None else self.config.get(CONFIG_KEY_MODE)
        current = (self.config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "")
        paused = bool(server.is_paused_by_user) if server is not None else True
        has_content = bool(current)
        content = getattr(self, "np_content", None)
        empty = getattr(self, "np_empty", None)
        if content is not None:
            content.set_visible(has_content)
        if empty is not None:
            empty.set_visible(not has_content)
        if not has_content:
            return
        self.np_badge.set_text(self._np_badge(mode, paused))
        playing = not paused and mode in (MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST)
        settings = Gtk.Settings.get_default()
        animations = bool(settings and settings.props.gtk_enable_animations)
        self.np_eq.set_visible(bool(playing and animations))
        title = self._display_source(current)
        self.np_title.set_text(title)
        self.np_title.set_tooltip_text(current)
        self.np_subtitle.set_text(self._mode_text(mode))
        self.now_playing_status.set_text(self._now_playing_text(mode, current))
        self.np_play_btn.set_icon_name(
            "media-playback-start-symbolic" if paused else "media-playback-pause-symbolic"
        )
        is_playlist = mode == MODE_PLAYLIST
        self.np_volume.set_sensitive(not is_playlist)
        self.np_mute_btn.set_sensitive(not is_playlist)
        if current != self._np_current_path:
            self._np_current_path = current
            self.np_generation += 1
            self._load_np_hero(current, self.np_generation)
        self._refresh_np_transport(server)
        self._refresh_np_details(mode, current)
        self._refresh_np_queue(mode)

    def _refresh_np_transport(self, server):
        muted = bool(self.config.get(CONFIG_KEY_MUTE))
        volume = int(self.config.get(CONFIG_KEY_VOLUME, 50))
        if server is not None:
            try:
                muted = bool(server.is_mute)
                volume = int(server.volume)
            except GLib.Error:
                pass
        mute_btn = self.np_mute_btn
        if mute_btn.get_active() != muted:
            mute_btn.set_active(muted)
        mute_btn.set_tooltip_text("Unmute" if muted else "Mute")
        self.np_mute_icon.set_from_icon_name(self._mute_icon(muted, volume))
        self.np_mute_label.set_text("Muted" if muted else "Mute")
        if int(self.np_volume.get_value()) != volume:
            self.np_volume.set_value(volume)

    @staticmethod
    def _mute_icon(muted, volume):
        if muted or volume == 0:
            return "audio-volume-muted-symbolic"
        if volume < 34:
            return "audio-volume-low-symbolic"
        if volume < 67:
            return "audio-volume-medium-symbolic"
        return "audio-volume-high-symbolic"

    def on_np_mute_toggled(self, button):
        state = bool(button.get_active())
        server = self._server()
        if server is not None:
            try:
                server.is_mute = state
            except GLib.Error:
                pass
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_MUTE] = state
        ConfigUtil().save(self.config)
        self.np_mute_icon.set_from_icon_name(
            self._mute_icon(state, int(self.np_volume.get_value()))
        )
        self.np_mute_label.set_text("Muted" if state else "Mute")
        button.set_tooltip_text("Unmute" if state else "Mute")

    def on_np_open_folder(self, *_args):
        current = self._np_current_path or ""
        if not current or "://" in current or not os.path.isfile(current):
            return
        try:
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(os.path.dirname(current)))
        except GLib.Error as e:
            logger.warning(f"[GUI4] Could not open containing folder: {e.message}")

    def _find_queue_item(self, path):
        for store in (getattr(self, "video_store", None), getattr(self, "queue_store", None)):
            if store is None:
                continue
            for i in range(store.get_n_items()):
                item = store.get_item(i)
                if item is not None and item.path == path:
                    return item
        return None

    def _show_hero_texture(self, texture, generation):
        if texture is None or generation != self.np_generation:
            return
        target = self.np_pic_b if self._np_visible_pic == "a" else self.np_pic_a
        target_name = "b" if self._np_visible_pic == "a" else "a"
        target.set_paintable(texture)
        self.np_stack.set_visible_child_name(target_name)
        self._np_visible_pic = target_name

    def _load_np_hero(self, path, generation):
        from apricus.gui.gtk4lib import VideoItem, load_texture_async

        video = self._find_queue_item(path) or VideoItem(path)

        def _paint(texture):
            self._show_hero_texture(texture, generation)

        if video.texture is not None:
            _paint(video.texture)
            return
        (self.np_pic_b if self._np_visible_pic == "a" else self.np_pic_a).set_paintable(None)
        load_texture_async(video, _paint, width=640)

    @staticmethod
    def _stream_thumb_url(meta):
        url = (meta or {}).get("thumbnail")
        return url or None

    def _apply_np_thumb(self, url, generation, data):
        if generation != self.np_generation or url != self._np_current_path:
            return False
        try:
            from gi.repository import GdkPixbuf

            loader = GdkPixbuf.PixbufLoader.new()
            loader.write_bytes(GLib.Bytes(data))
            loader.close()
            pixbuf = loader.get_pixbuf()
            texture = Gdk.Texture.new_for_pixbuf(pixbuf) if pixbuf is not None else None
        except Exception as e:  # noqa: BLE001 - hero simply stays empty
            logger.debug(f"[GUI4] Stream thumbnail failed for {url}: {e}")
            return False
        self._show_hero_texture(texture, generation)
        return False

    def _refresh_np_details(self, mode, current):
        group = getattr(self, "np_details", None)
        if group is None:
            return
        if mode == MODE_VIDEO and current and os.path.isfile(current):
            group.set_visible(True)
            for row in self.np_detail_rows.values():
                row.set_subtitle("…")
            generation = self.np_generation
            threading.Thread(
                target=self._probe_np_details, args=(current, generation), daemon=True
            ).start()
        elif mode == MODE_STREAM and current:
            group.set_visible(True)
            for row in self.np_detail_rows.values():
                row.set_subtitle("…")
            generation = self.np_generation
            threading.Thread(
                target=self._info_np_stream, args=(current, generation), daemon=True
            ).start()
        else:
            group.set_visible(False)

    def _probe_np_details(self, path, generation):
        from apricus import mediacache

        try:
            width, height, duration = mediacache.probe_video(path)
        except Exception:  # noqa: BLE001 - best effort, rows show dashes
            width, height, duration = None, None, None
        info = {
            "line1": (
                "Resolution",
                f"{width}×{height}" if width and height else "—",
                "video-display-symbolic",
            ),
            "line2": ("Duration", self._fmt_duration(duration), "alarm-symbolic"),
            "line3": (
                "Format",
                os.path.splitext(path)[1].lstrip(".").upper() or "—",
                "text-x-generic-symbolic",
            ),
            "line4": (
                os.path.basename(os.path.dirname(path)) or "Folder",
                os.path.dirname(path),
                "folder-symbolic",
            ),
        }
        GLib.idle_add(self._apply_np_details, generation, info, path)

    def _info_np_stream(self, url, generation):
        info = {
            "line1": ("Title", "—", "applications-multimedia-symbolic"),
            "line2": ("Site", self._display_source(url), "web-browser-symbolic"),
            "line3": ("Duration", "Live", "alarm-symbolic"),
            "line4": ("Link", url, "web-browser-symbolic"),
        }
        thumb_url = None
        try:
            with yt_dlp.YoutubeDL({"noplaylist": True, "socket_timeout": 15, "quiet": True}) as ydl:
                meta = ydl.extract_info(url, download=False) or {}
            title = meta.get("title")
            if title:
                info["line1"] = ("Title", title, "applications-multimedia-symbolic")
            uploader = meta.get("uploader") or meta.get("channel")
            if uploader:
                info["line2"] = (
                    "Site",
                    f"{self._display_source(url)} — {uploader}",
                    "web-browser-symbolic",
                )
            duration = meta.get("duration")
            if duration is not None:
                info["line3"] = ("Duration", self._fmt_duration(duration), "alarm-symbolic")
            height = meta.get("height")
            formats = meta.get("formats") or []
            if height is None:
                heights = [f.get("height") for f in formats if f.get("height")]
                height = max(heights) if heights else None
            if height:
                info["line4"] = ("Quality", f"{height}p", "video-display-symbolic")
            thumb_url = self._stream_thumb_url(meta)
        except Exception as e:  # noqa: BLE001 - rows keep safe defaults
            logger.debug(f"[GUI4] Stream info failed for {url}: {e}")
        GLib.idle_add(self._apply_np_details, generation, info, None)
        if thumb_url:
            try:
                response = requests.get(thumb_url, timeout=15)
                data = response.content or None
            except Exception as e:  # noqa: BLE001 - hero simply stays empty
                logger.debug(f"[GUI4] Stream thumbnail download failed: {e}")
                data = None
            if data:
                GLib.idle_add(self._apply_np_thumb, url, generation, data)

    def _apply_np_details(self, generation, info, folder_path):
        if generation != getattr(self, "np_generation", generation):
            return False
        for key, (title, subtitle, icon_name) in info.items():
            row = self.np_detail_rows.get(key)
            if row is None:
                continue
            row.set_title(title)
            row.set_subtitle(subtitle)
            icon = getattr(self, "np_detail_icons", {}).get(key)
            if icon is not None:
                icon.set_from_icon_name(icon_name)
                if title == "Site":
                    icon.add_css_class("np-accent")
                else:
                    icon.remove_css_class("np-accent")
        folder_btn = getattr(self, "np_folder_btn", None)
        if folder_btn is not None:
            folder_btn.set_visible(folder_path is not None)
        return False

    def _refresh_np_queue(self, mode):
        group = getattr(self, "np_queue_group", None)
        box = getattr(self, "np_queue_box", None)
        if group is None or box is None:
            return
        queue = list(getattr(self, "playlist_paths", None) or [])
        show = mode == MODE_PLAYLIST and bool(queue)
        group.set_visible(show)
        if not show:
            return
        while True:
            child = box.get_first_child()
            if child is None:
                break
            box.remove(child)
        from apricus.gui.gtk4lib import VideoItem, load_texture_async

        for pos, path in enumerate(queue[:3]):
            row = Adw.ActionRow()
            row.set_title(os.path.basename(path))
            row.set_subtitle(f"n. {pos + 1} di {len(queue)}")
            thumb = Gtk.Picture()
            thumb.set_size_request(96, 54)
            thumb.set_can_shrink(True)
            row.add_prefix(thumb)
            box.append(row)
            video = self._find_queue_item(path) or VideoItem(path)

            def _paint(texture, picture=thumb, expected=path):
                item = self._find_queue_item(expected)
                if item is not None and item.texture is not None:
                    picture.set_paintable(item.texture)
                elif texture is not None:
                    picture.set_paintable(texture)

            if video.texture is not None:
                thumb.set_paintable(video.texture)
            else:
                load_texture_async(video, _paint)

    @staticmethod
    def _mode_text(mode):
        return ControlPanel.MODE_LABELS.get(mode, ControlPanel.MODE_LABELS[MODE_NULL])

    @staticmethod
    def _mode_icon(mode):
        return ControlPanel.MODE_ICONS.get(mode, ControlPanel.MODE_ICONS[MODE_NULL])

    @staticmethod
    def _now_playing_text(mode, current_path):
        label = ControlPanel._mode_text(mode)
        if current_path:
            return f"{label}\n{ControlPanel._display_source(current_path)}"
        return label

    @staticmethod
    def _display_source(source):
        """Short readable form: basename for files, domain for URLs."""
        if "://" in source:
            try:
                from urllib.parse import urlparse

                host = urlparse(source).hostname or source
                return host
            except Exception:  # noqa: BLE001 - fall back to raw source
                return source
        return os.path.basename(source) or source

    def _streaming_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        title = Gtk.Label(label="Streaming")
        title.set_xalign(0)
        box.append(title)
        self.stream_entry = Gtk.Entry()
        self.stream_entry.set_placeholder_text("https://… — YouTube, Twitch, live, direct video")
        self.stream_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.PRIMARY, "web-browser-symbolic"
        )
        self.stream_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY, "edit-paste-symbolic"
        )
        self.stream_entry.set_icon_tooltip_text(
            Gtk.EntryIconPosition.SECONDARY, "Paste from clipboard"
        )
        self.stream_entry.connect("activate", self.on_stream_apply)
        self.stream_entry.connect("icon-press", self._on_stream_icon_press)
        self.stream_entry.connect("changed", self._on_stream_entry_changed)
        box.append(self.stream_entry)
        self.stream_type_row = Adw.ActionRow()
        self.stream_type_row.set_title("Detected type")
        self.stream_type_row.set_subtitle("Paste a link to detect its type")
        self.stream_type_icon = Gtk.Image.new_from_icon_name("web-browser-symbolic")
        self.stream_type_row.add_prefix(self.stream_type_icon)
        box.append(self.stream_type_row)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.CENTER)
        try_btn = Gtk.Button(label="Try 10s")
        try_btn.set_tooltip_text("Preview for 10 seconds, then pause")
        try_btn.connect("clicked", self.on_stream_try)
        row.append(try_btn)
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_stream_apply)
        row.append(apply_btn)
        box.append(row)
        self.stream_apply_btn = apply_btn
        self.saved_group = Adw.PreferencesGroup()
        self.saved_group.set_title("Saved sources")
        self.saved_list = Gtk.ListBox()
        self.saved_list.add_css_class("boxed-list")
        self.saved_list.connect("row-activated", self._on_saved_row_activated)
        self.saved_group.add(self.saved_list)
        box.append(self.saved_group)
        recent_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        recent_label = Gtk.Label(label="Recent")
        recent_label.set_xalign(0)
        recent_label.set_hexpand(True)
        recent_box.append(recent_label)
        clear_recent_btn = Gtk.Button(label="Clear")
        clear_recent_btn.connect("clicked", self.on_clear_stream_history)
        recent_box.append(clear_recent_btn)
        box.append(recent_box)
        self.recent_list = Gtk.ListBox()
        self.recent_list.add_css_class("boxed-list")
        self.recent_list.connect("row-activated", self._on_saved_row_activated)
        box.append(self.recent_list)
        self.stream_empty = Adw.StatusPage()
        self.stream_empty.set_icon_name("network-wireless-symbolic")
        self.stream_empty.set_title("No saved sources yet")
        self.stream_empty.set_description(
            "Paste a YouTube, Twitch or direct video link above. You will find it here."
        )
        paste_btn = Gtk.Button(label="Paste from clipboard")
        paste_btn.add_css_class("suggested-action")
        paste_btn.connect("clicked", self._on_stream_paste)
        self.stream_empty.set_child(paste_btn)
        box.append(self.stream_empty)
        drop_files = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_files.connect("drop", self._on_stream_drop)
        box.add_controller(drop_files)
        drop_text = Gtk.DropTarget.new(str, Gdk.DragAction.COPY)
        drop_text.connect("drop", self._on_stream_drop)
        box.add_controller(drop_text)
        self._update_stream_type()
        self._reload_stream_lists()
        return self._clamp_page(box)

    def _on_stream_icon_press(self, entry, position):
        if position == Gtk.EntryIconPosition.SECONDARY:
            self._on_stream_paste()

    def _on_stream_paste(self, *_args):
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.read_text_async(None, self._on_stream_clipboard_text)

    def _on_stream_clipboard_text(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            return
        if text and text.strip():
            self.stream_entry.set_text(text.strip())
            self._update_stream_type()

    def _on_stream_entry_changed(self, _entry):
        self._update_stream_type()

    def _update_stream_type(self):
        icon = getattr(self, "stream_type_icon", None)
        row = getattr(self, "stream_type_row", None)
        if icon is None or row is None:
            return
        url = self.stream_entry.get_text().strip()
        if not url:
            icon.set_from_icon_name("web-browser-symbolic")
            row.set_subtitle("Paste a link to detect its type")
            return
        kind = self._classify_stream_url(url)
        icon.set_from_icon_name(self.STREAM_KIND_ICONS.get(kind, "web-browser-symbolic"))
        row.set_subtitle(
            {
                "direct": "Direct video — plays without extraction",
                "page": f"Streaming page via yt-dlp — {self._stream_site(url)}",
                "collection": "Playlist or channel — only single videos play",
                "invalid": "Not a valid http(s) URL",
            }.get(kind, "")
        )

    def _stream_row_title(self, entry):
        title = entry.get("title") or ""
        site = entry.get("site") or self._stream_site(entry.get("url", ""))
        if title:
            return title if len(title) <= 60 else title[:57] + "…"
        return site

    def _history_entry(self, url):
        for entry in self._load_stream_data()[0]:
            if entry["url"] == url:
                return entry
        return {
            "url": url,
            "title": "",
            "site": self._stream_site(url),
            "kind": "page",
            "last_used": 0,
        }

    def _stream_row(self, entry, favorite, allow_delete=False):
        url = entry["url"]
        row = Adw.ActionRow()
        row.set_name(url)
        row.set_title(self._stream_row_title(entry))
        row.set_subtitle(url)
        kind_icon = Gtk.Image.new_from_icon_name(
            self.STREAM_KIND_ICONS.get(entry.get("kind") or "page", "web-browser-symbolic")
        )
        row.add_prefix(kind_icon)
        star = Gtk.ToggleButton()
        star.add_css_class("flat")
        star.set_icon_name("starred-symbolic" if favorite else "non-starred-symbolic")
        star.set_tooltip_text("Remove from saved" if favorite else "Save")
        star.set_active(favorite)
        star.set_valign(Gtk.Align.CENTER)
        star.connect("toggled", self._on_star_toggled, url)
        row.add_suffix(star)
        play = Gtk.Button()
        play.set_icon_name("media-playback-start-symbolic")
        play.set_tooltip_text("Apply this source now")
        play.set_valign(Gtk.Align.CENTER)
        play.connect("clicked", self._on_saved_play, url)
        row.add_suffix(play)
        if allow_delete:
            trash = Gtk.Button()
            trash.set_icon_name("user-trash-symbolic")
            trash.set_tooltip_text("Remove from saved (kept in recent)")
            trash.set_valign(Gtk.Align.CENTER)
            trash.connect("clicked", self._on_saved_delete, url)
            row.add_suffix(trash)
        return row

    def _clear_listbox(self, listbox):
        while True:
            child = listbox.get_first_child()
            if child is None:
                break
            listbox.remove(child)

    def _reload_stream_lists(self):
        if getattr(self, "saved_list", None) is None:
            return
        history, favorites = self._load_stream_data()
        fav_set = set(favorites)
        self._clear_listbox(self.saved_list)
        for url in favorites[:20]:
            entry = next((e for e in history if e["url"] == url), None)
            if entry is None:
                entry = self._history_entry(url)
            self.saved_list.append(self._stream_row(entry, True, True))
        self._clear_listbox(self.recent_list)
        for entry in history[:8]:
            self.recent_list.append(self._stream_row(entry, entry["url"] in fav_set))
        empty = not favorites and not history
        self.saved_group.set_visible(not empty)
        self.recent_list.set_visible(not empty)
        self.stream_empty.set_visible(empty)

    def _on_saved_row_activated(self, _listbox, row):
        url = row.get_name()
        if url:
            self.stream_entry.set_text(url)
            self._update_stream_type()

    def _on_star_toggled(self, button, url):
        active = bool(button.get_active())
        button.set_icon_name("starred-symbolic" if active else "non-starred-symbolic")
        _history, favorites = self._load_stream_data()
        if active and url not in favorites:
            favorites.insert(0, url)
        elif not active and url in favorites:
            favorites.remove(url)
        history, _favorites = self._load_stream_data()
        self._save_stream_data(history, favorites)
        self._reload_stream_lists()

    def _on_saved_delete(self, _button, url):
        _history, favorites = self._load_stream_data()
        if url in favorites:
            favorites.remove(url)
        history, _favorites = self._load_stream_data()
        self._save_stream_data(history, favorites)
        self._reload_stream_lists()

    def _on_saved_play(self, _button, url):
        self.stream_entry.set_text(url)
        self._update_stream_type()
        self.on_stream_apply()

    def on_clear_stream_history(self, *_args):
        history, favorites = self._load_stream_data()
        fav_set = set(favorites)
        self._save_stream_data([e for e in history if e["url"] in fav_set], favorites)
        self._reload_stream_lists()
        self._toast("Stream history cleared")

    def on_stream_try(self, *_args):
        url = self.stream_entry.get_text().strip()
        if not url:
            return
        kind = self._classify_stream_url(url)
        if kind == "invalid":
            self._show_error("Not a valid http(s) URL.")
            return
        if kind == "collection":
            self._show_collection_warning(url)
            return
        threading.Thread(target=self._validate_and_try, args=(url, kind), daemon=True).start()

    def _validate_and_try(self, url, kind="page"):
        if kind == "direct" and self._check_direct_url(url) is None:
            error = None
        else:
            error = self._check_stream_url(url)
        GLib.idle_add(self._finish_stream_try, url, error, kind)

    def _finish_stream_try(self, url, error, kind="page"):
        if error is not None:
            self._show_error(f"Failed to stream {url}.\nError:\n{error}")
            return False
        previous = (self.config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "")
        server = self._server()
        if server is not None:
            server.stream(url)
        self._record_stream_use(url, site=self._stream_site(url), kind=kind)
        self._reload_stream_lists()
        self._refresh_all()
        if getattr(self, "_try_timeout", None) is not None:
            GLib.source_remove(self._try_timeout)
        self._try_timeout = GLib.timeout_add_seconds(10, self._end_stream_try, previous)
        toast = Adw.Toast.new("Previewing for 10 seconds")
        toast.set_button_label("Stop now")
        toast.connect("button-clicked", self._cancel_stream_try, previous)
        overlay = getattr(self, "toasts", None)
        if overlay is not None:
            overlay.add_toast(toast)
        return False

    def _end_stream_try(self, previous):
        self._try_timeout = None
        server = self._server()
        if server is not None:
            try:
                server.pause_playback()
            except GLib.Error:
                pass
        self._toast("Preview finished — Apply to keep watching")
        return False

    def _cancel_stream_try(self, _toast, previous):
        if getattr(self, "_try_timeout", None) is not None:
            GLib.source_remove(self._try_timeout)
            self._try_timeout = None
        server = self._server()
        if server is not None:
            try:
                if previous:
                    server.stream(previous)
                else:
                    server.pause_playback()
            except GLib.Error:
                pass
        self._refresh_all()

    def _on_stream_drop(self, _target, value, _x, _y):
        text = None
        if isinstance(value, Gio.File):
            uri = value.get_uri()
            if uri and uri.startswith(("http://", "https://")):
                text = uri
        elif isinstance(value, str) and value.strip().startswith(("http://", "https://")):
            text = value.strip().split()[0]
        if text:
            self.stream_entry.set_text(text)
            self._update_stream_type()
            return True
        return False

    STREAM_KIND_ICONS = {
        "direct": "video-display-symbolic",
        "page": "web-browser-symbolic",
        "collection": "view-list-symbolic",
    }

    @staticmethod
    def _classify_stream_url(url):
        """direct (playable file) / page (extract via yt-dlp) /
        collection (playlist/channel, unsupported) / invalid."""
        low = ((url or "").strip()).lower()
        if not low.startswith(("http://", "https://")):
            return "invalid"
        if (
            "list=" in low
            or "/playlist" in low
            or "/channel/" in low
            or low.rstrip("/").endswith("/videos")
        ):
            return "collection"
        if re.search(r"\.(mp4|webm|mov|m3u8|mpd)(\?|#|$)", low):
            return "direct"
        return "page"

    @staticmethod
    def _stream_site(url):
        try:
            from urllib.parse import urlparse

            return urlparse(url).hostname or url
        except Exception:  # noqa: BLE001 - fall back to raw URL
            return url

    def _load_stream_data(self):
        config = getattr(self, "config", None) or {}
        history = ConfigUtil._sanitize_stream_entries(config.get(CONFIG_KEY_STREAM_HISTORY) or [])
        favorites = [
            u for u in (config.get(CONFIG_KEY_STREAM_FAVORITES) or []) if isinstance(u, str)
        ]
        return history, favorites

    def _save_stream_data(self, history, favorites):
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_STREAM_HISTORY] = ConfigUtil._sanitize_stream_entries(history)
        self.config[CONFIG_KEY_STREAM_FAVORITES] = list(dict.fromkeys(favorites))[:50]
        ConfigUtil().save(self.config)

    def _record_stream_use(self, url, title="", site="", kind="page"):
        history, favorites = self._load_stream_data()
        history = [e for e in history if e["url"] != url]
        history.insert(
            0,
            {
                "url": url,
                "title": title,
                "site": site or self._stream_site(url),
                "kind": kind,
                "last_used": time.time(),
            },
        )
        self._save_stream_data(history, favorites)

    def _toggle_stream_favorite(self, url):
        history, favorites = self._load_stream_data()
        if url in favorites:
            favorites.remove(url)
        else:
            favorites.insert(0, url)
        self._save_stream_data(history, favorites)
        self._reload_stream_lists()

    def _check_stream_url(self, url):
        """Validate a streaming URL with yt-dlp; None when valid, else error."""
        try:
            with yt_dlp.YoutubeDL({"noplaylist": True, "socket_timeout": 15}) as ydl:
                ydl.extract_info(url, download=False)
        except Exception as e:  # noqa: BLE001 - any backend failure is a bad URL
            return self._classify_stream_error(str(e))
        return None

    @staticmethod
    def _classify_stream_error(message):
        low = (message or "").lower()
        if "sign in to confirm" in low or "bot" in low:
            return "YouTube is asking for bot verification. Try again later."
        if "private" in low or "login" in low or "sign in" in low:
            return "This video needs login or is private."
        if "drm" in low or "widevine" in low:
            return "DRM-protected streams are not supported."
        if "unsupported url" in low:
            return "URL not supported."
        if "404" in low or "not found" in low:
            return "Not found (404)."
        return " ".join(message.split(" ")[1:]) or message

    def _check_direct_url(self, url):
        """Fast-path HEAD check; None when playable, 'fallback' for yt-dlp."""
        try:
            response = requests.head(url, timeout=5, allow_redirects=True)
            content_type = response.headers.get("Content-Type", "")
            if "video/" in content_type or "mpegurl" in content_type or "dash+xml" in content_type:
                return None
            return "fallback"
        except requests.exceptions.RequestException:
            return "fallback"

    def on_stream_apply(self, *_args):
        url = self.stream_entry.get_text().strip()
        if not url:
            return
        kind = self._classify_stream_url(url)
        if kind == "invalid":
            self._show_error("Not a valid http(s) URL.")
            return
        if kind == "collection":
            self._show_collection_warning(url)
            return
        threading.Thread(target=self._validate_and_stream, args=(url, kind), daemon=True).start()

    def _show_collection_warning(self, url):
        dialog = Adw.AlertDialog(
            heading="Playlist links aren't supported",
            body=(
                "That link points to a playlist or channel. "
                "Apricus plays single videos — use the video's own link."
            ),
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("use", "Use video anyway")
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _d, r: (
                threading.Thread(
                    target=self._validate_and_stream, args=(url, "page"), daemon=True
                ).start()
                if r == "use"
                else None
            ),
        )
        dialog.present(self.window)

    def _validate_and_stream(self, url, kind="page"):
        if kind == "direct" and self._check_direct_url(url) is None:
            error = None
        else:
            error = self._check_stream_url(url)
        GLib.idle_add(self._finish_stream_apply, url, error, kind)

    def _finish_stream_apply(self, url, error, kind="page"):
        if error is not None:
            logger.error(f"[GUI4] Failed to stream {url}. Error:\n{error}")
            self._show_error(f"Failed to stream {url}.\nError:\n{error}")
            return False
        logger.info(f"[GUI4] Streaming: {url}")
        self._record_stream_use(url, site=self._stream_site(url), kind=kind)
        self._reload_stream_lists()
        self._show_stream_monitor_chooser(url)
        return False

    def _show_stream_monitor_chooser(self, url):
        if not self._monitor_choices():
            self._apply_stream_to_monitor(url, "")
            return
        popover = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, key in self._monitor_menu_items(url):
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.connect(
                "clicked",
                lambda *_a, k=key: (popover.popdown(), self._apply_stream_to_monitor(url, k)),
            )
            box.append(btn)
        popover.set_child(box)
        popover.set_parent(self.stream_apply_btn)
        popover.popup()

    def _apply_stream_to_monitor(self, url, monitor):
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_MODE] = MODE_STREAM
        data_source = self.config.get(CONFIG_KEY_DATA_SOURCE) or {}
        self.config[CONFIG_KEY_DATA_SOURCE] = data_source
        data_source["Default"] = url
        ConfigUtil().save(self.config)
        server = self._server()
        if server is not None:
            server.stream(url, monitor)
        self._refresh_all()
        target = "all monitors" if not monitor else monitor
        self._toast(f"Streaming on {target}")

    def _webpage_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        title = Gtk.Label(label="Web Page")
        title.set_xalign(0)
        box.append(title)
        self.webpage_entry = Gtk.Entry()
        self.webpage_entry.set_placeholder_text("https://www.google.com/")
        self.webpage_entry.connect("activate", self.on_webpage_apply)
        box.append(self.webpage_entry)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.CENTER)
        apply_btn = Gtk.Button(label="Apply")
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_webpage_apply)
        local_btn = Gtk.Button(label="Local HTML file…")
        local_btn.set_tooltip_text("Choose a HTML file on this PC")
        local_btn.connect("clicked", self.on_webpage_choose_file)
        row.append(apply_btn)
        row.append(local_btn)
        box.append(row)
        return self._clamp_page(box)

    def _check_web_url(self, url):
        """Validate a web page URL; None when valid, else error."""
        try:
            response = requests.get(url, timeout=10, stream=True)
            response.close()
        except requests.exceptions.RequestException as e:
            return str(e)
        if response.status_code >= 400:
            return f"Error code: {response.status_code}"
        return None

    def on_webpage_apply(self, *_args):
        url = self.webpage_entry.get_text().strip()
        if not url:
            return
        threading.Thread(target=self._validate_and_webpage, args=(url,), daemon=True).start()

    def _validate_and_webpage(self, url):
        error = self._check_web_url(url)
        GLib.idle_add(self._finish_webpage_apply, url, error)

    def _finish_webpage_apply(self, url, error):
        if error is not None:
            logger.error(f"[GUI4] Failed to access {url}. Error:\n{error}")
            self._show_error(f"Failed to access {url}.\nError:\n{error}")
            return False
        self._set_webpage_source(url)
        return False

    def on_webpage_choose_file(self, *_args):
        dialog = Gtk.FileDialog()
        dialog.set_title("Choose a HTML file")
        html_filter = Gtk.FileFilter()
        html_filter.set_name("HTML files")
        html_filter.add_pattern("*.html")
        html_filter.add_pattern("*.htm")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(html_filter)
        dialog.set_filters(filters)
        dialog.open(self.window, None, self._on_webpage_file_chosen)

    def _on_webpage_file_chosen(self, dialog, result):
        try:
            chosen = dialog.open_finish(result)
        except GLib.Error as e:
            logger.debug("[GUI4] File chooser dismissed: %s", e.message)
            return
        path = chosen.get_path()
        if not path:
            return
        self._set_webpage_source(path)

    def _set_webpage_source(self, source):
        logger.info(f"[GUI4] Webpage: {source}")
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_MODE] = MODE_WEBPAGE
        data_source = self.config.get(CONFIG_KEY_DATA_SOURCE) or {}
        self.config[CONFIG_KEY_DATA_SOURCE] = data_source
        data_source["Default"] = source
        ConfigUtil().save(self.config)
        server = self._server()
        if server is not None:
            server.webpage(source)
        self._refresh_all()
        self._toast("Web page set as wallpaper")

    def _show_error(self, message):
        dialog = Adw.AlertDialog(heading="Error", body=message)
        dialog.add_response("ok", "OK")
        dialog.set_default_response("ok")
        dialog.connect("response", lambda *_: dialog.close())
        dialog.present(self.window)

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
        return self._clamp_page(box)

    # -- refresh + handlers ---------------------------------------------

    def _refresh_all(self):
        server = self._server()
        mode = server.mode if server is not None else self.config.get(CONFIG_KEY_MODE)
        n_videos = len(get_video_paths(resolve_video_dir(self.config)))
        self._refresh_source_row(n_videos)
        self._reload_library_store()
        self._reload_queue_store()
        self._refresh_playlist_status()
        self._refresh_now_playing()
        is_playlist = mode == MODE_PLAYLIST
        self.mute_switch.set_state(bool(self.config.get(CONFIG_KEY_MUTE)))
        self.volume_scale.set_value(int(self.config.get(CONFIG_KEY_VOLUME, 50)))
        self.volume_scale.set_sensitive(not is_playlist)
        self.mute_switch.set_sensitive(not is_playlist)
        self.shuffle_check.set_active(bool(self.config.get("playlist_shuffle")))
        playlist = (self.config.get("playlist") or {}).get("Default", [])
        self.playlist_status.set_text(
            f"{len(playlist)} videos in queue"
            + (" (muted by design)" if mode == MODE_PLAYLIST else "")
        )
        self.mute_switch.set_state(bool(self.config.get(CONFIG_KEY_MUTE)))
        self.volume_scale.set_value(int(self.config.get(CONFIG_KEY_VOLUME, 50)))
        self._refresh_badges(n_videos, len(self.playlist_paths))
        self._update_queue_actions()
        banner = getattr(self, "server_banner", None)
        if banner is not None:
            banner.set_revealed(server is None)

    def _reload_library_store(self):
        from apricus.gui.gtk4lib import VideoItem

        items = [VideoItem(p) for p in get_video_paths(resolve_video_dir(self.config))]
        self.video_store.splice(0, self.video_store.get_n_items(), items)
        self._on_lib_selection()

    def on_play_pause(self, *_args):
        server = self._server()
        if server is None:
            self._show_server_missing()
            return
        prev = server.is_paused_by_user
        server.is_paused_by_user = not prev
        if not prev:
            server.pause_playback()
        else:
            server.start_playback()
        self._refresh_all()

    def on_playlist_apply(self, *_args):
        self._apply_queue()

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
