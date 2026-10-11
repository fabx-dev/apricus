import logging
import multiprocessing as mp
import os
import subprocess
import sys
import threading
from gettext import gettext as _

# TODO: Port to Gtk4/adwaita someday...
import gi
import requests
import setproctitle

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import yt_dlp
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
from pydbus import SessionBus

from apricus.commons import (
    AUTOSTART_DESKTOP_PATH,
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_FIRST_TIME,
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED,
    CONFIG_KEY_PLAYLIST,
    CONFIG_KEY_SHUFFLE,
    CONFIG_KEY_STATIC_WALLPAPER,
    CONFIG_KEY_VIDEO_DIR,
    CONFIG_KEY_VOLUME,
    CONFIG_PATH,
    DBUS_NAME_SERVER,
    DBUS_PROJECT,
    LOGGER_NAME,
    MODE_PLAYLIST,
    MODE_STREAM,
    MODE_VIDEO,
    MODE_WEBPAGE,
    TRANSLATION_DOMAIN,
    resolve_video_dir,
)
from apricus.gui.gui_utils import debounce, get_thumbnail
from apricus.monitor import Monitors
from apricus.utils import (
    ConfigUtil,
    get_video_paths,
    init_translations,
    is_gnome,
    is_wayland,
    setup_autostart,
    setup_logging,
)

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(LOGGER_NAME)

APP_ID = f"{DBUS_PROJECT}.gui"
APP_TITLE = "Apricus"
APP_UI_RESOURCE_PATH = "/io/github/fabx_dev/Apricus/control.ui"


class ControlPanel(Gtk.Application):
    def __init__(self, version, *args, **kwargs):
        super().__init__(
            *args,
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
            **kwargs,
        )
        setproctitle.setproctitle(mp.current_process().name)
        # Builder init
        self.builder = Gtk.Builder()
        self.builder.set_application(self)
        self.builder.set_translation_domain(TRANSLATION_DOMAIN)
        self.builder.add_from_resource(APP_UI_RESOURCE_PATH)
        # Handlers declared in `control.ui``
        signals = {
            "on_volume_changed": self.on_volume_changed,
            "on_streaming_activate": self.on_streaming_activate,
            "on_web_page_activate": self.on_web_page_activate,
        }
        self.builder.connect_signals(signals)

        # Variables init
        self.version = version
        self.window = None
        self.server = None
        self.icon_view = None
        self.video_paths = None
        self.playlist_paths = []
        self.all_key = "all"

        self.is_autostart = os.path.isfile(AUTOSTART_DESKTOP_PATH)

        self._connect_server()
        self._load_config()

        # initialize monitors
        self.monitors = Monitors()
        # get video paths
        video_paths = self.config[CONFIG_KEY_DATA_SOURCE]
        for key in self.monitors.get_monitors():
            # check if monitor exists in paths
            if key in video_paths and len(video_paths[key]) != 0:
                self.monitors.get_monitor(key).set_wallpaper(video_paths[key])
            else:
                self.monitors.get_monitor(key).set_wallpaper(video_paths["Default"])

        self._setup_context_menu()  # setup context menu for selecting monitors

    def _connect_server(self):
        try:
            self.server = SessionBus().get(DBUS_NAME_SERVER)
        except GLib.Error:
            logger.error("[GUI] Couldn't connect to server")

    def _setup_context_menu(self):
        self.contextMenu_monitors = Gtk.Menu()
        self.contextMenu_monitors.show_all()

        for monitor in self.monitors.get_monitors().values():
            item = Gtk.MenuItem(label=_("Set For {monitor}").format(monitor=monitor.name))
            item.connect("activate", self.on_set_as, monitor)
            self.contextMenu_monitors.append(item)

        # add all option
        item = Gtk.MenuItem(label=_("Set For All"))
        item.connect("activate", self.on_set_as, self.all_key)
        self.contextMenu_monitors.append(item)

    def _load_config(self):
        self.config = ConfigUtil().load()
        playlist = self.config.get(CONFIG_KEY_PLAYLIST) or {}
        self.playlist_paths = list(playlist.get("Default", []))

    def _save_config(self):
        ConfigUtil().save(self.config)

    @debounce(1)
    def _save_config_delay(self):
        self._save_config()

    def do_startup(self):
        Gtk.Application.do_startup(self)

        actions = [
            ("local_video_dir", self.on_open_video_dir),
            ("local_video_choose_dir", self.on_choose_video_dir),
            ("local_video_refresh", self._reload_icon_view),
            ("local_video_apply", self.on_local_video_apply),
            ("local_web_page_apply", self.on_local_web_page_apply),
            ("playlist_add_selected", self.on_playlist_add_selected),
            ("playlist_add_all", self.on_playlist_add_all),
            ("playlist_up", self.on_playlist_up),
            ("playlist_down", self.on_playlist_down),
            ("playlist_remove", self.on_playlist_remove),
            ("playlist_clear", self.on_playlist_clear),
            ("playlist_apply", self.on_playlist_apply),
            ("play_pause", self.on_play_pause),
            ("feeling_lucky", self.on_feeling_lucky),
            (
                "config",
                lambda *_: subprocess.run(["xdg-open", os.path.realpath(CONFIG_PATH)]),
            ),
            ("about", self.on_about),
            ("quit", self.on_quit),
        ]

        for action_name, handler in actions:
            action = Gio.SimpleAction.new(action_name, None)
            action.connect("activate", handler)
            self.add_action(action)

        statefuls = [
            ("mute", self.config[CONFIG_KEY_MUTE], self.on_mute),
            ("autostart", self.is_autostart, self.on_autostart),
            (
                "static_wallpaper",
                self.config[CONFIG_KEY_STATIC_WALLPAPER],
                self.on_static_wallpaper,
            ),
            (
                "pause_when_maximized",
                self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED],
                self.on_pause_when_maximized,
            ),
            (
                "mute_when_maximized",
                self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED],
                self.on_mute_when_maximized,
            ),
        ]

        for action_name, state, handler in statefuls:
            action = Gio.SimpleAction.new_stateful(
                action_name, None, GLib.Variant.new_boolean(state)
            )
            action.connect("change-state", handler)
            self.add_action(action)

        if is_wayland():
            self.builder.get_object("TogglePauseWhenMaximized").set_visible(False)
            self.builder.get_object("ToggleMuteWhenMaximized").set_visible(False)

        if not is_gnome():
            # Disable static wallpaper functionality for non-GNOME DE
            self.builder.get_object("ToggleStaticWallpaper").set_visible(False)

        check_shuffle = self.builder.get_object("CheckShuffle")
        if check_shuffle is not None:
            check_shuffle.connect("toggled", self.on_shuffle_toggled)

        self._reload_all_widgets()

    def do_activate(self):
        if self.window is None:
            self.window: Gtk.ApplicationWindow = self.builder.get_object("ApplicationWindow")
            self.window.set_title("Apricus")
            self.window.set_application(self)
            self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.present()

        if self.server is None:
            self._show_error(_("Couldn't connect to server"))

        if self.config[CONFIG_KEY_FIRST_TIME]:
            self._show_welcome()
            self.config[CONFIG_KEY_FIRST_TIME] = False
            self._save_config()

    def _show_welcome(self):
        # Welcome dialog
        dialog = Gtk.MessageDialog(
            parent=self.window,
            modal=True,
            destroy_with_parent=True,
            text=_("Welcome to Apricus 🤗"),
            message_type=Gtk.MessageType.INFO,
            #    secondary_text="You can bring up the Menu by <b>Right click</b> on the desktop",
            secondary_text=_(
                "Quickstart for adding local videos:\n"
                " ・Click the folder icon to open your video folder\n"
                " ・Put your videos there\n"
                " ・Click the refresh button"
            ),
            secondary_use_markup=True,
            buttons=Gtk.ButtonsType.OK,
        )
        dialog.run()
        dialog.destroy()

    def _show_error(self, error):
        dialog = Gtk.MessageDialog(
            parent=self.window,
            modal=True,
            destroy_with_parent=True,
            text=_("Oops!"),
            message_type=Gtk.MessageType.ERROR,
            secondary_text=error,
            buttons=Gtk.ButtonsType.OK,
        )
        dialog.run()
        dialog.destroy()

    def on_open_video_dir(self, *_args):
        video_dir = resolve_video_dir(self.config)
        if video_dir is None:
            self.on_choose_video_dir()
            return
        subprocess.run(["xdg-open", os.path.realpath(video_dir)])

    def on_choose_video_dir(self, *_args):
        current = resolve_video_dir(self.config)
        dialog = Gtk.FileChooserDialog(
            title=_("Choose video folder (current: {folder})").format(
                folder=current or _("none chosen yet")
            ),
            parent=self.window,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dialog.add_buttons(
            _("Cancel"),
            Gtk.ResponseType.CANCEL,
            _("Choose"),
            Gtk.ResponseType.OK,
        )
        dialog.set_current_folder(current or os.path.expanduser("~"))
        if dialog.run() == Gtk.ResponseType.OK:
            chosen = dialog.get_filename()
            logger.info(f"[GUI] Video folder: {chosen}")
            self.config[CONFIG_KEY_VIDEO_DIR] = chosen
            self._save_config()
            self._reload_icon_view()
        dialog.destroy()

    def on_local_video_apply(self, *_args):
        selected = self.icon_view.get_selected_items()
        if len(selected) != 0:
            # show menu
            self.contextMenu_monitors.show_all()
            self.contextMenu_monitors.popup(None, None, None, None, 0, Gtk.get_current_event_time())
        else:
            dialog = Gtk.MessageDialog(
                parent=self.window,
                modal=True,
                destroy_with_parent=True,
                text=_("No Video Selected"),
                message_type=Gtk.MessageType.INFO,
                secondary_text=_("There are no video selected.\nPlease choose one first."),
                secondary_use_markup=True,
                buttons=Gtk.ButtonsType.OK,
            )
            dialog.run()
            dialog.destroy()

    def on_set_as(self, widget, monitor):
        index = self.icon_view.get_selected_items()[0].get_indices()[0]
        video_path = self.video_paths[index]
        logger.info(f"[GUI] Local Video Set To {video_path} For Monitor {monitor}")
        self.config[CONFIG_KEY_MODE] = MODE_VIDEO
        paths = self.config[CONFIG_KEY_DATA_SOURCE] if not None else []
        # all option
        if monitor == self.all_key:
            for key, mon in self.monitors.get_monitors().items():
                paths[key] = video_path
                mon.set_wallpaper(video_path)
            server_monitor = None
        else:
            paths[monitor.key] = video_path
            self.monitors.get_monitor(monitor.key).set_wallpaper(video_path)
            server_monitor = monitor.key

        # also update the Default video
        paths["Default"] = video_path
        self.config[CONFIG_KEY_DATA_SOURCE] = paths
        self._save_config()
        if self.server is not None:
            # monitor=None: player reloads the full saved config (single restart)
            self.server.video(video_path, server_monitor)

    def on_local_web_page_apply(self, *_args):
        file_chooser: Gtk.FileChooserButton = self.builder.get_object("FileChooser")
        choose: Gio.File = file_chooser.get_file()
        if choose is None:
            self._show_error(_("Please choose a HTML file"))
            return
        file_path = choose.get_path()
        logger.info(f"[GUI] Local Webpage: {file_path}")
        self.config[CONFIG_KEY_MODE] = MODE_WEBPAGE
        self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = (
            file_path  #! we dont want to break the config, webpage and stream modes will kept in Default source
        )
        self._save_config()
        if self.server is not None:
            self.server.webpage(choose.get_path())

    def on_play_pause(self, *_args):
        if self.server is None:
            return
        prev_state = self.server.is_paused_by_user
        self.server.is_paused_by_user = not prev_state
        if not prev_state:
            self.server.pause_playback()
        else:
            self.server.start_playback()

    def on_feeling_lucky(self, *_args):
        if self.server is not None:
            self.server.feeling_lucky()

    def _refresh_playlist_view(self):
        view = self.builder.get_object("PlaylistView")
        if view is None:
            return
        if not view.get_columns():
            for i, title in enumerate([_("#"), _("Video")]):
                renderer = Gtk.CellRendererText()
                column = Gtk.TreeViewColumn(title, renderer, text=i)
                view.append_column(column)
        store = Gtk.ListStore(int, str)
        for pos, path in enumerate(self.playlist_paths, start=1):
            store.append([pos, os.path.basename(path)])
        view.set_model(store)

    def on_playlist_add_selected(self, *_args):
        selected = self.icon_view.get_selected_items()
        if not selected:
            return
        for item in selected:
            path = self.video_paths[item.get_indices()[0]]
            if path not in self.playlist_paths:
                self.playlist_paths.append(path)
        self._refresh_playlist_view()

    def on_playlist_add_all(self, *_args):
        for path in self.video_paths or []:
            if path not in self.playlist_paths:
                self.playlist_paths.append(path)
        self._refresh_playlist_view()

    def _playlist_selected_index(self):
        view = self.builder.get_object("PlaylistView")
        selection = view.get_selection()
        _model, tree_iter = selection.get_selected()
        if tree_iter is None:
            return None
        return _model.get_path(tree_iter).get_indices()[0]

    def on_playlist_up(self, *_args):
        idx = self._playlist_selected_index()
        if idx is not None and idx > 0:
            self.playlist_paths[idx - 1], self.playlist_paths[idx] = (
                self.playlist_paths[idx],
                self.playlist_paths[idx - 1],
            )
            self._refresh_playlist_view()
            self.builder.get_object("PlaylistView").get_selection().select_path(idx - 1)

    def on_playlist_down(self, *_args):
        idx = self._playlist_selected_index()
        if idx is not None and idx < len(self.playlist_paths) - 1:
            self.playlist_paths[idx + 1], self.playlist_paths[idx] = (
                self.playlist_paths[idx],
                self.playlist_paths[idx + 1],
            )
            self._refresh_playlist_view()
            self.builder.get_object("PlaylistView").get_selection().select_path(idx + 1)

    def on_playlist_remove(self, *_args):
        idx = self._playlist_selected_index()
        if idx is not None:
            del self.playlist_paths[idx]
            self._refresh_playlist_view()

    def on_playlist_clear(self, *_args):
        self.playlist_paths = []
        self._refresh_playlist_view()

    def on_playlist_apply(self, *_args):
        if not self.playlist_paths:
            return
        logger.info(f"[GUI] Playlist apply ({len(self.playlist_paths)} videos)")
        self.config[CONFIG_KEY_MODE] = MODE_PLAYLIST
        self._save_config()
        if self.server is not None:
            self.server.playlist(self.playlist_paths, "all")
        self._reload_all_widgets()

    def on_shuffle_toggled(self, check):
        self.config[CONFIG_KEY_SHUFFLE] = bool(check.get_active())
        logger.info(f"[GUI] Shuffle: {self.config[CONFIG_KEY_SHUFFLE]}")
        self._save_config()
        if self.server is not None and self.config[CONFIG_KEY_MODE] == MODE_PLAYLIST:
            self.server.reload()

    def set_mute_toggle_icon(self):
        toggle_icon: Gtk.Image = self.builder.get_object("ToggleMuteIcon")
        volume, is_mute = self.config[CONFIG_KEY_VOLUME], self.config[CONFIG_KEY_MUTE]
        if volume == 0 or is_mute:
            icon_name = "audio-volume-muted-symbolic"
        elif volume < 30:
            icon_name = "audio-volume-low-symbolic"
        elif volume < 60:
            icon_name = "audio-volume-medium-symbolic"
        else:
            icon_name = "audio-volume-high-symbolic"
        toggle_icon.set_from_icon_name(icon_name=icon_name, size=0)

    def _apply_playlist_audio_gate(self):
        """Playlist players have no audio backend: grey out volume/mute."""
        is_playlist = self.config[CONFIG_KEY_MODE] == MODE_PLAYLIST
        scale = self.builder.get_object("ScaleVolume")
        toggle = self.builder.get_object("ToggleMute")
        if scale is not None:
            scale.set_sensitive(not is_playlist and not self.config[CONFIG_KEY_MUTE])
            scale.set_tooltip_text(
                _("Disabled in playlist mode (muted by design)")
                if is_playlist
                else _("Volume control")
            )
        if toggle is not None:
            toggle.set_sensitive(not is_playlist)
        mute_action = self.lookup_action("mute")
        if mute_action is not None:
            mute_action.set_enabled(not is_playlist)

    def _select_tab_for_mode(self):
        stack = self.builder.get_object("stack1")
        if stack is None:
            return
        mapping = {
            MODE_VIDEO: "video",
            MODE_STREAM: "streaming",
            MODE_WEBPAGE: "webpage",
            MODE_PLAYLIST: "playlist",
        }
        child = mapping.get(self.config[CONFIG_KEY_MODE])
        if child is not None:
            stack.set_visible_child_name(child)

    def set_scale_volume_sensitive(self):
        scale = self.builder.get_object("ScaleVolume")
        if self.config[CONFIG_KEY_MUTE]:
            scale.set_sensitive(False)
        else:
            scale.set_sensitive(True)

    def on_volume_changed(self, adjustment):
        self.config[CONFIG_KEY_VOLUME] = int(adjustment.get_value())
        logger.info(f"[GUI] Volume: {self.config[CONFIG_KEY_VOLUME]}")
        self._save_config_delay()
        if self.server is not None:
            self.server.volume = self.config[CONFIG_KEY_VOLUME]
        self.set_mute_toggle_icon()

    def on_mute(self, action, state):
        action.set_state(state)
        self.config[CONFIG_KEY_MUTE] = bool(state)
        logger.info(f"[GUI] {action.get_name()}: {state}")
        self._save_config()
        if self.server is not None:
            self.server.is_mute = self.config[CONFIG_KEY_MUTE]
        self.set_mute_toggle_icon()
        self.set_scale_volume_sensitive()

    def on_autostart(self, action, state):
        action.set_state(state)
        self.is_autostart = bool(state)
        logger.info(f"[GUI] {action.get_name()}: {state}")
        setup_autostart(state)

    def on_static_wallpaper(self, action, state):
        action.set_state(state)
        self.config[CONFIG_KEY_STATIC_WALLPAPER] = bool(state)
        logger.info(f"[GUI] {action.get_name()}: {state}")
        self._save_config()
        if self.server is not None:
            self.server.is_static_wallpaper = self.config[CONFIG_KEY_STATIC_WALLPAPER]

    def on_pause_when_maximized(self, action, state):
        action.set_state(state)
        self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED] = bool(state)
        logger.info(f"[GUI] {action.get_name()}: {state}")
        self._save_config()
        if self.server is not None:
            self.server.is_pause_when_maximized = self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED]

    def on_mute_when_maximized(self, action, state):
        action.set_state(state)
        self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED] = bool(state)
        logger.info(f"[GUI] {action.get_name()}: {state}")
        self._save_config()
        if self.server is not None:
            self.server.is_mute_when_maximized = self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]

    def on_about(self, *_args):
        self.builder.add_from_resource(APP_UI_RESOURCE_PATH)
        about_dialog: Gtk.AboutDialog = self.builder.get_object("AboutDialog")
        about_dialog.set_transient_for(self.window)
        about_dialog.set_version(self.version)
        about_dialog.set_modal(True)
        about_dialog.present()

    def _check_url(self, url):
        # Check if the url is valid
        try:
            response = requests.get(url)
        except requests.exceptions.RequestException as e:
            logger.error(f"[GUI] Failed to access {url}. Error:\n{e}")
            self._show_error(_("Failed to access {url}. Error:\n{error}").format(url=url, error=e))
            return False
        if response.status_code >= 400:
            logger.error(f"[GUI] Failed to access {url}. Error code: {response.status_code}")
            self._show_error(
                _("Failed to access {url}. Error code: {code}").format(
                    url=url, code=response.status_code
                )
            )
            return False
        return True

    def _check_yt_dlp(self, raw_url):
        # Check if the url is valid (yt_dlp)
        try:
            with yt_dlp.YoutubeDL({"noplaylist": True}) as ydl:
                ydl.extract_info(raw_url, download=False)
        except yt_dlp.utils.DownloadError as e:
            s = " ".join(str(e).split(" ")[1:])
            logger.error(f"[GUI] Failed to stream {raw_url}. Error:\n{s}")
            self._show_error(
                _("Failed to stream {url}. Error:\n{error}").format(url=raw_url, error=s)
            )
            return False
        return True

    def on_streaming_activate(self, entry: Gtk.Entry, *_):
        url = entry.get_text()
        if not self._check_yt_dlp(url):
            return
        logger.info(f"[GUI] Streaming: {url}")
        self.config[CONFIG_KEY_MODE] = MODE_STREAM
        self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = (
            url  #! we dont want to break the config, webpage and stream modes will kept in Default source
        )
        self._save_config()
        if self.server is not None:
            self.server.stream(url)

    def on_web_page_activate(self, entry: Gtk.Entry, *_):
        url = entry.get_text()
        if not self._check_url(url):
            return
        logger.info(f"[GUI] Webpage: {url}")
        self.config[CONFIG_KEY_MODE] = MODE_WEBPAGE
        self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = (
            url  #! we dont want to break the config, webpage and stream modes will kept in Default source
        )
        self._save_config()
        if self.server is not None:
            self.server.webpage(url)

    def on_icon_view_button_press(self, widget, event):
        if event.button == Gdk.BUTTON_SECONDARY:  # Right click
            path_info = widget.get_path_at_pos(event.x, event.y)
            if path_info is not None:
                tree_path = Gtk.TreePath(path_info[0])
                self.icon_view.grab_focus()
                widget.select_path(tree_path)
                self.contextMenu_monitors.show_all()
                self.contextMenu_monitors.popup(
                    None, None, None, None, 0, Gtk.get_current_event_time()
                )
                return True
        return False

    def on_quit(self, *_args):
        if self.server is not None:
            try:
                self.server.quit()
            except GLib.Error:
                # Ignore NoReply error
                pass
        self.quit()

    def _reload_all_widgets(self):
        self._reload_icon_view()
        self._refresh_playlist_view()
        self.set_mute_toggle_icon()
        self.set_scale_volume_sensitive()
        self._apply_playlist_audio_gate()
        self._select_tab_for_mode()
        check_shuffle = self.builder.get_object("CheckShuffle")
        if check_shuffle is not None:
            check_shuffle.set_active(bool(self.config.get(CONFIG_KEY_SHUFFLE)))
        toggle_mute: Gtk.ToggleButton = self.builder.get_object("ToggleMute")
        toggle_mute.set_state = self.config[CONFIG_KEY_MUTE]

        scale_volume: Gtk.Scale = self.builder.get_object("ScaleVolume")
        adjustment_volume: Gtk.Adjustment = self.builder.get_object("AdjustmentVolume")
        # Temporary block signal
        adjustment_volume.handler_block_by_func(self.on_volume_changed)
        scale_volume.set_value(self.config[CONFIG_KEY_VOLUME])
        adjustment_volume.handler_unblock_by_func(self.on_volume_changed)

        toggle_mute: Gtk.ToggleButton = self.builder.get_object("ToggleAutostart")
        toggle_mute.set_state = self.is_autostart

    def _reload_icon_view(self, *_args):
        video_dir = resolve_video_dir(self.config)
        self.video_paths = get_video_paths(video_dir)
        label = self.builder.get_object("LabelVideoFolder")
        if label is not None:
            if video_dir is None:
                text = _("Choose a video folder to begin")
            elif self.video_paths:
                text = _("Videos in {folder} ({count})").format(
                    folder=os.path.basename(video_dir), count=len(self.video_paths)
                )
            else:
                text = _("No videos in {folder}").format(folder=os.path.basename(video_dir))
            label.set_markup(
                '<span size="x-large"><b>Local Video</b></span>\n' + GLib.markup_escape_text(text)
            )
            label.set_tooltip_text(video_dir or "")
        list_store = Gtk.ListStore(GdkPixbuf.Pixbuf, str)
        self.icon_view: Gtk.IconView = self.builder.get_object("IconView")
        self.icon_view.set_pixbuf_column(0)
        self.icon_view.set_text_column(1)
        self.icon_view.set_model(list_store)
        self.icon_view.connect("button-press-event", self.on_icon_view_button_press)
        for idx, video_path in enumerate(self.video_paths):
            pixbuf = Gtk.IconTheme().get_default().load_icon("video-x-generic", 96, 0)
            list_store.append([pixbuf, os.path.basename(video_path)])
            thread = threading.Thread(target=get_thumbnail, args=(video_path, list_store, idx))
            thread.daemon = True
            thread.start()


def _find_gresource(pkgdatadir):
    """Locate apricus.gresource: the launcher-provided prefix first, then the
    standard XDG data dirs (so `python -m apricus` works for any install)."""
    candidates = [os.path.join(pkgdatadir, "apricus.gresource")]
    candidates += [
        os.path.join(d, "apricus", "apricus.gresource")
        for d in (GLib.get_user_data_dir(), *GLib.get_system_data_dirs())
    ]
    return next((c for c in candidates if os.path.isfile(c)), None)


def main(version="devel", pkgdatadir="/usr/share/apricus", localedir="/usr/share/locale"):
    setup_logging()
    init_translations(localedir)
    gresource = _find_gresource(pkgdatadir)
    if gresource is None:
        logger.error("[GUI] Couldn't find apricus.gresource. Is Apricus installed?")
        return
    Gio.Resource.load(gresource)._register()
    Gtk.IconTheme.get_default().add_resource_path("/io/github/fabx_dev/Apricus/icons")

    app = ControlPanel(version)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
