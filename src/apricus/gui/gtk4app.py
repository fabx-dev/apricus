# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK4 + Libadwaita control panel.

Talks to ApricusServer over the D-Bus API. Started by the server in a
fresh spawned process.

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
from gettext import gettext as _
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, GObject, Gtk
from pydbus import SessionBus

from apricus.commons import (
    AUTOSTART_DESKTOP_PATH,
    CONFIG_DIR,
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED,
    CONFIG_KEY_PLAYLIST,
    CONFIG_KEY_STATIC_WALLPAPER,
    CONFIG_KEY_STREAM_FAVORITES,
    CONFIG_KEY_STREAM_HISTORY,
    CONFIG_KEY_VIDEO_DIR,
    CONFIG_KEY_VOLUME,
    CONFIG_KEY_WEB_FAVORITES,
    CONFIG_KEY_WEB_HISTORY,
    CONFIG_PATH,
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
from apricus.utils import (
    ConfigUtil,
    get_video_paths,
    init_translations,
    is_flatpak,
    is_gnome,
    is_snap,
    is_wayland,
    setup_autostart,
    setup_logging,
)
from apricus.yt_utils import base_options
from apricus.yt_utils import youtube_dl as yt_dlp


class _LazyRequests:
    """Lazy proxy for requests so GUI cold startup avoids its ~125 ms import."""

    _mod = None

    def __getattr__(self, name):
        if self._mod is None:
            import requests as _req

            type(self)._mod = _req
        return getattr(self._mod, name)


requests = _LazyRequests()

logger = logging.getLogger(LOGGER_NAME)

APP_ID = f"{PROJECT}.gui4"
_UNSET = object()


def _capped_get(url, timeout, max_bytes):
    """GET at most max_bytes of a URL; None on any failure or overflow.

    Thin wrapper over :func:`apricus.utils.fetch_public`: redirects are
    followed hop-by-hop with every hop re-validated, and hostile infinite
    bodies are dropped mid-stream instead of filling RAM.
    """
    from apricus.utils import fetch_public

    data, _status, _content_type, _reason = fetch_public(url, timeout, max_bytes)
    return data


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
        MODE_PLAYLIST: "view-list-symbolic",
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
        GLib.set_prgname(kwargs["application_id"])
        app_flags = (
            Gio.ApplicationFlags.NON_UNIQUE if is_snap() else Gio.ApplicationFlags.FLAGS_NONE
        )
        super().__init__(
            *args,
            flags=app_flags,
            **kwargs,
        )
        self.version = version
        self.window = None
        self.toasts = None
        self.config = ConfigUtil().load()
        self.playlist_paths = list((self.config.get("playlist") or {}).get("Default", []))

    def do_shutdown(self):
        pool = getattr(self, "_stream_thumb_pool", None)
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)
            self._stream_thumb_pool = None
        Gio.Application.do_shutdown(self)

    def do_activate(self):
        if self.window is None:
            Gtk.Window.set_default_icon_name(PROJECT)
            self.window = Adw.ApplicationWindow(application=self)
            self.window.set_icon_name(PROJECT)
            self.window.set_title("Apricus")
            self.window.set_default_size(900, 600)
            self.stack = Adw.ViewStack()
            self.stack.add_titled(self._library_page(), "library", _("Library"))
            self.stack.add_titled(self._playlist_page(), "playlist", _("Playlist"))
            self.stack.add_titled(self._now_playing_page(), "now-playing", _("Now Playing"))
            self.stack.add_titled(self._streaming_page(), "streaming", _("Streaming"))
            self.stack.add_titled(self._webpage_page(), "webpage", _("Web Page"))
            self.stack.add_titled(self._settings_page(), "settings", _("Settings"))
            self.stack.set_hexpand(True)
            self.stack.set_vexpand(True)
            self._build_sidebar()
            content = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
            content.append(self.sidebar_box)
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
            self.server_banner.set_title(_("Background service unreachable"))
            self.server_banner.set_button_label(_("Retry"))
            self.server_banner.connect("button-clicked", lambda *_: self._refresh_all())
            toolbar.add_top_bar(self.server_banner)
            self.playback_banner = Adw.Banner()
            self.playback_banner.set_title(_("Playback failed"))
            self.playback_banner.set_button_label(_("Retry"))
            self.playback_banner.connect("button-clicked", self._on_retry_playback)
            toolbar.add_top_bar(self.playback_banner)
            toolbar.set_content(self.toasts)
            self.window.set_content(toolbar)
            self._load_css()
        self._refresh_all()
        # Dev/test aid: preselect a section (e.g. screenshots).
        initial_tab = os.environ.get("APRICUS_GTK4_TAB")
        self._select_section(initial_tab or "library")
        self.window.present()

    def _build_sidebar(self):
        from gi.repository import Pango

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.sidebar_box = box
        # Fixed slot: never claim extra horizontal space even though nested
        # rows contain expanding children (Adw internals). Without pinning,
        # compute_expand() propagates up and the sidebar swallows half the
        # free window width.
        box.set_hexpand(False)
        box.set_hexpand_set(True)
        sidebar = Gtk.ListBox()
        sidebar.add_css_class("navigation-sidebar")
        sidebar.set_vexpand(True)
        self.sidebar_rows = {}
        for name, title, icon_name in self.SECTIONS:
            row = Adw.ActionRow()
            row.set_name(name)
            row.set_title(_(title))
            icon = Gtk.Image.new_from_icon_name(icon_name)
            row.add_prefix(icon)
            self.sidebar_rows[name] = row
            sidebar.append(row)
        sidebar.connect("row-selected", self._on_sidebar_selected)
        self.sidebar = sidebar
        box.append(sidebar)
        server_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        server_row.set_margin_top(8)
        server_row.set_margin_bottom(2)
        server_row.set_margin_start(12)
        server_row.set_margin_end(12)
        self.sidebar_server_dot = Gtk.Label(label="●")
        server_row.append(self.sidebar_server_dot)
        self.sidebar_server_label = Gtk.Label(label="")
        self.sidebar_server_label.set_xalign(0)
        self.sidebar_server_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.sidebar_server_label.set_max_width_chars(14)
        server_row.append(self.sidebar_server_label)
        box.append(server_row)
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        status_row.set_margin_top(2)
        status_row.set_margin_bottom(2)
        status_row.set_margin_start(12)
        status_row.set_margin_end(12)
        self.sidebar_dot = Gtk.Image.new_from_icon_name("video-display-symbolic")
        self.sidebar_dot.set_pixel_size(14)
        status_row.append(self.sidebar_dot)
        self.sidebar_status = Gtk.Label(label="")
        self.sidebar_status.set_xalign(0)
        self.sidebar_status.set_ellipsize(Pango.EllipsizeMode.END)
        self.sidebar_status.set_max_width_chars(12)
        status_row.append(self.sidebar_status)
        self.sidebar_eq = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.sidebar_eq.set_valign(Gtk.Align.CENTER)
        for _i in range(3):
            bar = Gtk.Box()
            bar.set_size_request(3, 10)
            bar.add_css_class("sb-eq-bar")
            self.sidebar_eq.append(bar)
        status_row.append(self.sidebar_eq)
        box.append(status_row)
        self.sidebar_version = Gtk.Label(label=f"Apricus {self.version}")
        self.sidebar_version.add_css_class("dim-label")
        self.sidebar_version.add_css_class("caption")
        self.sidebar_version.set_xalign(0)
        self.sidebar_version.set_ellipsize(Pango.EllipsizeMode.END)
        self.sidebar_version.set_max_width_chars(22)
        self.sidebar_version.set_margin_start(12)
        self.sidebar_version.set_margin_end(12)
        self.sidebar_version.set_margin_bottom(8)
        box.append(self.sidebar_version)
        self._refresh_sidebar_status(None, None)
        return box

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
            b".sb-eq-bar { background: @success_color; border-radius: 2px; "
            b"animation: np-eq 0.9s ease-in-out infinite alternate; }"
            b".sb-eq-bar:nth-child(2) { animation-delay: 0.3s; }"
            b".sb-eq-bar:nth-child(3) { animation-delay: 0.6s; }"
            b".web-hero { background-image: linear-gradient(to bottom, "
            b"alpha(@accent_bg_color, 0.55), alpha(black, 0.75)); "
            b"border-radius: 12px; padding: 32px; }"
            b".web-hero .title-4 { color: white; }"
            b".web-hero .dim-label { color: alpha(white, 0.85); }"
        )
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

    def _toast(self, message):
        overlay = getattr(self, "toasts", None)
        if overlay is None:
            return
        overlay.add_toast(Adw.Toast.new(message))

    @staticmethod
    def _sidebar_status_text(server_present, mode):
        """(dot_css_class, status_text) for the sidebar footer."""
        if not server_present:
            return "error", _("Server down")
        return "success", _("Ready — {mode}").format(mode=ControlPanel._mode_text(mode))

    @staticmethod
    def _animations_enabled():
        settings = Gtk.Settings.get_default()
        return bool(settings and settings.props.gtk_enable_animations)

    @staticmethod
    def _sidebar_server_text(server_present):
        """(dot_css_class, server_text) for Row 1 of the sidebar footer."""
        if not server_present:
            return "error", _("Server down")
        ready = _("Ready — {mode}").format(mode="").split("—")[0].strip() or "Ready"
        return "success", ready

    @staticmethod
    def _sidebar_activity_state(server_present, mode, paused):
        """(icon_name, activity_text) for Row 2 of the sidebar footer."""
        if not server_present:
            return "media-playback-stop-symbolic", _("Nothing playing")
        if mode in (MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST) and paused:
            return "media-playback-pause-symbolic", _("Paused")
        if mode == MODE_PLAYLIST:
            return ControlPanel._mode_icon(mode), _("Playlist")
        if mode == MODE_NULL or mode not in ControlPanel.MODE_LABELS:
            return ControlPanel._mode_icon(MODE_NULL), _("Static")
        return ControlPanel._mode_icon(mode), ControlPanel._mode_text(mode)

    def _refresh_sidebar_status(self, server, mode, paused=None):
        dot = getattr(self, "sidebar_dot", None)
        label = getattr(self, "sidebar_status", None)
        eq = getattr(self, "sidebar_eq", None)
        if dot is None or label is None:
            return
        server_ok = server is not None
        css, srv_text = self._sidebar_server_text(server_ok)
        srv_dot = getattr(self, "sidebar_server_dot", None)
        srv_label = getattr(self, "sidebar_server_label", None)
        if srv_dot is not None:
            for cls in ("success", "error"):
                srv_dot.remove_css_class(cls)
            srv_dot.add_css_class(css)
        if srv_label is not None:
            srv_label.set_text(srv_text)
            srv_label.set_tooltip_text(
                "Background service unreachable. Start Apricus." if not server_ok else srv_text
            )
        playing = False
        if server_ok and mode in (MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST):
            if paused is None:
                try:
                    paused = bool(server.is_paused_by_user)
                except Exception:  # noqa: BLE001 - unknown state means not playing
                    paused = True
            playing = not paused
        else:
            paused = True
        icon_name, act_text = self._sidebar_activity_state(server_ok, mode, paused)
        show_eq = bool(playing and self._animations_enabled() and eq is not None)
        if eq is not None:
            eq.set_visible(show_eq)
        if hasattr(dot, "set_from_icon_name"):
            dot.set_from_icon_name(icon_name)
            dot.set_visible(True)
        else:
            dot.set_visible(not show_eq)
        for cls in ("success", "error"):
            dot.remove_css_class(cls)
        dot.add_css_class(css)
        label.set_text(act_text)
        label.set_tooltip_text(
            "Background service unreachable. Start Apricus." if not server_ok else act_text
        )

    def _show_about(self, *_args):
        self._make_about_dialog().present(self.window)

    def _make_about_dialog(self):
        dialog = Adw.AboutDialog()
        dialog.set_application_name("Apricus")
        dialog.set_application_icon("io.github.fabx_dev.Apricus")
        dialog.set_developer_name("Fabrizio (fabx-dev)")
        dialog.set_version(self.version)
        dialog.set_comments(
            _(
                "Modern live wallpaper for Linux — local videos, looping playlists, "
                "live streams and interactive web pages.\n"
                "Independent evolution of Hidamari with a native GTK4 + Libadwaita "
                "control center and a gapless playback engine."
            )
        )
        dialog.set_copyright("© 2026 Fabrizio (fabx-dev) · Based on Hidamari © 2022–2026 Jeff Shee")
        dialog.set_license_type(Gtk.License.GPL_3_0)
        dialog.set_website("https://github.com/fabx-dev/apricus")
        dialog.set_issue_url("https://github.com/fabx-dev/apricus/issues")
        dialog.add_link(
            _("Releases and Flatpak Bundles"),
            "https://github.com/fabx-dev/apricus/releases",
        )
        dialog.add_link(_("Contact via Email"), "mailto:fabxdev@proton.me")
        dialog.add_link(_("Original Hidamari Project"), "https://github.com/jeffshee/hidamari")
        dialog.set_developers(
            [
                "Fabrizio (fabx-dev) <fabxdev@proton.me> https://github.com/fabx-dev",
            ]
        )
        dialog.set_designers(
            [
                "Fabrizio (fabx-dev) https://github.com/fabx-dev",
            ]
        )
        dialog.set_artists(
            [
                _("Apricus 2.0 application icon — Fabrizio (fabx-dev)"),
            ]
        )
        dialog.set_translator_credits(
            "Fabrizio (fabx-dev) — English, Italiano, Deutsch, Français, Español, 日本語"
        )
        dialog.add_acknowledgement_section(
            _("Original Upstream Project (Hidamari)"),
            [
                "Jeff Shee https://github.com/jeffshee/hidamari",
            ],
        )
        dialog.add_acknowledgement_section(
            _("Powered by Open Source"),
            [
                "GTK4 and Libadwaita https://www.gtk.org",
                "VLC and libVLC https://www.videolan.org",
                "yt-dlp https://github.com/yt-dlp/yt-dlp",
                "Deno Runtime https://deno.com",
                "WebKitGTK https://webkitgtk.org",
            ],
        )
        dialog.set_release_notes_version(self.version)
        dialog.set_release_notes(
            "<p>Apricus 2.0 brings a complete GTK4 + Libadwaita control center and playback engine:</p>"
            "<ul>"
            "<li>Six dedicated workspaces: Library, Playlist, Now Playing, Streaming, Web Page and Settings</li>"
            "<li>Visual looping playlists with thumbnails, drag-and-drop reorder, shuffle and undo</li>"
            "<li>Now Playing dashboard with 16:9 hero preview, transport bar, metadata and Up next queue</li>"
            "<li>Streaming &amp; Web hubs with saved favorites, recent history, LIVE badge and 10-second live preview</li>"
            "<li>Instant left-aligned monitor chooser across all Apply actions</li>"
            "<li>Gapless in-place video switching, ~1.4s login startup and hop-by-hop SSRF network hardening</li>"
            "<li>Complete translations in English, Italian, German, French, Spanish and Japanese</li>"
            "</ul>"
        )
        return dialog

    def _on_sidebar_selected(self, _sidebar, row):
        if row is not None:
            self.stack.set_visible_child_name(row.get_name())
            if row.get_name() == "streaming":
                self._reload_stream_lists()
            elif row.get_name() == "webpage":
                self._reload_web_lists()

    def _select_section(self, name):
        names = [section for section, _, _ in self.SECTIONS]
        target = name if name in names else "library"
        row = self.sidebar.get_row_at_index(names.index(target))
        self.sidebar.select_row(row)

    def _server(self):
        if is_snap():
            from apricus.snap_ipc import get_service

            return get_service(DBUS_NAME_SERVER)
        try:
            bus = getattr(self, "_session_bus", None)
            if bus is None:
                bus = SessionBus()
                self._session_bus = bus
            return bus.get(DBUS_NAME_SERVER)
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
        hint = Gtk.Label(
            label=_("Click to select. Ctrl+click or Shift+click selects more than one.")
        )
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
        source_row.set_title(_("No folder chosen"))
        source_row.set_subtitle(_("Choose the folder scanned for local videos"))
        choose_btn = Gtk.Button(label=_("Choose…"))
        choose_btn.set_tooltip_text(_("Choose the folder scanned for local videos"))
        choose_btn.set_valign(Gtk.Align.CENTER)
        choose_btn.connect("clicked", self.on_choose_video_dir)
        source_row.add_suffix(choose_btn)
        open_btn = Gtk.Button()
        open_btn.set_icon_name("folder-open-symbolic")
        open_btn.set_tooltip_text(_("Open the video folder in the file manager"))
        open_btn.set_valign(Gtk.Align.CENTER)
        open_btn.connect("clicked", self.on_open_video_dir)
        source_row.add_suffix(open_btn)
        self.source_row = source_row
        box.append(source_row)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.FILL)
        apply_btn = Gtk.Button(label=_("Apply selected"))
        apply_btn.add_css_class("suggested-action")
        apply_btn.set_tooltip_text(_("Use the selected video as live wallpaper"))
        apply_btn.connect("clicked", self.on_library_apply)
        self.apply_btn = apply_btn
        add_split = Adw.SplitButton()
        add_split.set_child(Gtk.Label(label=_("Add to playlist")))
        add_split.set_tooltip_text(_("Add the selected videos to the playlist queue"))
        add_split.connect("clicked", self.on_library_add)
        add_menu = Gtk.Popover()
        add_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        add_selected_btn = Gtk.Button(label=_("Add selected"))
        add_selected_btn.add_css_class("flat")
        add_selected_btn.connect("clicked", lambda *_a: (add_menu.popdown(), self.on_library_add()))
        add_all_btn = Gtk.Button(label=_("Add all"))
        add_all_btn.add_css_class("flat")
        add_all_btn.set_tooltip_text(_("Add every video in the library to the playlist queue"))
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
        play = Gtk.Button(label=_("Play/Pause"))
        play.set_tooltip_text(_("Toggle playback"))
        play.connect("clicked", self.on_play_pause)
        row.append(play)
        quit_btn = Gtk.Button(label=_("Quit"))
        quit_btn.set_tooltip_text(_("Close the panel. Wallpaper keeps running"))
        quit_btn.connect("clicked", lambda *_: self.quit())
        row.append(quit_btn)
        more_btn = Gtk.MenuButton()
        more_btn.set_icon_name("view-more-symbolic")
        more_btn.set_tooltip_text(_("More library actions"))
        more_menu = Gtk.Popover()
        more_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        refresh_btn = Gtk.Button(label=_("Refresh"))
        refresh_btn.add_css_class("flat")
        refresh_btn.set_tooltip_text(_("Rescan the source folder"))
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
            ""
            if n == 0
            else (_("1 video selected") if n == 1 else _("{} videos selected").format(n))
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
            row.set_title(_("No folder chosen"))
            row.set_subtitle(_("Choose the folder scanned for local videos"))
        else:
            row.set_title(self._plain(os.path.basename(video_dir) or video_dir))
            subtitle = video_dir
            if n_videos is not None:
                subtitle += f" — {n_videos} videos"
            row.set_subtitle(self._plain(subtitle))

    def _show_server_missing(self):
        self._show_error(_("Background service unreachable.\nStart Apricus to apply wallpapers."))

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
        """Pure label list for the chooser: [("Set for all monitors", "")] + monitors."""
        return [(_("Set for all monitors"), "")] + [
            (_("Set for {monitor}").format(monitor=label), key)
            for key, label in self._monitor_choices()
        ]

    def _popup_monitor_chooser(self, anchor, path, on_pick):
        prev = getattr(anchor, "_active_popover", None)
        if prev is not None:
            try:
                prev.unparent()
            except Exception:  # noqa: BLE001
                pass
            anchor._active_popover = None
        popover = Gtk.Popover()
        anchor._active_popover = popover
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        for label, key in self._monitor_menu_items(path):
            btn = Gtk.Button(label=label)
            btn.add_css_class("flat")
            btn.set_halign(Gtk.Align.FILL)
            child = btn.get_child()
            if hasattr(child, "set_xalign"):
                child.set_xalign(0.0)
            btn.connect(
                "clicked",
                lambda *_a, k=key: (popover.popdown(), on_pick(k)),
            )
            box.append(btn)
        popover.set_child(box)
        popover.set_parent(anchor)
        popover.popup()

    def _show_monitor_chooser(self, path):
        if not self._monitor_choices():
            self._apply_video_to_monitor(path, "")
            return
        self._popup_monitor_chooser(
            self.apply_btn,
            path,
            lambda k: self._apply_video_to_monitor(path, k),
        )

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
            heading=_("Apply needs a single video"),
            body=(
                _(
                    "You selected {count} videos, but Apply sets one wallpaper.\n"
                    "Select just one video to apply it, or add them to the "
                    "playlist to play them all in a loop."
                ).format(count=count)
            ),
        )
        dialog.add_response("ok", _("OK"))
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
        dialog.set_title(_("Choose video folder"))
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
        self._force_lib_reload = True
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
        apply_btn = Gtk.Button(label=_("Apply playlist"))
        apply_btn.add_css_class("suggested-action")
        apply_btn.set_tooltip_text(_("Apply current order to playback"))
        apply_btn.connect("clicked", self.on_playlist_apply)
        row.append(apply_btn)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        row.append(spacer)
        self.shuffle_check = Gtk.ToggleButton(label=_("Shuffle"))
        self.shuffle_check.set_tooltip_text(_("Shuffle playlist order randomly"))
        self.shuffle_check.connect("toggled", self.on_shuffle_toggled)
        rm_btn = Gtk.Button(label=_("Remove selected"))
        rm_btn.set_tooltip_text(_("Remove the selected video from the playlist"))
        rm_btn.connect("clicked", self.on_queue_remove)
        self.queue_remove_btn = rm_btn
        clear_btn = Gtk.Button(label=_("Clear"))
        clear_btn.add_css_class("destructive-action")
        clear_btn.set_tooltip_text(_("Remove all videos from the playlist"))
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
            more_btn.set_tooltip_text(_("More actions"))
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

        paths = list(self.playlist_paths)
        if self.queue_store is not None:
            n = self.queue_store.get_n_items()
            if n == len(paths) and all(
                self.queue_store.get_item(i) is not None
                and self.queue_store.get_item(i).path == paths[i]
                for i in range(n)
            ):
                return
        items = [VideoItem(p) for p in paths]
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
        toast.set_button_label(_("Undo"))
        toast.connect("button-clicked", lambda *_: on_undo())
        overlay.add_toast(toast)
        return toast

    def on_queue_clear(self, *_args):
        if not self.playlist_paths:
            return
        dialog = Adw.AlertDialog(
            heading=_("Clear playlist?"),
            body=(
                _(
                    "This will remove all {n} videos "
                    "from the playlist. This action cannot be undone."
                ).format(n=len(self.playlist_paths))
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("clear", _("Clear"))
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
        self._toast(_("Playlist cleared"))

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

    def _refresh_playlist_status(self, server=_UNSET, mode=None):
        n = len(self.playlist_paths)
        if mode is None:
            if server is _UNSET:
                server = self._server()
            mode = server.mode if server is not None else self.config.get(CONFIG_KEY_MODE)
        if n == 0:
            text = _("Queue empty — add videos from Library")
        elif mode == MODE_PLAYLIST:
            text = _("{n} videos in queue (muted by design)").format(n=n)
        else:
            text = _("{n} videos in queue (paused)").format(n=n)
        self.playlist_status.set_text(text)

    @staticmethod
    def _clamp_page(box, maximum_size=700):
        clamp = Adw.Clamp()
        clamp.set_maximum_size(maximum_size)
        clamp.set_child(box)
        return clamp

    @staticmethod
    def _scroll_page(widget):
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_child(widget)
        return scrolled

    def _now_playing_page(self):
        from gi.repository import Pango

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        self.np_generation = 0
        self._np_current_path = None
        self._np_queue_shown = None
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
        self.np_play_btn.set_tooltip_text(_("Play / Pause"))
        self.np_play_btn.connect("clicked", self.on_play_pause)
        transport.append(self.np_play_btn)
        self.np_mute_btn = Gtk.ToggleButton()
        self.np_mute_btn.add_css_class("flat")
        self.np_mute_btn.set_tooltip_text(_("Mute / Unmute"))
        self.np_mute_btn.connect("toggled", self.on_np_mute_toggled)
        mute_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.np_mute_icon = Gtk.Image()
        self.np_mute_label = Gtk.Label(label=_("Mute"))
        mute_box.append(self.np_mute_icon)
        mute_box.append(self.np_mute_label)
        self.np_mute_btn.set_child(mute_box)
        transport.append(self.np_mute_btn)
        self.np_volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.np_volume.set_size_request(220, -1)
        self.np_volume.set_valign(Gtk.Align.CENTER)
        self.np_volume.set_tooltip_text(_("Volume"))
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
        self.np_details.set_title(_("Details"))
        self.np_detail_rows = {}
        self.np_detail_icons = {}
        for key, title, icon_name in (
            ("line1", _("Resolution"), "video-display-symbolic"),
            ("line2", _("Duration"), "alarm-symbolic"),
            ("line3", _("Format"), "text-x-generic-symbolic"),
            ("line4", _("Folder"), "folder-symbolic"),
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
        self.np_folder_btn.set_tooltip_text(_("Open the containing folder"))
        self.np_folder_btn.set_valign(Gtk.Align.CENTER)
        self.np_folder_btn.connect("clicked", self.on_np_open_folder)
        self.np_detail_rows["line4"].add_suffix(self.np_folder_btn)
        content.append(self.np_details)
        self.np_queue_group = Adw.PreferencesGroup()
        self.np_queue_group.set_title(_("Up next"))
        self.np_queue_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.np_queue_group.add(self.np_queue_box)
        content.append(self.np_queue_group)
        self.np_content = content
        box.append(content)
        self.np_empty = Adw.StatusPage()
        self.np_empty.set_icon_name("video-display-symbolic")
        self.np_empty.set_title(_("Nothing playing"))
        self.np_empty.set_description(_("Choose a video from the library to start watching."))
        open_lib_btn = Gtk.Button(label=_("Open library"))
        open_lib_btn.add_css_class("suggested-action")
        open_lib_btn.connect("clicked", lambda *_: self._select_section("library"))
        self.np_empty.add_css_class("compact")
        box.append(self.np_empty)
        self.np_empty.set_child(open_lib_btn)
        return self._scroll_page(self._clamp_page(box, 800))

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
        for _i in range(7):
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
            return _("Playlist in loop")
        if mode == MODE_WEBPAGE:
            return _("Web page")
        if mode in (MODE_VIDEO, MODE_STREAM):
            return _("Paused") if paused else _("Playing")
        return _("Static")

    def _refresh_now_playing(self, server=_UNSET, mode=None, paused=None):
        if server is _UNSET:
            server = self._server()
        if mode is None:
            mode = server.mode if server is not None else self.config.get(CONFIG_KEY_MODE)
        current = (self.config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "")
        if mode == MODE_PLAYLIST:
            # A playlist never rewrites data_source.Default, so the hero
            # would keep showing the previous single video (or nothing).
            # Follow the queue head instead (loop restarts there).
            queue = list(getattr(self, "playlist_paths", None) or [])
            if queue:
                current = queue[0]
        if paused is None:
            paused = True
            if server is not None and mode in (MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST):
                try:
                    paused = bool(server.is_paused_by_user)
                except Exception:  # noqa: BLE001
                    paused = True
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
        mute_btn.set_tooltip_text(_("Unmute") if muted else _("Mute"))
        self.np_mute_icon.set_from_icon_name(self._mute_icon(muted, volume))
        self.np_mute_label.set_text(_("Muted") if muted else _("Mute"))
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
        self.np_mute_label.set_text(_("Muted") if state else _("Mute"))
        button.set_tooltip_text(_("Unmute") if state else _("Mute"))

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

    @staticmethod
    def _static_cache_path(source):
        import hashlib

        digest = hashlib.sha256((source or "").encode("utf-8")).hexdigest()[:16]
        return os.path.join(CONFIG_DIR, f"static-{digest}.png")

    def _try_load_static_hero(self, path, generation):
        static_png = self._static_cache_path(path)
        if not os.path.isfile(static_png):
            return False
        try:
            from gi.repository import GdkPixbuf

            st = os.stat(static_png)
            if (
                getattr(self, "_np_static_mtime", None) == (static_png, st.st_mtime_ns)
                and getattr(self, "_np_current_path", None) == path
            ):
                return True
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_size(static_png, 640, -1)
            if pixbuf is not None:
                self._np_static_mtime = (static_png, st.st_mtime_ns)
                self._show_hero_texture(Gdk.Texture.new_for_pixbuf(pixbuf), generation)
                return True
        except Exception:  # noqa: BLE001
            pass
        return False

    def _poll_web_np_hero(self, path, generation):
        if generation != getattr(self, "np_generation", generation):
            return False
        if path != getattr(self, "_np_current_path", None):
            return False
        if getattr(self, "_np_stream_thumb_loaded", None) == (path, generation):
            return False
        self._try_load_static_hero(path, generation)
        return False

    def _load_np_hero(self, path, generation):
        from apricus.gui.gtk4lib import VideoItem, load_texture_async

        self._np_static_mtime = None
        self._np_stream_thumb_loaded = None
        is_web_or_remote = "://" in (path or "") or (path or "").lower().endswith((".html", ".htm"))
        if is_web_or_remote:
            loaded = self._try_load_static_hero(path, generation)
            if not loaded and "://" in (path or ""):
                try:
                    cached = self._thumb_cache().lookup(path)
                    if cached and os.path.isfile(cached):
                        from gi.repository import GdkPixbuf

                        pb = GdkPixbuf.Pixbuf.new_from_file_at_size(cached, 640, -1)
                        if pb is not None:
                            self._show_hero_texture(Gdk.Texture.new_for_pixbuf(pb), generation)
                            loaded = True
                except Exception:  # noqa: BLE001
                    pass
            if not loaded:
                (self.np_pic_b if self._np_visible_pic == "a" else self.np_pic_a).set_paintable(
                    None
                )
            for delay_ms in (1800, 3500, 5500):
                GLib.timeout_add(delay_ms, self._poll_web_np_hero, path, generation)
            return

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
        if texture is not None:
            self._np_stream_thumb_loaded = (url, generation)
        self._show_hero_texture(texture, generation)
        return False

    def _refresh_np_details(self, mode, current):
        group = getattr(self, "np_details", None)
        if group is None:
            return
        signature = (mode, current)
        if signature == getattr(self, "_np_details_shown", None):
            return
        self._np_details_shown = signature
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
        elif mode == MODE_WEBPAGE and current:
            group.set_visible(True)
            rows = self.np_detail_rows
            is_file = "://" not in current
            rows["line1"].set_title(_("Title"))
            rows["line2"].set_title(_("Site"))
            rows["line3"].set_title(_("Link"))
            rows["line4"].set_title(_("Type"))
            if is_file:
                rows["line1"].set_subtitle(os.path.basename(current))
                try:
                    size = os.path.getsize(current)
                except OSError:
                    size = None
                rows["line2"].set_subtitle(_("Local web page"))
                rows["line3"].set_subtitle(current)
                rows["line4"].set_subtitle(
                    f"Web page — {self._format_file_size(size)}" if size is not None else "Web page"
                )
            else:
                rows["line1"].set_subtitle("…")
                rows["line2"].set_subtitle(self._display_source(current))
                rows["line3"].set_subtitle(current)
                rows["line4"].set_subtitle(_("Web page"))
                generation = self.np_generation
                threading.Thread(
                    target=self._fetch_np_web_title, args=(current, generation), daemon=True
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
                _("Resolution"),
                f"{width}×{height}" if width and height else "—",
                "video-display-symbolic",
            ),
            "line2": (_("Duration"), self._fmt_duration(duration), "alarm-symbolic"),
            "line3": (
                _("Format"),
                os.path.splitext(path)[1].lstrip(".").upper() or "—",
                "text-x-generic-symbolic",
            ),
            "line4": (
                os.path.basename(os.path.dirname(path)) or _("Folder"),
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
            cached = getattr(self, "_stream_meta_cache", None)
            if cached and cached[0] == url and (time.time() - cached[1]) < 120:
                meta = cached[2]
            else:
                with yt_dlp.YoutubeDL(base_options(quiet=True)) as ydl:
                    meta = ydl.extract_info(url, download=False) or {}
                self._stream_meta_cache = (url, time.time(), meta)
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
            from apricus.stream_thumbs import is_live_meta

            GLib.idle_add(
                self._update_stream_live_flag_idle,
                url,
                is_live_meta(meta),
                meta.get("title") or "",
            )
        except Exception:  # noqa: BLE001 - rows keep safe defaults, no URL in logs
            logger.debug("[GUI4] Stream info failed")
        GLib.idle_add(self._apply_np_details, generation, info, None)
        if thumb_url:
            try:
                data = _capped_get(thumb_url, timeout=15, max_bytes=512 * 1024)
            except Exception:  # noqa: BLE001 - hero simply stays empty
                logger.debug("[GUI4] Stream thumbnail download failed")
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
            row.set_title(self._plain(title))
            row.set_subtitle(self._plain(subtitle))
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

    def _fetch_np_web_title(self, url, generation):
        title = ""
        try:
            body = _capped_get(url, timeout=10, max_bytes=64 * 1024) or b""
            title = self._extract_html_title(body)
        except requests.exceptions.RequestException as e:
            logger.debug(f"[GUI4] Web title fetch failed for {url}: {e}")
        GLib.idle_add(self._apply_np_web_title, generation, url, title)

    def _apply_np_web_title(self, generation, url, title):
        if generation != getattr(self, "np_generation", generation):
            return False
        current = (self.config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "")
        if current != url or not title:
            return False
        row = getattr(self, "np_detail_rows", {}).get("line1")
        if row is not None:
            row.set_subtitle(self._plain(title))
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
            self._np_queue_shown = None
            return
        # Rebuilding the rows destroys/recreates thumbnails (visible flash
        # on every refresh, e.g. play/pause): skip when nothing changed.
        signature = (mode, tuple(queue[:3]))
        if signature == getattr(self, "_np_queue_shown", None):
            return
        self._np_queue_shown = signature
        while True:
            child = box.get_first_child()
            if child is None:
                break
            box.remove(child)
        from apricus.gui.gtk4lib import VideoItem, load_texture_async

        for pos, path in enumerate(queue[:3]):
            row = Adw.ActionRow()
            row.set_title(self._plain(os.path.basename(path)))
            row.set_subtitle(_("n. {pos} of {total}").format(pos=pos + 1, total=len(queue)))
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
    def _register_i18n_constants():
        return (
            _("Single video"),
            _("Streaming"),
            _("Web page"),
            _("Playlist (muted)"),
            _("Static wallpaper"),
            _("Library"),
            _("Playlist"),
            _("Now Playing"),
            _("Web Page"),
            _("Settings"),
            _("Playback failed before the first frame was shown"),
        )

    @staticmethod
    def _mode_text(mode):
        return _(ControlPanel.MODE_LABELS.get(mode, ControlPanel.MODE_LABELS[MODE_NULL]))

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
    def _plain(text):
        """Escape for Adw row titles/subtitles (they parse Pango markup)."""
        return GLib.markup_escape_text(str(text or ""))

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

    # Both content pages must measure identically: their children differ
    # (web has file row + chips), so without an explicit floor the narrower
    # page would collapse and the heroes would render at different widths.
    PAGE_MIN_WIDTH = 600

    def _build_hero(self, icon_name, title, subtitle):
        """Shared Streaming/Web Page hero: identical structure and geometry.

        Frame.card > Box.web-hero (VERTICAL, spacing 8, FILL) >
        Image 32px + Label.title-4 (xalign 0, ellipsize END) +
        Label.dim (xalign 0, ellipsize END). Only the contents differ
        per page; never the structure, so the two heroes cannot drift.
        """
        from gi.repository import Pango

        hero = Gtk.Frame()
        hero.add_css_class("card")
        hero_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        hero_box.add_css_class("web-hero")
        hero_box.set_halign(Gtk.Align.FILL)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(45)
        hero_box.append(icon)
        title_label = Gtk.Label(label=title)
        title_label.add_css_class("title-4")
        title_label.set_xalign(0)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        hero_box.append(title_label)
        sub_label = Gtk.Label(label=subtitle)
        sub_label.add_css_class("dim-label")
        sub_label.set_xalign(0)
        sub_label.set_ellipsize(Pango.EllipsizeMode.END)
        hero_box.append(sub_label)
        hero.set_child(hero_box)
        return hero, {"icon": icon, "title": title_label, "sub": sub_label}

    def _streaming_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_size_request(self.PAGE_MIN_WIDTH, -1)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        hero, refs = self._build_hero(
            "network-wireless-symbolic",
            _("Streaming"),
            _("Paste a link to stream it as wallpaper"),
        )
        self.stream_hero_icon = refs["icon"]
        self.stream_hero_title = refs["title"]
        self.stream_hero_sub = refs["sub"]
        box.append(hero)
        self.stream_entry = Gtk.Entry()
        self.stream_entry.set_placeholder_text(_("https://… — YouTube, Twitch, live, direct video"))
        self.stream_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.PRIMARY, "web-browser-symbolic"
        )
        self.stream_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY, "edit-paste-symbolic"
        )
        self.stream_entry.set_icon_tooltip_text(
            Gtk.EntryIconPosition.SECONDARY, _("Paste from clipboard")
        )
        self.stream_entry.connect("activate", self.on_stream_apply)
        self.stream_entry.connect("icon-press", self._on_stream_icon_press)
        self.stream_entry.connect("changed", self._on_stream_entry_changed)
        box.append(self.stream_entry)
        self.stream_type_row = Adw.ActionRow()
        self.stream_type_row.set_title(_("Detected"))
        self.stream_type_row.set_subtitle(_("Paste a link to detect its type"))
        self.stream_type_icon = Gtk.Image.new_from_icon_name("web-browser-symbolic")
        self.stream_type_row.add_prefix(self.stream_type_icon)
        self.stream_type_spinner = Gtk.Spinner()
        self.stream_type_spinner.set_valign(Gtk.Align.CENTER)
        self.stream_type_row.add_suffix(self.stream_type_spinner)
        box.append(self.stream_type_row)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.FILL)
        apply_btn = Gtk.Button(label=_("Apply"))
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_stream_apply)
        row.append(apply_btn)
        self.stream_apply_btn = apply_btn
        try_btn = Gtk.Button(label=_("Try 10s"))
        try_btn.set_tooltip_text(_("Preview for 10 seconds, then pause"))
        try_btn.connect("clicked", self.on_stream_try)
        row.append(try_btn)
        box.append(row)
        self.saved_group = Adw.PreferencesGroup()
        self.saved_group.set_title(_("Saved sources"))
        self.saved_list = Gtk.ListBox()
        self.saved_list.add_css_class("boxed-list")
        self.saved_list.connect("row-activated", self._on_saved_row_activated)
        self.saved_group.add(self.saved_list)
        box.append(self.saved_group)
        recent_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        recent_label = Gtk.Label(label=_("Recent"))
        recent_label.set_xalign(0)
        recent_label.set_hexpand(True)
        recent_box.append(recent_label)
        clear_recent_btn = Gtk.Button(label=_("Clear"))
        clear_recent_btn.connect("clicked", self.on_clear_stream_history)
        recent_box.append(clear_recent_btn)
        box.append(recent_box)
        self.recent_list = Gtk.ListBox()
        self.recent_list.add_css_class("boxed-list")
        self.recent_list.connect("row-activated", self._on_saved_row_activated)
        box.append(self.recent_list)
        self.stream_empty = Adw.StatusPage()
        self.stream_empty.set_icon_name("network-wireless-symbolic")
        self.stream_empty.set_title(_("No saved sources yet"))
        self.stream_empty.set_description(
            _("Paste a YouTube, Twitch or direct video link above. You will find it here.")
        )
        paste_btn = Gtk.Button(label=_("Paste from clipboard"))
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
        page = self._scroll_page(self._clamp_page(box))
        vadj = page.get_vadjustment()
        vadj.connect("value-changed", self._on_stream_scroll)
        vadj.connect("changed", self._on_stream_scroll)
        return page

    def _on_stream_scroll(self, *_args):
        self._schedule_visible_thumbs()

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
        url = self.stream_entry.get_text().strip()
        host = self._stream_site(url) if url else ""
        if host == getattr(self, "_favicon_host", None):
            return  # same host: keep the current icon, never clobber it
        self._favicon_host = host
        self._favicon_generation = getattr(self, "_favicon_generation", 0) + 1
        if not host:
            self._set_favicon_state("idle")
            return
        cached = self._cached_favicon(host)
        if cached is not None:
            self._set_favicon_state("ok", cached)
            return
        self._set_favicon_state("verifying")
        threading.Thread(
            target=self._fetch_favicon,
            args=(host, url, self._favicon_generation),
            daemon=True,
        ).start()

    def _set_favicon_state(self, state, path=None, reason=""):
        """Favicon state machine: idle / verifying / ok / fallback.

        Transitions are INFO-logged so a missing icon is always diagnosable;
        a valid favicon is never replaced except by a host change (above).
        """
        self._favicon_state = state
        icon = getattr(self, "stream_type_icon", None)
        spinner = getattr(self, "stream_type_spinner", None)
        if icon is None:
            return
        if state == "ok" and path:
            icon.set_from_file(path)
            logger.info(f"[GUI4] Favicon ok for {getattr(self, '_favicon_host', '')}")
        else:
            icon.set_from_icon_name(self._stream_kind_icon())
            if spinner is not None:
                spinner.set_spinning(state == "verifying")
            if state == "fallback":
                logger.info(f"[GUI4] Favicon fallback: {reason or 'unavailable'}")

    def _stream_kind_icon(self):
        entry = getattr(self, "stream_entry", None)
        url = entry.get_text().strip() if entry is not None else ""
        kind = self._classify_stream_url(url) if url else "invalid"
        return self.STREAM_KIND_ICONS.get(kind, "web-browser-symbolic")

    @staticmethod
    def _favicon_path(host):
        safe = re.sub(r"[^a-z0-9.-]", "_", (host or "").lower())[:100]
        return os.path.join(GLib.get_user_cache_dir(), "apricus", "favicons", f"{safe}.png")

    def _cached_favicon(self, host):
        if not host:
            return None
        path = self._favicon_path(host)
        try:
            if os.path.isfile(path) and time.time() - os.path.getmtime(path) < 30 * 86400:
                return path
        except OSError:
            pass
        return None

    @staticmethod
    def _favicon_magic_ok(data):
        """True for PNG, ICO, GIF or JPEG magic bytes."""
        if not data:
            return False
        return data[:8].startswith(
            (
                b"\x89PNG\r\n\x1a\n",
                b"\x00\x00\x01\x00",
                b"GIF8",
                b"\xff\xd8\xff",
            )
        )

    def _download_favicon(self, host):
        """Fetch + validate + normalize a favicon. Sync: call off main thread.

        Returns (path_or_None, reason) where reason is "ok", "http_NNN",
        "bad_content_type", "empty", "bad_magic", "decode_failed",
        "timeout", "connection" or "error" (also for refused non-public
        hops; oversized bodies report "empty"). Only transient reasons
        (timeout, connection, http_5xx) deserve a retry.
        """
        url = f"https://{host}/favicon.ico"
        from apricus.utils import fetch_public

        data, status, content_type, reason = fetch_public(url, timeout=8, max_bytes=100 * 1024)
        if reason == "timeout":
            return None, "timeout"
        if reason == "connection":
            return None, "connection"
        if reason in ("error", "refused"):
            logger.debug(f"[GUI4] Favicon download failed for {host}: {reason}")
            return None, "error"
        if status != 200:
            reason = f"http_{status}"
            if 500 <= status < 600:
                reason = "transient_http_5xx"
            return None, reason
        if "image" not in content_type:
            return None, "bad_content_type"
        if not data:
            return None, "empty"
        if not self._favicon_magic_ok(data):
            return None, "bad_magic"
        try:
            from gi.repository import GdkPixbuf

            loader = GdkPixbuf.PixbufLoader.new()
            loader.write_bytes(GLib.Bytes(data))
            loader.close()
            pixbuf = loader.get_pixbuf()
            if pixbuf is None:
                return None, "decode_failed"
            width = pixbuf.get_width()
            height = pixbuf.get_height()
            if width > 32:
                scaled_height = max(1, height * 32 // width)
                pixbuf = pixbuf.scale_simple(32, scaled_height, GdkPixbuf.InterpType.BILINEAR)
            out = self._favicon_path(host)
            os.makedirs(os.path.dirname(out), exist_ok=True)
            pixbuf.savev(out, "png", [], [])
        except Exception as e:  # noqa: BLE001 - decode/save failure means fallback
            logger.debug(f"[GUI4] Favicon decode failed for {host}: {e}")
            return None, "decode_failed"
        return out, "ok"

    def _fetch_favicon(self, host, url, generation):
        path, reason = self._download_favicon(host)
        if path is None and reason in ("timeout", "connection", "transient_http_5xx"):
            logger.info(f"[GUI4] Favicon retry for {host} after {reason}")
            path, reason = self._download_favicon(host)
        if path is None:
            logger.info(f"[GUI4] Favicon fallback for {host}: {reason}")
        else:
            logger.info(f"[GUI4] Favicon ok for {host}")
        GLib.idle_add(self._apply_favicon, url, generation, path, reason)

    def _apply_favicon(self, url, generation, path, reason=""):
        if generation != getattr(self, "_favicon_generation", generation):
            logger.info(f"[GUI4] Favicon stale generation for {url}, keeping current icon")
            return False
        entry = getattr(self, "stream_entry", None)
        if entry is None or entry.get_text().strip() != url:
            logger.info("[GUI4] Favicon entry changed, keeping current icon")
            return False
        if not path:
            self._set_favicon_state("fallback", reason=reason)
            return False
        self._set_favicon_state("ok", path=path)
        return False

    def _update_stream_type(self):
        """Subtitle + hero only. Never touches the type icon: the favicon
        state machine owns it, so a valid favicon survives retyping."""
        row = getattr(self, "stream_type_row", None)
        if row is None:
            return
        url = self.stream_entry.get_text().strip()
        if not url:
            row.set_subtitle(_("Paste a link to detect its type"))
            self._update_stream_hero("", "")
            return
        kind = self._classify_stream_url(url)
        service = self._service_name(url)
        if kind == "direct":
            subtitle = "Direct video — plays without extraction"
        elif kind == "page":
            subtitle = (
                f"{service} — {self._stream_site(url)}"
                if service
                else (f"Streaming page via yt-dlp — {self._stream_site(url)}")
            )
        elif kind == "collection":
            subtitle = "Playlist or channel — only single videos play"
        else:
            subtitle = "Not a valid http(s) URL"
        row.set_subtitle(self._plain(subtitle))
        self._update_stream_hero(url, kind)

    def _update_stream_hero(self, url, kind):
        if getattr(self, "stream_hero_title", None) is None:
            return
        if not url or kind in ("invalid",):
            self.stream_hero_title.set_text(_("Streaming"))
            self.stream_hero_sub.set_text(_("Paste a link to stream it as wallpaper"))
            return
        domain = self._stream_site(url)
        self.stream_hero_title.set_text(domain or _("Streaming"))
        self.stream_hero_sub.set_text(
            {
                "direct": "Direct video",
                "page": "Streaming page",
                "collection": "Playlist link",
            }.get(kind, "Streaming")
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
        row.set_title(self._plain(self._stream_row_title(entry)))
        row.set_subtitle(self._plain(url))
        thumb_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        thumb_box.set_size_request(96, 54)
        thumb_box.set_valign(Gtk.Align.CENTER)
        overlay = Gtk.Overlay()
        avatar = Adw.Avatar(text=self._web_monogram(self._stream_site(url)), size=36)
        avatar.set_tooltip_text(self._stream_site(url))
        overlay.set_child(avatar)
        picture = Gtk.Picture()
        picture.set_content_fit(Gtk.ContentFit.COVER)
        picture.set_size_request(96, 54)
        picture.set_can_shrink(True)
        cached_thumb = None
        try:
            cached_thumb = self._thumb_cache().lookup(url)
        except Exception:  # noqa: BLE001
            cached_thumb = None
        if cached_thumb and os.path.isfile(cached_thumb):
            picture.set_filename(cached_thumb)
            picture.set_visible(True)
            avatar.set_visible(False)
        else:
            picture.set_visible(False)
        overlay.add_overlay(picture)
        if entry.get("is_live"):
            badge = Gtk.Label(label=_("LIVE"))
            badge.add_css_class("stream-live-badge")
            badge.set_valign(Gtk.Align.START)
            badge.set_halign(Gtk.Align.START)
            overlay.add_overlay(badge)
        thumb_box.append(overlay)
        row.add_prefix(thumb_box)
        row._thumb_picture = picture
        row._thumb_avatar = avatar
        star = Gtk.ToggleButton()
        star.add_css_class("flat")
        star.set_icon_name("starred-symbolic" if favorite else "non-starred-symbolic")
        star.set_tooltip_text(_("Remove from saved") if favorite else _("Save"))
        star.set_active(favorite)
        star.set_valign(Gtk.Align.CENTER)
        star.connect("toggled", self._on_star_toggled, url)
        row.add_suffix(star)
        play = Gtk.Button()
        play.set_icon_name("media-playback-start-symbolic")
        play.set_tooltip_text(_("Apply this source now"))
        play.set_valign(Gtk.Align.CENTER)
        play.connect("clicked", self._on_saved_play, url)
        row.add_suffix(play)
        if allow_delete:
            trash = Gtk.Button()
            trash.set_icon_name("user-trash-symbolic")
            trash.set_tooltip_text(_("Remove from saved (kept in recent)"))
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
        sig = (
            tuple(favorites[:20]),
            tuple((e["url"], e.get("title") or "", bool(e.get("is_live"))) for e in history[:8]),
        )
        if sig != getattr(self, "_stream_lists_sig", None):
            self._stream_lists_sig = sig
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
        self._thumb_generation = getattr(self, "_thumb_generation", 0) + 1
        self._schedule_visible_thumbs()

    def _thumb_cache(self):
        from apricus.stream_thumbs import ThumbCache

        cache = getattr(self, "_stream_thumb_cache", None)
        if cache is None:
            cache = ThumbCache(os.path.join(GLib.get_user_cache_dir(), "apricus", "stream-thumbs"))
            self._stream_thumb_cache = cache
        return cache

    def _thumb_fetcher(self):
        from apricus.stream_thumbs import ThumbFetcher

        fetcher = getattr(self, "_stream_thumb_fetcher", None)
        if fetcher is None:
            fetcher = ThumbFetcher(self._thumb_cache(), max_concurrent=2)
            self._stream_thumb_fetcher = fetcher
        return fetcher

    def _thumb_pool(self):
        from concurrent.futures import ThreadPoolExecutor

        pool = getattr(self, "_stream_thumb_pool", None)
        if pool is None:
            pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="streamthumb")
            self._stream_thumb_pool = pool
        return pool

    def _visible_stream_rows(self):
        """(listbox, row, url) for mapped rows currently in the viewport."""
        found = []
        for listbox in (
            getattr(self, "saved_list", None),
            getattr(self, "recent_list", None),
        ):
            if listbox is None or not listbox.get_mapped():
                continue
            scrolled = listbox.get_ancestor(Gtk.ScrolledWindow)
            if scrolled is None:
                continue
            viewport = scrolled.get_child()
            if viewport is None:
                continue
            vadj = scrolled.get_vadjustment()
            top, bottom = vadj.get_value(), vadj.get_value() + vadj.get_page_size()
            child = listbox.get_first_child()
            while child is not None:
                url = child.get_name()
                if url and child.get_mapped():
                    ok, bounds = child.compute_bounds(viewport)
                    if ok:
                        y, height = bounds.origin.y, bounds.size.height
                        if height > 0 and y + height >= top and y <= bottom:
                            found.append((listbox, child, url))
                child = child.get_next_sibling()
        return found

    def _schedule_visible_thumbs(self):
        if getattr(self, "_thumb_debounce", None) is not None:
            GLib.source_remove(self._thumb_debounce)
            self._thumb_debounce = None

        def _later():
            self._thumb_debounce = None
            self._fetch_visible_thumbs()
            return False

        self._thumb_debounce = GLib.timeout_add(150, _later)

    def _fetch_visible_thumbs(self):
        generation = getattr(self, "_thumb_generation", 0)
        for _listbox, row, url in self._visible_stream_rows():
            picture = getattr(row, "_thumb_picture", None)
            if picture is None or picture.get_visible():
                continue  # already has a texture (or no picture slot)
            entry_live = self._stream_entry_live(url)
            self._thumb_pool().submit(self._fetch_row_thumb_job, generation, row, url, entry_live)

    def _stream_entry_live(self, url):
        for entry in self._load_stream_data()[0]:
            if entry["url"] == url:
                return bool(entry.get("is_live"))
        return False

    @staticmethod
    def _youtube_thumb_id(url):
        import re

        match = re.search(
            r"(?:youtu\.be/|youtube\.com/(?:watch\?.*v=|shorts/|live/|embed/))([A-Za-z0-9_-]{6,})",
            url or "",
        )
        return match.group(1) if match else ""

    def _fetch_row_thumb(self, url):
        """Size-limited thumbnail bytes for a row, or None (fallback)."""
        from apricus.utils import fetch_public

        video_id = self._youtube_thumb_id(url)
        if not video_id:
            return None
        data, _status, content_type, reason = fetch_public(
            f"https://i.ytimg.com/vi/{video_id}/mqdefault.jpg",
            timeout=10,
            max_bytes=512 * 1024,
        )
        if data is None or "image" not in content_type:
            logger.debug(f"[GUI4] Row thumbnail refused ({reason})")
            return None
        return data

    def _fetch_row_thumb_job(self, generation, row, url, is_live):
        self._thumb_fetcher().fetch(
            url,
            self._fetch_row_thumb,
            lambda path, reason: GLib.idle_add(self._apply_row_thumb, generation, row, url, path),
            is_live=is_live,
        )

    def _apply_row_thumb(self, generation, row, url, path):
        if generation != getattr(self, "_thumb_generation", generation):
            return False
        try:
            alive = row.get_parent() is not None and row.get_name() == url
        except Exception:  # noqa: BLE001 - destroyed row
            return False
        if not alive or not path:
            return False
        picture = getattr(row, "_thumb_picture", None)
        avatar = getattr(row, "_thumb_avatar", None)
        if picture is None:
            return False
        picture.set_filename(path)
        picture.set_visible(True)
        if avatar is not None:
            avatar.set_visible(False)
        return False

    def _update_stream_live_flag(self, url, is_live, title=""):
        history, favorites = self._load_stream_data()
        changed = False
        for entry in history:
            if entry["url"] != url:
                continue
            if bool(entry.get("is_live")) != bool(is_live):
                entry["is_live"] = bool(is_live)
                changed = True
            if title and entry.get("title") != title:
                entry["title"] = title[:200]
                changed = True
        if changed:
            self._save_stream_data(history, favorites)

    def _update_stream_live_flag_idle(self, url, is_live, title=""):
        self._update_stream_live_flag(url, is_live, title)
        return False

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
        self._toast(_("Stream history cleared"))

    def on_stream_try(self, *_args):
        url = self.stream_entry.get_text().strip()
        if not url:
            return
        kind = self._classify_stream_url(url)
        if kind == "invalid":
            self._show_error(_("Not a valid http(s) URL."))
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
            self._show_error(
                _("Failed to stream {url}.\nError:\n{error}").format(url=url, error=error)
            )
            return False
        previous = (self.config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "")
        server = self._server()
        if server is not None:
            server.stream(url, "all")
        self._record_stream_use(url, site=self._stream_site(url), kind=kind)
        self._reload_stream_lists()
        self._refresh_all()
        if getattr(self, "_try_timeout", None) is not None:
            GLib.source_remove(self._try_timeout)
        self._try_timeout = GLib.timeout_add_seconds(10, self._end_stream_try, previous)
        toast = Adw.Toast.new(_("Previewing for 10 seconds"))
        toast.set_button_label(_("Stop now"))
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
        self._toast(_("Preview finished — Apply to keep watching"))
        return False

    def _cancel_stream_try(self, _toast, previous):
        if getattr(self, "_try_timeout", None) is not None:
            GLib.source_remove(self._try_timeout)
            self._try_timeout = None
        server = self._server()
        if server is not None:
            try:
                if previous:
                    server.stream(previous, "all")
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

    SERVICE_NAMES = (
        (("youtube.com", "youtu.be", "youtube-nocookie.com"), "YouTube"),
        (("twitch.tv",), "Twitch"),
        (("vimeo.com", "player.vimeo.com"), "Vimeo"),
        (("dailymotion.com", "dai.ly"), "Dailymotion"),
        (("kick.com",), "Kick"),
        (("facebook.com", "fb.watch"), "Facebook"),
        (("tiktok.com",), "TikTok"),
        (("x.com", "twitter.com"), "X"),
    )

    @staticmethod
    def _service_name(url):
        try:
            from urllib.parse import urlparse

            host = (urlparse(url).hostname or "").lower()
        except Exception:  # noqa: BLE001 - unknown service
            return ""
        host = re.sub(r"^(www|m|music|gaming)\.", "", host)
        for suffixes, name in ControlPanel.SERVICE_NAMES:
            if any(host == suffix or host.endswith("." + suffix) for suffix in suffixes):
                return name
        return ""

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

    def _load_web_data(self):
        config = getattr(self, "config", None) or {}
        history = ConfigUtil._sanitize_web_entries(config.get(CONFIG_KEY_WEB_HISTORY))
        favorites = [u for u in (config.get(CONFIG_KEY_WEB_FAVORITES) or []) if isinstance(u, str)]
        return history, favorites

    def _save_web_data(self, history, favorites):
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_WEB_HISTORY] = ConfigUtil._sanitize_web_entries(history)
        self.config[CONFIG_KEY_WEB_FAVORITES] = list(dict.fromkeys(favorites))[:50]
        ConfigUtil().save(self.config)

    def _record_web_use(self, url, title=""):
        history, favorites = self._load_web_data()
        history = [e for e in history if e["url"] != url]
        history.insert(
            0,
            {
                "url": url,
                "title": title,
                "site": self._web_domain(url),
                "last_used": time.time(),
            },
        )
        self._save_web_data(history, favorites)

    def _toggle_web_favorite(self, url):
        history, favorites = self._load_web_data()
        if url in favorites:
            favorites.remove(url)
        else:
            favorites.insert(0, url)
        self._save_web_data(history, favorites)
        self._reload_web_lists()

    def _check_stream_url(self, url):
        """Validate a streaming URL with yt-dlp; None when valid, else error."""
        from apricus.utils import public_http_url, valid_http_url

        if not valid_http_url(url):
            return "URL not supported."
        if not public_http_url(url):
            return "Local network addresses are not supported."
        cached = getattr(self, "_stream_meta_cache", None)
        if cached and cached[0] == url and (time.time() - cached[1]) < 120:
            return None
        from apricus.yt_utils import get_cached_meta, save_cached_meta

        disk_meta = get_cached_meta(url)
        if disk_meta is not None:
            self._stream_meta_cache = (url, time.time(), disk_meta)
            return None
        try:
            with yt_dlp.YoutubeDL(base_options()) as ydl:
                meta = ydl.extract_info(url, download=False) or {}
            if meta.get("formats"):
                self._stream_meta_cache = (url, time.time(), meta)
                save_cached_meta(url, meta)
        except Exception as e:  # noqa: BLE001 - any backend failure is a bad URL
            return self._classify_stream_error(str(e))
        return None

    @staticmethod
    def _classify_stream_error(message):
        from apricus.yt_utils import classify_stream_error

        return classify_stream_error(message)

    def _check_direct_url(self, url):
        """Fast-path HEAD check; None when playable, 'fallback' for yt-dlp.

        Follows redirects hop-by-hop with allow_redirects=False so an
        intermediate redirect to a private/loopback address is refused
        before any socket touches it.
        """
        from urllib.parse import urljoin

        from apricus.utils import public_http_url

        current = url
        for _hop in range(4):
            if not public_http_url(current):
                return "fallback"
            try:
                response = requests.head(current, timeout=5, allow_redirects=False)
                try:
                    status = response.status_code
                    location = response.headers.get("Location", "")
                    content_type = response.headers.get("Content-Type", "")
                finally:
                    response.close()
            except requests.exceptions.RequestException:
                return "fallback"
            if status in (301, 302, 303, 307, 308) and location:
                current = urljoin(current, location)
                continue
            if "video/" in content_type or "mpegurl" in content_type or "dash+xml" in content_type:
                return None
            return "fallback"
        return "fallback"

    def _set_apply_busy(self, button, busy):
        if button is None:
            return
        button.set_sensitive(not busy)
        button.set_label(_("Checking…") if busy else _("Apply"))

    def on_stream_apply(self, *_args):
        url = self.stream_entry.get_text().strip()
        if not url:
            return
        kind = self._classify_stream_url(url)
        if kind == "invalid":
            self._show_error(_("Not a valid http(s) URL."))
            return
        if kind == "collection":
            self._show_collection_warning(url)
            return
        self._show_stream_monitor_chooser(url)

    def _start_stream_for_monitor(self, url, monitor):
        kind = self._classify_stream_url(url)
        if kind in ("invalid", "collection"):
            kind = "page"
        self._set_apply_busy(getattr(self, "stream_apply_btn", None), True)
        threading.Thread(
            target=self._validate_and_stream,
            args=(url, kind, monitor),
            daemon=True,
        ).start()

    def _show_collection_warning(self, url):
        dialog = Adw.AlertDialog(
            heading=_("Playlist links aren't supported"),
            body=(
                _(
                    "That link points to a playlist or channel. "
                    "Apricus plays single videos — use the video's own link."
                )
            ),
        )
        dialog.add_response("cancel", _("Cancel"))
        dialog.add_response("use", _("Use video anyway"))
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect(
            "response",
            lambda _d, r: self._show_stream_monitor_chooser(url) if r == "use" else None,
        )
        dialog.present(self.window)

    def _validate_and_stream(self, url, kind="page", monitor=None):
        if kind == "direct" and self._check_direct_url(url) is None:
            error = None
        else:
            error = self._check_stream_url(url)
        GLib.idle_add(self._finish_stream_apply, url, error, kind, monitor)

    def _finish_stream_apply(self, url, error, kind="page", monitor=None):
        self._set_apply_busy(getattr(self, "stream_apply_btn", None), False)
        if error is not None:
            logger.error(f"[GUI4] Failed to stream {url}. Error:\n{error}")
            self._show_error(
                _("Failed to stream {url}.\nError:\n{error}").format(url=url, error=error)
            )
            return False
        logger.info(f"[GUI4] Streaming: {url}")
        self._record_stream_use(url, site=self._stream_site(url), kind=kind)
        self._reload_stream_lists()
        if monitor is None:
            self._show_stream_monitor_chooser(url)
        else:
            self._apply_stream_to_monitor(url, monitor)
        return False

    def _show_stream_monitor_chooser(self, url):
        if not self._monitor_choices():
            self._start_stream_for_monitor(url, "")
            return
        self._popup_monitor_chooser(
            self.stream_apply_btn,
            url,
            lambda k: self._start_stream_for_monitor(url, k),
        )

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

    WEB_EXAMPLES = (
        ("Windy", "https://www.windy.com/", "Live weather map"),
        ("Earth", "https://earth.nullschool.net/", "Live earth data"),
        ("Attacks", "https://www.digitalattackmap.com/", "Live cyber attacks"),
        ("Bitcoin", "https://bitcoin.clarkmoody.com/", "Live market dashboard"),
        ("Lightning", "https://www.lightningmaps.org/", "Live lightning map"),
        ("Silk", "https://weavesilk.com/", "Interactive silk art"),
    )

    @staticmethod
    def _web_domain(url):
        try:
            from urllib.parse import urlparse

            return urlparse(url).hostname or ""
        except Exception:  # noqa: BLE001 - no domain deducible
            return ""

    @staticmethod
    def _web_monogram(domain):
        import re

        parts = [p for p in re.split(r"[^a-z0-9]", (domain or "").lower()) if p]
        while parts and parts[0] in ("www", "m", "mobile"):
            parts.pop(0)
        if not parts:
            return ""
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[1][:1]).upper()

    @staticmethod
    def _extract_html_title(data):
        import html
        import re

        try:
            text = bytes(data)[:65536].decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001 - undecodable body
            return ""
        match = re.search(r"<title[^>]*>(.*?)</title>", text, re.IGNORECASE | re.DOTALL)
        if not match:
            return ""
        return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()[:200]

    @staticmethod
    def _format_file_size(size):
        try:
            size = int(size)
        except (TypeError, ValueError):
            return "—"
        if size < 1024:
            return f"{size} B"
        if size < 1024 * 1024:
            return f"{size / 1024:.0f} KB"
        return f"{size / (1024 * 1024):.1f} MB"

    def _webpage_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_size_request(self.PAGE_MIN_WIDTH, -1)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        self._web_generation = 0
        hero, refs = self._build_hero(
            "web-browser-symbolic",
            _("Web Page"),
            _("Display a website or HTML file as wallpaper"),
        )
        self.web_hero_icon = refs["icon"]
        self.web_hero_title = refs["title"]
        self.web_hero_sub = refs["sub"]
        box.append(hero)
        self.webpage_entry = Gtk.Entry()
        self.webpage_entry.set_placeholder_text("https://example.com")
        self.webpage_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.PRIMARY, "web-browser-symbolic"
        )
        self.webpage_entry.set_icon_from_icon_name(
            Gtk.EntryIconPosition.SECONDARY, "edit-paste-symbolic"
        )
        self.webpage_entry.set_icon_tooltip_text(
            Gtk.EntryIconPosition.SECONDARY, _("Paste from clipboard")
        )
        self.webpage_entry.connect("activate", self.on_webpage_apply)
        self.webpage_entry.connect("icon-press", self._on_webpage_icon_press)
        self.webpage_entry.connect("changed", self._on_webpage_entry_changed)
        box.append(self.webpage_entry)
        self.web_detect_row = Adw.ActionRow()
        self.web_detect_row.set_title(_("Detected"))
        self.web_detect_row.set_subtitle(_("Paste a link to detect its type"))
        self.web_detect_icon = Gtk.Image.new_from_icon_name("web-browser-symbolic")
        self.web_detect_row.add_prefix(self.web_detect_icon)
        self.web_detect_spinner = Gtk.Spinner()
        self.web_detect_spinner.set_valign(Gtk.Align.CENTER)
        self.web_detect_row.add_suffix(self.web_detect_spinner)
        box.append(self.web_detect_row)
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        row.set_halign(Gtk.Align.FILL)
        apply_btn = Gtk.Button(label=_("Apply"))
        apply_btn.add_css_class("suggested-action")
        apply_btn.connect("clicked", self.on_webpage_apply)
        open_btn = Gtk.Button(label=_("Open in browser"))
        open_btn.set_tooltip_text(_("Open the entered address in the browser"))
        open_btn.connect("clicked", self.on_webpage_open_browser)
        row.append(apply_btn)
        row.append(open_btn)
        box.append(row)
        self.webpage_apply_btn = apply_btn
        file_group = Adw.PreferencesGroup()
        file_group.set_title(_("Local web page"))
        self.web_file_row = Adw.ActionRow()
        self.web_file_row.set_title(_("No page chosen"))
        self.web_file_row.set_subtitle(_("Choose an HTML web page on this PC"))
        file_icon = Gtk.Image.new_from_icon_name("x-office-document-symbolic")
        self.web_file_row.add_prefix(file_icon)
        choose_btn = Gtk.Button(label=_("Choose…"))
        choose_btn.set_tooltip_text(_("Choose local HTML web page"))
        choose_btn.set_valign(Gtk.Align.CENTER)
        choose_btn.connect("clicked", self.on_webpage_choose_file)
        self.web_file_row.add_suffix(choose_btn)
        folder_btn = Gtk.Button()
        folder_btn.set_icon_name("folder-open-symbolic")
        folder_btn.set_tooltip_text(_("Show the file in the file manager"))
        folder_btn.set_valign(Gtk.Align.CENTER)
        folder_btn.connect("clicked", self.on_webpage_show_in_folder)
        self.web_file_row.add_suffix(folder_btn)
        file_group.add(self.web_file_row)
        box.append(file_group)
        drop_html = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        drop_html.connect("drop", self._on_webpage_drop)
        file_group.add_controller(drop_html)
        insp_group = Adw.PreferencesGroup()
        insp_group.set_title(_("Inspiration"))
        insp_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        for name, url, desc in self.WEB_EXAMPLES:
            chip = Gtk.Button()
            chip_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            chip_icon = Gtk.Image.new_from_icon_name("web-browser-symbolic")
            chip_box.append(chip_icon)
            chip_label = Gtk.Label(label=name)
            chip_box.append(chip_label)
            chip.set_child(chip_box)
            chip.set_tooltip_text(f"{desc} — {url}")
            chip.connect("clicked", self._on_web_example, url)
            insp_row.append(chip)
        insp_group.add(insp_row)
        box.append(insp_group)
        self.web_details = Adw.PreferencesGroup()
        self.web_details.set_title(_("Details"))
        self.web_detail_rows = {}
        for key, title, icon_name in (
            ("title", "Title", "x-office-document-symbolic"),
            ("address", "Address", "web-browser-symbolic"),
            ("source", "Source", "folder-symbolic"),
            ("status", "Status", "object-select-symbolic"),
        ):
            action_row = Adw.ActionRow()
            action_row.set_title(title)
            icon = Gtk.Image.new_from_icon_name(icon_name)
            action_row.add_prefix(icon)
            self.web_details.add(action_row)
            self.web_detail_rows[key] = action_row
        copy_btn = Gtk.Button()
        copy_btn.set_icon_name("edit-copy-symbolic")
        copy_btn.set_tooltip_text(_("Copy link"))
        copy_btn.set_valign(Gtk.Align.CENTER)
        copy_btn.connect("clicked", self.on_webpage_copy_link)
        self.web_detail_rows["address"].add_suffix(copy_btn)
        box.append(self.web_details)
        self.web_saved_group = Adw.PreferencesGroup()
        self.web_saved_group.set_title(_("Saved pages"))
        self.web_saved_list = Gtk.ListBox()
        self.web_saved_list.add_css_class("boxed-list")
        self.web_saved_list.connect("row-activated", self._on_web_list_activated)
        self.web_saved_group.add(self.web_saved_list)
        box.append(self.web_saved_group)
        web_recent_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        web_recent_label = Gtk.Label(label=_("Recent"))
        web_recent_label.set_xalign(0)
        web_recent_label.set_hexpand(True)
        web_recent_box.append(web_recent_label)
        web_clear_btn = Gtk.Button(label=_("Clear"))
        web_clear_btn.connect("clicked", self.on_clear_web_history)
        web_recent_box.append(web_clear_btn)
        box.append(web_recent_box)
        self.web_recent_list = Gtk.ListBox()
        self.web_recent_list.add_css_class("boxed-list")
        self.web_recent_list.connect("row-activated", self._on_web_list_activated)
        box.append(self.web_recent_list)
        self.web_empty = Adw.StatusPage()
        self.web_empty.set_icon_name("web-browser-symbolic")
        self.web_empty.set_title(_("No saved pages yet"))
        self.web_empty.set_description(_("Apply a link or file above. You will find it here."))
        box.append(self.web_empty)
        self._refresh_web_file_row()
        self._refresh_web_details()
        self._sync_web_hero_with_config()
        self._reload_web_lists()
        return self._scroll_page(self._clamp_page(box))

    def _web_list_title(self, entry):
        if entry.get("title"):
            title = entry["title"]
            return title if len(title) <= 60 else title[:57] + "…"
        url = entry.get("url", "")
        if "://" in url:
            return self._web_domain(url) or url
        return os.path.basename(url) or url

    def _web_list_row(self, entry, favorite, allow_delete):
        url = entry["url"]
        row = Adw.ActionRow()
        row.set_name(url)
        row.set_title(self._plain(self._web_list_title(entry)))
        row.set_subtitle(self._plain(url))
        is_file = "://" not in url
        if is_file:
            prefix = Gtk.Image.new_from_icon_name("x-office-document-symbolic")
        else:
            prefix = Adw.Avatar(text=self._web_monogram(self._web_domain(url)), size=32)
            prefix.set_show_initials(True)
        row.add_prefix(prefix)
        star = Gtk.ToggleButton()
        star.add_css_class("flat")
        star.set_icon_name("starred-symbolic" if favorite else "non-starred-symbolic")
        star.set_tooltip_text(_("Remove from saved") if favorite else _("Save"))
        star.set_active(favorite)
        star.set_valign(Gtk.Align.CENTER)
        star.connect("toggled", self._on_web_star_toggled, url)
        row.add_suffix(star)
        if is_file:
            go = Gtk.Button()
            go.set_icon_name("media-playback-start-symbolic")
            go.set_tooltip_text(_("Apply this file now"))
            go.set_valign(Gtk.Align.CENTER)
            go.connect("clicked", self._on_web_list_play, url)
            row.add_suffix(go)
        else:
            globe = Gtk.Button()
            globe.set_icon_name("web-browser-symbolic")
            globe.set_tooltip_text(_("Open in browser"))
            globe.set_valign(Gtk.Align.CENTER)
            globe.connect("clicked", self._on_web_open, url)
            row.add_suffix(globe)
        if allow_delete:
            trash = Gtk.Button()
            trash.set_icon_name("user-trash-symbolic")
            trash.set_tooltip_text(_("Remove from recent (kept in saved)"))
            trash.set_valign(Gtk.Align.CENTER)
            trash.connect("clicked", self._on_web_delete, url)
            row.add_suffix(trash)
        return row

    def _reload_web_lists(self):
        if getattr(self, "web_saved_list", None) is None:
            return
        history, favorites = self._load_web_data()
        fav_set = set(favorites)
        self._clear_listbox(self.web_saved_list)
        for url in favorites[:20]:
            entry = next((e for e in history if e["url"] == url), None)
            if entry is None:
                entry = {
                    "url": url,
                    "title": "",
                    "site": self._web_domain(url),
                    "last_used": 0,
                }
            self.web_saved_list.append(self._web_list_row(entry, True, False))
        self._clear_listbox(self.web_recent_list)
        for entry in history[:8]:
            self.web_recent_list.append(self._web_list_row(entry, entry["url"] in fav_set, True))
        empty = not favorites and not history
        self.web_saved_group.set_visible(not empty)
        self.web_recent_list.set_visible(not empty)
        self.web_empty.set_visible(empty)

    def _on_web_list_activated(self, _listbox, row):
        url = row.get_name()
        if not url:
            return
        if "://" in url:
            self.webpage_entry.set_text(url)
            self._on_webpage_entry_changed(self.webpage_entry)
            self.on_webpage_apply()
        elif os.path.isfile(url):
            self._set_webpage_file(url)

    def _on_web_star_toggled(self, button, url):
        active = bool(button.get_active())
        button.set_icon_name("starred-symbolic" if active else "non-starred-symbolic")
        _history, favorites = self._load_web_data()
        if active and url not in favorites:
            favorites.insert(0, url)
        elif not active and url in favorites:
            favorites.remove(url)
        history, _favorites = self._load_web_data()
        self._save_web_data(history, favorites)
        self._reload_web_lists()

    def _on_web_list_play(self, _button, url):
        if os.path.isfile(url):
            self._set_webpage_file(url)

    def _on_web_open(self, _button, url):
        if not url.startswith(("http://", "https://")):
            return
        try:
            Gio.AppInfo.launch_default_for_uri(url)
        except GLib.Error as e:
            logger.warning(f"[GUI4] Could not open browser: {e.message}")

    def _on_web_delete(self, _button, url):
        history, favorites = self._load_web_data()
        self._save_web_data([e for e in history if e["url"] != url], favorites)
        self._reload_web_lists()

    def on_clear_web_history(self, *_args):
        history, favorites = self._load_web_data()
        fav_set = set(favorites)
        self._save_web_data([e for e in history if e["url"] in fav_set], favorites)
        self._reload_web_lists()
        self._toast(_("Web history cleared"))

    def _on_webpage_icon_press(self, entry, position):
        if position == Gtk.EntryIconPosition.SECONDARY:
            clipboard = Gdk.Display.get_default().get_clipboard()
            clipboard.read_text_async(None, self._on_webpage_clipboard_text)

    def _on_webpage_clipboard_text(self, clipboard, result):
        try:
            text = clipboard.read_text_finish(result)
        except GLib.Error:
            return
        if text and text.strip():
            self.webpage_entry.set_text(text.strip())
            self._on_webpage_entry_changed(self.webpage_entry)

    def _on_webpage_entry_changed(self, entry):
        url = entry.get_text().strip()
        if not url:
            self._set_web_detect("web-browser-symbolic", "Paste a link to detect its type", False)
            self._update_web_hero("", "")
            return
        if not url.startswith(("http://", "https://")):
            self._set_web_detect(
                "dialog-error-symbolic", "Enter a URL starting with http(s)://", False
            )
            self._update_web_hero("", "")
            return
        domain = self._web_domain(url)
        self._set_web_detect("web-browser-symbolic", f"{domain} — checking…", True)
        self._update_web_hero(url, "")
        self._web_generation += 1
        generation = self._web_generation
        threading.Thread(
            target=self._check_web_details, args=(url, generation), daemon=True
        ).start()

    def _set_web_detect(self, icon_name, subtitle, spinning):
        self.web_detect_icon.set_from_icon_name(icon_name)
        self.web_detect_row.set_subtitle(self._plain(subtitle))
        self.web_detect_spinner.set_spinning(spinning)

    def _check_web_details(self, url, generation):
        from apricus.utils import fetch_public

        title, ok, detail = "", False, ""
        body, status, content_type, reason = fetch_public(url, timeout=10, max_bytes=64 * 1024)
        if reason != "ok":
            GLib.idle_add(self._apply_web_details, generation, url, "", False, "")
            return
        body = body or b""
        if status >= 400:
            detail = f"Error code: {status}"
        else:
            ok = True
            title = self._extract_html_title(body) if "html" in content_type else ""
            detail = f"{self._web_domain(url)} — reachable"
        GLib.idle_add(self._apply_web_details, generation, url, title, ok, detail)

    def _apply_web_details(self, generation, url, title, ok, detail):
        if generation != getattr(self, "_web_generation", generation):
            return False
        if ok:
            self._verified_web_url = url
            self._set_web_detect("object-select-symbolic", detail, False)
        else:
            if getattr(self, "_verified_web_url", None) == url:
                self._verified_web_url = None
            self._set_web_detect("dialog-error-symbolic", detail, False)
        self._update_web_hero(url, title)
        return False

    def _update_web_hero(self, url, title):
        if getattr(self, "web_hero_title", None) is None:
            return
        domain = self._web_domain(url)
        is_file = bool(url) and "://" not in url
        if title:
            self.web_hero_title.set_text(title)
        elif domain:
            self.web_hero_title.set_text(domain)
        elif is_file:
            self.web_hero_title.set_text(os.path.basename(url))
        else:
            self.web_hero_title.set_text(_("Web Page"))
        if url:
            self.web_hero_sub.set_text(url)
        else:
            self.web_hero_sub.set_text(_("Type an address above to load a preview"))
        self.web_hero_sub.set_tooltip_text(url)

    def _on_web_example(self, _button, url):
        self.webpage_entry.set_text(url)
        self._on_webpage_entry_changed(self.webpage_entry)
        self._toast(_("Example loaded"))

    def _on_webpage_drop(self, _target, value, _x, _y):
        path = None
        if isinstance(value, Gio.File):
            if value.get_uri_scheme() in ("http", "https"):
                self.webpage_entry.set_text(value.get_uri())
                self._on_webpage_entry_changed(self.webpage_entry)
                return True
            path = value.get_path()
        if path and path.lower().endswith((".html", ".htm")):
            self._set_webpage_file(path)
            return True
        return False

    def _sync_web_hero_with_config(self):
        config = getattr(self, "config", None) or {}
        if config.get(CONFIG_KEY_MODE) != MODE_WEBPAGE:
            return
        current = (config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "")
        if not current:
            return
        if "://" not in current and current.lower().endswith((".html", ".htm")):
            self._refresh_web_file_row()
            self._update_web_hero(current, "")
        elif "://" in current:
            self.webpage_entry.set_text(current)
            self._update_web_hero(current, "")

    def _set_webpage_file(self, path):
        try:
            size = os.path.getsize(path)
        except OSError:
            size = None
        self.web_file_row.set_title(self._plain(os.path.basename(path)))
        self.web_file_row.set_subtitle(
            self._plain(f"{path} — {self._format_file_size(size)}" if size is not None else path)
        )
        self._update_web_hero(path, "")
        self._set_webpage_source(path)

    def on_webpage_open_browser(self, *_args):
        url = self.webpage_entry.get_text().strip()
        if not url.startswith(("http://", "https://")):
            return
        try:
            Gio.AppInfo.launch_default_for_uri(url)
        except GLib.Error as e:
            logger.warning(f"[GUI4] Could not open browser: {e.message}")

    def on_webpage_show_in_folder(self, *_args):
        subtitle = self.web_file_row.get_subtitle()
        folder = os.path.dirname(subtitle.split(" — ")[0])
        if not folder or not os.path.isdir(folder):
            return
        try:
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(folder))
        except GLib.Error as e:
            logger.warning(f"[GUI4] Could not open containing folder: {e.message}")

    def on_webpage_copy_link(self, *_args):
        url = self.web_detail_rows["address"].get_subtitle()
        if not url:
            return
        clipboard = Gdk.Display.get_default().get_clipboard()
        clipboard.set(url)
        self._toast(_("Link copied"))

    def _refresh_web_file_row(self):
        row = getattr(self, "web_file_row", None)
        if row is None:
            return
        config = getattr(self, "config", None) or {}
        current = (config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "")
        is_web_file = (
            config.get(CONFIG_KEY_MODE) == MODE_WEBPAGE
            and bool(current)
            and "://" not in current
            and current.lower().endswith((".html", ".htm"))
            and os.path.isfile(current)
        )
        if not is_web_file:
            row.set_title(_("No page chosen"))
            row.set_subtitle(_("Choose an HTML web page on this PC"))
            return
        try:
            size = os.path.getsize(current)
        except OSError:
            size = None
        row.set_title(self._plain(os.path.basename(current)))
        row.set_subtitle(
            self._plain(
                f"{current} — {self._format_file_size(size)}" if size is not None else current
            )
        )

    def _refresh_web_details(self):
        group = getattr(self, "web_details", None)
        if group is None:
            return
        mode = self.config.get(CONFIG_KEY_MODE) if hasattr(self, "config") else None
        current = ""
        if hasattr(self, "config") and self.config:
            current = (self.config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "")
        show = mode == MODE_WEBPAGE and bool(current)
        group.set_visible(show)
        if not show:
            return
        is_file = os.path.isfile(current) if current and "://" not in current else False
        rows = self.web_detail_rows
        if is_file:
            try:
                size = os.path.getsize(current)
            except OSError:
                size = None
            rows["title"].set_subtitle(self._plain(os.path.basename(current)))
            rows["address"].set_subtitle(self._plain(current))
            rows["source"].set_subtitle(
                self._plain(_("Local web page — {size}").format(size=self._format_file_size(size)))
            )
        else:
            rows["title"].set_subtitle("…")
            rows["address"].set_subtitle(self._plain(current))
            rows["source"].set_subtitle(
                self._plain(_("Remote — {domain}").format(domain=self._web_domain(current)))
            )
            self._web_generation += 1
            generation = self._web_generation
            threading.Thread(
                target=self._fetch_web_title, args=(current, generation), daemon=True
            ).start()
        active = (self.config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "") == current
        rows["status"].set_subtitle(_("Current wallpaper") if active else _("Not applied"))

    def _fetch_web_title(self, url, generation):
        title = ""
        try:
            body = _capped_get(url, timeout=10, max_bytes=64 * 1024) or b""
            title = self._extract_html_title(body)
        except requests.exceptions.RequestException as e:
            logger.debug(f"[GUI4] Title fetch failed for {url}: {e}")
        GLib.idle_add(self._apply_web_fetched_title, generation, url, title)

    def _apply_web_fetched_title(self, generation, url, title):
        if generation != getattr(self, "_web_generation", generation):
            return False
        current = (self.config.get(CONFIG_KEY_DATA_SOURCE) or {}).get("Default", "")
        if current != url:
            return False
        if title:
            self.web_detail_rows["title"].set_subtitle(self._plain(title))
        return False

    def _check_web_url(self, url):
        """Validate a web page URL; None when valid, else error.

        Follows redirects hop-by-hop with allow_redirects=False so no
        intermediate hop can contact a private or loopback host.
        """
        from urllib.parse import urljoin

        from apricus.utils import public_http_url, valid_http_url

        if not valid_http_url(url):
            return "Not a valid http(s) URL."
        if not public_http_url(url):
            return "Local network addresses are not supported."
        current = url
        for _hop in range(4):
            try:
                response = requests.get(current, timeout=10, stream=True, allow_redirects=False)
                try:
                    status = response.status_code
                    location = response.headers.get("Location", "")
                finally:
                    response.close()
            except requests.exceptions.RequestException as e:
                return str(e)
            if status in (301, 302, 303, 307, 308) and location:
                current = urljoin(current, location)
                if not public_http_url(current):
                    return "Redirects to a local network address."
                continue
            if status >= 400:
                return f"Error code: {status}"
            return None
        return "Too many redirects."

    def on_webpage_apply(self, *_args):
        from apricus.utils import valid_http_url

        url = self.webpage_entry.get_text().strip()
        if not url:
            return
        if not valid_http_url(url):
            self._show_error(_("Not a valid http(s) URL."))
            return
        self._show_webpage_monitor_chooser(url)

    def _start_webpage_for_monitor(self, source, monitor):
        if "://" not in source or getattr(self, "_verified_web_url", None) == source:
            self._record_web_use(source)
            self._reload_web_lists()
            self._apply_webpage_to_monitor(source, monitor)
            return
        self._set_apply_busy(getattr(self, "webpage_apply_btn", None), True)
        threading.Thread(
            target=self._validate_and_webpage, args=(source, monitor), daemon=True
        ).start()

    def _validate_and_webpage(self, url, monitor=None):
        error = self._check_web_url(url)
        GLib.idle_add(self._finish_webpage_apply, url, error, monitor)

    def _finish_webpage_apply(self, url, error, monitor=None):
        self._set_apply_busy(getattr(self, "webpage_apply_btn", None), False)
        if error is not None:
            logger.error(f"[GUI4] Failed to access {url}. Error:\n{error}")
            self._show_error(
                _("Failed to access {url}.\nError:\n{error}").format(url=url, error=error)
            )
            return False
        self._verified_web_url = url
        self._record_web_use(url)
        self._reload_web_lists()
        if monitor is None:
            self._show_webpage_monitor_chooser(url)
        else:
            self._apply_webpage_to_monitor(url, monitor)
        return False

    def _show_webpage_monitor_chooser(self, source):
        if not self._monitor_choices():
            self._start_webpage_for_monitor(source, "")
            return
        anchor = getattr(self, "webpage_apply_btn", None) or self.window
        self._popup_monitor_chooser(
            anchor,
            source,
            lambda k: self._start_webpage_for_monitor(source, k),
        )

    def _apply_webpage_to_monitor(self, source, monitor):
        logger.info(f"[GUI4] Webpage: {source} For Monitor {monitor or 'all'}")
        self.config = ConfigUtil().load()
        self.config[CONFIG_KEY_MODE] = MODE_WEBPAGE
        data_source = self.config.get(CONFIG_KEY_DATA_SOURCE) or {}
        self.config[CONFIG_KEY_DATA_SOURCE] = data_source
        data_source["Default"] = source
        ConfigUtil().save(self.config)
        server = self._server()
        if server is not None:
            server.webpage(source, monitor)
        self._refresh_all()
        if "://" in source and getattr(self, "web_hero_title", None) is not None:
            self._update_web_hero(source, "")
        target = "all monitors" if not monitor else monitor
        self._toast(f"Web page on {target}")

    def on_webpage_choose_file(self, *_args):
        dialog = Gtk.FileDialog()
        dialog.set_title(_("Choose a local HTML web page"))
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
        self._set_webpage_file(path)

    def _set_webpage_source(self, source):
        logger.info(f"[GUI4] Webpage: {source}")
        self._record_web_use(source)
        self._reload_web_lists()
        self._show_webpage_monitor_chooser(source)

    def _show_error(self, message):
        dialog = Adw.AlertDialog(heading=_("Error"), body=message)
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.connect("response", lambda *_: dialog.close())
        dialog.present(self.window)

    def _settings_switch_row(self, parent, title, subtitle, active, sensitive=True):
        row = Adw.ActionRow()
        row.set_title(title)
        row.set_subtitle(subtitle)
        switch = Gtk.Switch()
        switch.set_valign(Gtk.Align.CENTER)
        switch.set_active(bool(active))
        switch.set_sensitive(sensitive)
        parent.add(row)
        row.add_suffix(switch)
        return row, switch

    def _settings_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        box.set_margin_top(24)
        box.set_margin_bottom(24)
        box.set_margin_start(24)
        box.set_margin_end(24)
        config = getattr(self, "config", None) or {}
        behaviour = Adw.PreferencesGroup()
        behaviour.set_title(_("Behaviour"))
        self.static_row, self.static_switch = self._settings_switch_row(
            behaviour,
            _("Static wallpaper"),
            _("Keep the last video frame as wallpaper"),
            bool(config.get(CONFIG_KEY_STATIC_WALLPAPER, True)),
            sensitive=is_gnome(),
        )
        if not is_gnome():
            self.static_row.set_subtitle(_("Only on GNOME"))
        self.static_switch.connect("state-set", self._on_settings_flag, CONFIG_KEY_STATIC_WALLPAPER)
        wayland = is_wayland()
        self.pause_max_row, self.pause_max_switch = self._settings_switch_row(
            behaviour,
            _("Pause when maximized"),
            _("Pause playback when a window is maximized"),
            bool(config.get(CONFIG_KEY_PAUSE_WHEN_MAXIMIZED, True)),
            sensitive=not wayland,
        )
        if wayland:
            self.pause_max_row.set_subtitle(_("Requires X11"))
        self.pause_max_switch.connect(
            "state-set", self._on_settings_flag, CONFIG_KEY_PAUSE_WHEN_MAXIMIZED
        )
        self.mute_max_row, self.mute_max_switch = self._settings_switch_row(
            behaviour,
            _("Mute when maximized"),
            _("Mute audio when a window is maximized"),
            bool(config.get(CONFIG_KEY_MUTE_WHEN_MAXIMIZED, False)),
            sensitive=not wayland,
        )
        if wayland:
            self.mute_max_row.set_subtitle(_("Requires X11"))
        self.mute_max_switch.connect(
            "state-set", self._on_settings_flag, CONFIG_KEY_MUTE_WHEN_MAXIMIZED
        )
        box.append(behaviour)
        if is_gnome() and is_wayland():
            icons = Adw.PreferencesGroup()
            icons.set_title(_("Desktop icons"))
            self.integ_row = Adw.ActionRow()
            self.integ_row.set_title(_("Keep icons above video"))
            self.integ_switch = Gtk.Switch()
            self.integ_switch.set_valign(Gtk.Align.CENTER)
            self.integ_switch.connect("state-set", self._on_integration_toggled)
            self.integ_row.add_suffix(self.integ_switch)
            icons.add(self.integ_row)
            self._host_cmd = None
            self.prep_row = Adw.ActionRow()
            self.prep_row.set_title(_("Prepare host installation"))
            self.prep_row.set_subtitle(
                _("Export the extension files, then run one command on the host")
            )
            self.prep_btn = Gtk.Button(label=_("Prepare"))
            self.prep_btn.set_valign(Gtk.Align.CENTER)
            self.prep_btn.connect("clicked", self._on_prepare_host_install)
            self.prep_row.add_suffix(self.prep_btn)
            icons.add(self.prep_row)
            self.cmd_row = Adw.ActionRow()
            self.cmd_row.set_title(_("Run on the host"))
            self.cmd_row.set_subtitle(_("Log out and back in afterwards"))
            self.cmd_row.set_subtitle_selectable(True)
            self.copy_btn = Gtk.Button(label=_("Copy"))
            self.copy_btn.set_valign(Gtk.Align.CENTER)
            self.copy_btn.connect("clicked", self._on_copy_host_command)
            self.cmd_row.add_suffix(self.copy_btn)
            self.check_btn = Gtk.Button(label=_("Check again"))
            self.check_btn.set_valign(Gtk.Align.CENTER)
            self.check_btn.connect("clicked", self._on_check_integration)
            self.cmd_row.add_suffix(self.check_btn)
            icons.add(self.cmd_row)
            box.append(icons)
            self._refresh_integration_row()
        startup = Adw.PreferencesGroup()
        startup.set_title(_("Startup"))
        self.autostart_row, self.autostart_switch = self._settings_switch_row(
            startup,
            _("Start wallpaper at login"),
            _("Start only the wallpaper service at login"),
            self._autostart_enabled(),
        )
        self._update_autostart_subtitle(self.autostart_switch.get_active())
        self.autostart_switch.connect("state-set", self._on_autostart_toggled)
        box.append(startup)
        system = Adw.PreferencesGroup()
        system.set_title(_("System"))
        config_row = Adw.ActionRow()
        config_row.set_title(_("Open configuration"))
        config_row.set_subtitle(self._short_config_path())
        config_btn = Gtk.Button(label=_("Open"))
        config_btn.set_valign(Gtk.Align.CENTER)
        config_btn.connect("clicked", self._on_open_config)
        config_row.add_suffix(config_btn)
        system.add(config_row)
        help_row = Adw.ActionRow()
        help_row.set_title(_("How to select videos"))
        help_row.set_subtitle(_("Selection shortcuts for the library"))
        help_btn = Gtk.Button(label=_("Show"))
        help_btn.set_valign(Gtk.Align.CENTER)
        help_btn.connect("clicked", self._on_selection_help)
        help_row.add_suffix(help_btn)
        system.add(help_row)
        box.append(system)
        info = Adw.PreferencesGroup()
        info.set_title(_("Info"))
        about_row = Adw.ActionRow()
        about_row.set_title(_("About Apricus"))
        about_row.set_subtitle(
            _("Version {version} · GPL-3.0-or-later").format(version=self.version)
        )
        about_btn = Gtk.Button(label=_("Open"))
        about_btn.set_valign(Gtk.Align.CENTER)
        about_btn.connect("clicked", self._show_about)
        about_row.add_suffix(about_btn)
        info.add(about_row)
        box.append(info)
        return self._scroll_page(self._clamp_page(box))

    def _refresh_integration_row(self):
        """Show the GNOME Shell integration state (never raises)."""
        row = getattr(self, "integ_row", None)
        switch = getattr(self, "integ_switch", None)
        if row is None:
            return
        try:
            from apricus.gnome_shell import EXTENSION_UUID, integration_status

            state, detail = integration_status()
        except Exception:  # noqa: BLE001 - status must never break settings
            from apricus.gnome_shell import EXTENSION_UUID

            state, detail = "unknown", {}
        switch_on, sensitive = False, False
        if state == "active":
            subtitle = _("Active — icons stay above the video")
            switch_on, sensitive = True, True
        elif state == "disabled":
            if is_snap() or is_flatpak():
                subtitle = (
                    _("Installed but disabled — enable it manually: gnome-extensions enable")
                    + f" {EXTENSION_UUID}"
                )
            else:
                subtitle = _("Installed but disabled — turn on to keep icons above video")
                switch_on, sensitive = False, True
        elif state == "needs_logout":
            subtitle = _("Enabled — log out and back in to activate it")
            switch_on, sensitive = True, True
        elif state == "not_discovered":
            subtitle = _("Installed — log out and back in so GNOME finds it")
            switch_on, sensitive = False, True
        elif state == "not_installed":
            subtitle = _("Not installed — native packages include it; see README")
        elif state == "incompatible":
            supported = ", ".join(detail.get("supported") or []) or "?"
            subtitle = _("Unsupported GNOME {shell} (supports {supported})").format(
                shell=detail.get("shell") or "?", supported=supported
            )
        elif state == "error":
            subtitle = _("Failed to load — check the journal for errors")
        else:
            subtitle = _("Status unavailable")
        row.set_subtitle(subtitle)
        if switch is not None:
            switch.set_active(switch_on)
            switch.set_sensitive(sensitive)
        self._refresh_host_install_rows(state)

    def _refresh_host_install_rows(self, state):
        """Show the guided host install only where the sandbox needs it."""
        prep = getattr(self, "prep_row", None)
        cmd_row = getattr(self, "cmd_row", None)
        if prep is None or cmd_row is None:
            return
        show = state == "not_installed" and (is_snap() or is_flatpak())
        prep.set_visible(show)
        cmd = getattr(self, "_host_cmd", None)
        cmd_row.set_visible(bool(show and cmd))
        if cmd:
            cmd_row.set_subtitle(cmd)

    def _on_prepare_host_install(self, _button):
        """Export ZIP + installer for the host (never executes anything)."""
        try:
            from apricus.gnome_shell import prepare_host_install

            _export_dir, cmd = prepare_host_install()
        except Exception as e:  # noqa: BLE001 - show the failure inline
            self._toast(_("Could not prepare installation files ({err})").format(err=e))
            return
        self._host_cmd = cmd
        self._refresh_integration_row()
        self._toast(_("Files ready — run the command on the host"))

    def _on_copy_host_command(self, _button):
        cmd = getattr(self, "_host_cmd", None)
        if not cmd:
            return
        try:
            display = Gdk.Display.get_default()
            if display is None:
                raise RuntimeError("no display")
            display.get_clipboard().set_text(cmd)
        except Exception as e:  # noqa: BLE001 - clipboard best effort
            self._toast(_("Could not copy ({err})").format(err=e))
            return
        self._toast(_("Command copied"))

    def _on_check_integration(self, _button):
        """Re-read the real extension status (e.g. after a host install)."""
        self._host_cmd = getattr(self, "_host_cmd", None)
        self._refresh_integration_row()

    def _on_integration_toggled(self, switch, _state):
        """Enable/disable the Shell extension on explicit user action."""
        want = bool(switch.get_active())
        try:
            from apricus.gnome_shell import EXTENSION_UUID
            from apricus.utils import gnome_extension_set_disable, gnome_extension_set_enable

            if want:
                confirmed = gnome_extension_set_enable(EXTENSION_UUID)
            else:
                confirmed = gnome_extension_set_disable(EXTENSION_UUID)
            if not confirmed:
                # No exception, but the Shell did not confirm: fall through
                # to the manual fallback instead of claiming success.
                raise RuntimeError("Shell did not confirm")
            if not want:
                self._toast(_("Integration disabled — using basic stacking"))
        except Exception as e:  # noqa: BLE001 - show the manual fallback
            row = getattr(self, "integ_row", None)
            if row is not None:
                action = "enable" if want else "disable"
                row.set_subtitle(
                    (
                        _(
                            "Could not {action} automatically ({err}); run: gnome-extensions {action}"
                        ).format(action=action, err=e)
                    )
                    + f" {EXTENSION_UUID}"
                )
            switch.set_active(not want)
            return False
        self._refresh_integration_row()
        return False

    @staticmethod
    def _short_config_path():
        home = os.path.expanduser("~")
        return CONFIG_PATH.replace(home, "~")

    @staticmethod
    def _autostart_enabled():
        try:
            return os.path.isfile(AUTOSTART_DESKTOP_PATH)
        except OSError:
            return False

    def _update_autostart_subtitle(self, enabled):
        row = getattr(self, "autostart_row", None)
        if row is None:
            return
        row.set_subtitle(
            _("Wallpaper service starts at login")
            if enabled
            else _("Starts only the wallpaper service at login")
        )

    def _on_autostart_toggled(self, switch, *args):
        if getattr(self, "_autostart_busy", False):
            return False
        want = bool(switch.get_active())
        self._autostart_busy = True
        try:
            setup_autostart(want)
        except Exception as e:  # noqa: BLE001 - report, then roll back
            logger.warning(f"[GUI4] Autostart change failed: {e}")
            switch.set_active(not want)
            self._show_error(_("Could not change the startup setting."))
            return False
        finally:
            self._autostart_busy = False
        actual = self._autostart_enabled()
        if actual != want:
            self._autostart_busy = True
            try:
                switch.set_active(actual)
            finally:
                self._autostart_busy = False
            self._show_error(_("Could not change the startup setting."))
            return False
        self._update_autostart_subtitle(actual)
        self._toast(_("Startup enabled") if actual else _("Startup disabled"))
        return False

    _SETTINGS_FLAG_ATTRS = {
        CONFIG_KEY_STATIC_WALLPAPER: "is_static_wallpaper",
        CONFIG_KEY_PAUSE_WHEN_MAXIMIZED: "is_pause_when_maximized",
        CONFIG_KEY_MUTE_WHEN_MAXIMIZED: "is_mute_when_maximized",
    }

    def _on_settings_flag(self, switch, _state, key):
        if getattr(self, "_syncing_flags", False):
            return False
        want = bool(switch.get_active())
        server = self._server()
        if server is not None:
            try:
                setattr(server, self._SETTINGS_FLAG_ATTRS[key], want)
            except Exception as e:  # noqa: BLE001 - keep config, report
                logger.warning(f"[GUI4] Server flag update failed: {e}")
        self.config = ConfigUtil().load()
        self.config[key] = want
        ConfigUtil().save(self.config)
        return False

    def _on_open_config(self, *_args):
        try:
            Gio.AppInfo.launch_default_for_uri(GLib.filename_to_uri(CONFIG_PATH))
        except GLib.Error as e:
            logger.warning(f"[GUI4] Could not open config: {e.message}")
            self._show_error(_("Could not open the configuration file."))

    def _on_selection_help(self, *_args):
        dialog = Adw.AlertDialog(
            heading=_("How to select videos"),
            body=(
                _(
                    "Click to select a video.\n"
                    "Ctrl+click or Shift+click selects more than one.\n"
                    "Apply lets you choose the monitor."
                )
            ),
        )
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.connect("response", lambda *_: dialog.close())
        dialog.present(self.window)

    # -- refresh + handlers ---------------------------------------------

    def _refresh_all(self):
        server = self._server()
        mode = server.mode if server is not None else self.config.get(CONFIG_KEY_MODE)
        paused = True
        if server is not None and mode in (MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST):
            try:
                paused = bool(server.is_paused_by_user)
            except Exception:  # noqa: BLE001
                paused = True
        paths = get_video_paths(resolve_video_dir(self.config))
        self._refresh_source_row(len(paths))
        self._reload_library_store(paths)
        self._reload_queue_store()
        self._refresh_playlist_status(server=server, mode=mode)
        self._refresh_now_playing(server=server, mode=mode, paused=paused)
        self.shuffle_check.set_active(bool(self.config.get("playlist_shuffle")))
        playlist = (self.config.get("playlist") or {}).get("Default", [])
        self.playlist_status.set_text(
            f"{len(playlist)} videos in queue"
            + (" (muted by design)" if mode == MODE_PLAYLIST else "")
        )
        self._update_queue_actions()
        self._refresh_web_details()
        banner = getattr(self, "server_banner", None)
        if banner is not None:
            banner.set_revealed(server is None)
        p_err = self._playback_error(server)
        p_banner = getattr(self, "playback_banner", None)
        if p_banner is not None:
            if p_err:
                p_banner.set_title(_(p_err))
            p_banner.set_revealed(bool(p_err and server is not None))
        self._refresh_sidebar_status(server, mode, paused=paused)

    @staticmethod
    def _playback_error(server):
        if server is None:
            return None
        try:
            err = getattr(server, "playback_error", "") or ""
        except Exception:  # noqa: BLE001 - D-Bus failure must not break refresh
            return None
        return err or None

    def _on_retry_playback(self, *_args):
        server = self._server()
        if server is not None:
            try:
                server.reload()
            except GLib.Error:
                pass
        self._refresh_all()

    def _reload_library_store(self, paths=None):
        from apricus.gui.gtk4lib import VideoItem

        if paths is None:
            paths = get_video_paths(resolve_video_dir(self.config))
        force = getattr(self, "_force_lib_reload", False)
        self._force_lib_reload = False
        if not force and self.video_store is not None:
            n = self.video_store.get_n_items()
            if n == len(paths) and all(
                self.video_store.get_item(i) is not None
                and self.video_store.get_item(i).path == paths[i]
                for i in range(n)
            ):
                return
        items = [VideoItem(p) for p in paths]
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
    # Set the program name before any Gtk init: under X11 GDK derives the
    # window WM_CLASS from it, and the desktop file matches windows to the
    # launcher via StartupWMClass. Without this the class is "python" and
    # GNOME docks the running panel under a separate generic gear icon.
    GLib.set_prgname(APP_ID)
    setup_logging()
    init_translations(localedir)
    Adw.init()
    app = ControlPanel(version)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
