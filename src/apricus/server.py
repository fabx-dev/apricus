import logging
import multiprocessing as mp
import os
import random
import signal
import time
from multiprocessing import Process

import setproctitle
from gi.repository import GLib
from pydbus import SessionBus

from apricus import perf
from apricus.commons import (
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED,
    CONFIG_KEY_PLAYLIST,
    CONFIG_KEY_STATIC_WALLPAPER,
    CONFIG_KEY_SYSTRAY,
    CONFIG_KEY_VOLUME,
    DBUS_NAME_PLAYER,
    DBUS_NAME_SERVER,
    LOGGER_NAME,
    MODE_NULL,
    MODE_PLAYLIST,
    MODE_STREAM,
    MODE_VIDEO,
    MODE_WEBPAGE,
    resolve_video_dir,
)
from apricus.monitor import Monitors
from apricus.utils import ConfigUtil, EndSessionHandler, _authorize, get_video_paths, is_snap

# NOTE: menu and the player mains pin Gtk 3 namespaces at import time
# (gi forbids two Gtk majors in one process). They must never be imported
# at server top-level: the forkserver image inherits the parent's loaded
# namespaces, so any Gtk 3 import here would break GTK4 children even
# though show_gui() imports gtk4app lazily. Each target below imports its
# real main *inside* the child process instead.


def _gui_gtk4_target(version, pkgdatadir, localedir):
    from apricus.gui.gtk4app import main as gui_target

    gui_target(version, pkgdatadir, localedir)


def _video_player_target():
    from apricus.player.video_player import main as video_player_main

    video_player_main()


def _web_player_target():
    from apricus.player.web_player import main as web_player_main

    web_player_main()


def _systray_target(mode, localedir):
    from apricus.menu import show_systray_icon

    show_systray_icon(mode, localedir)


loop = GLib.MainLoop()
logger = logging.getLogger(LOGGER_NAME)


class ApricusServer:
    """
    <node>
    <interface name='io.github.fabx_dev.apricus.server'>
        <method name='null'/>
        <method name='video'>
            <arg type='s' name='video_path' direction='in'/>
            <arg type='s' name='monitor' direction='in'/>
        </method>
        <method name='stream'>
            <arg type='s' name='stream_url' direction='in'/>
            <arg type='s' name='monitor' direction='in'/>
        </method>
        <method name='webpage'>
            <arg type='s' name='webpage_url' direction='in'/>
            <arg type='s' name='monitor' direction='in'/>
        </method>
        <method name='playlist'>
            <arg type='as' name='video_paths' direction='in'/>
            <arg type='s' name='monitor' direction='in'/>
        </method>
        <method name='pause_playback'/>
        <method name='start_playback'/>
        <method name="reload"/>
        <method name="feeling_lucky"/>
        <method name='show_gui'/>
        <method name='quit'/>
        <property name="mode" type="s" access="read"/>
        <property name="volume" type="i" access="readwrite"/>
        <property name="is_mute" type="b" access="readwrite"/>
        <property name="is_playing" type="b" access="read"/>
        <property name="is_paused_by_user" type="b" access="readwrite"/>
        <property name="is_static_wallpaper" type="b" access="readwrite"/>
        <property name="is_pause_when_maximized" type="b" access="readwrite"/>
        <property name="is_mute_when_maximized" type="b" access="readwrite"/>
        <property name="playback_error" type="s" access="read"/>
    </interface>
    </node>
    """

    def __init__(self, version, pkgdatadir, localedir, args):
        setproctitle.setproctitle("apricus-server")

        # Share the login-timescale T0 with all forkserver children.
        os.environ["APRICUS_T0"] = repr(perf.start_time())

        self.version = version
        self.pkgdatadir = pkgdatadir
        self.localedir = localedir
        self.args = args
        self._prev_mode = None
        self._player_count = 0

        # Processes
        # Switch to `forkserver` since v3.2 for performance. BTW `fork` didn't work (it crashes).
        # Ref: https://bnikolic.co.uk/blog/python/parallelism/2019/11/13/python-forkserver-preload.html
        mp.set_start_method("forkserver")
        self.gui_process = None
        self.sys_icon_process = None
        self.player_process = None

        signal.signal(signal.SIGINT, lambda *_: self.quit())
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        # No SIGSEGV handler: after a real segfault the interpreter state is
        # undefined and quit() (D-Bus + joins) is not reentrant. Crashes in
        # native code are diagnosed via faulthandler in the player.
        # Monitoring EndSession (OS reboot, shutdown, etc.)
        EndSessionHandler(self.quit)

        # Configuration
        if args.reset:
            ConfigUtil().generate_template()
        self._load_config()
        perf.mark("server: config loaded")

        # Player process
        self.reload()
        perf.mark("server: player spawn requested")

        # Show main GUI
        if not args.background:
            self.show_gui()

        logger.info("[Server] Started")

    def _load_config(self):
        self.config = ConfigUtil().load()

    def _save_config(self):
        ConfigUtil().save(self.config)

    def _setup_player(self, mode, data_source=None, monitor=None):
        """Setup and run player"""
        # The GUI shares this file: reload so we never persist a stale
        # in-memory copy over a newer on-disk config (and vice versa).
        self._load_config()
        logger.info(f"[Mode] {mode}")
        self.config[CONFIG_KEY_MODE] = mode

        # Set data source if specified
        if data_source is not None:
            if monitor and monitor != "all":
                self.config[CONFIG_KEY_DATA_SOURCE][monitor] = data_source
            else:
                if mode in (MODE_VIDEO, MODE_STREAM, MODE_WEBPAGE):
                    # "All monitors": refresh every stored key plus every
                    # live one, not just Default. Stale per-monitor entries
                    # would otherwise shadow Default in the player's
                    # key→model→Default priority and the wallpaper would
                    # never change. (Mirrors playlist() and the GTK3 panel.)
                    # Note: the server (X11) and the player may enumerate
                    # monitors under different names, so live keys alone are
                    # not enough — stored keys must go too.
                    data = self.config[CONFIG_KEY_DATA_SOURCE]
                    for key in list(data):
                        data[key] = data_source
                    for key in Monitors().get_monitors():
                        data[key] = data_source
            # always update default source
            self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = data_source

        # Persist before spawning: the player process reads the config from
        # disk, so runtime mode switches via D-Bus must not rely on callers
        # saving first (the GUI used to mask this by saving itself).
        self._save_config()

        # Pre-apply the new video's cached static frame so the switch shows
        # the incoming wallpaper instead of the previous one. Best-effort:
        # cache misses are refreshed by the new player as usual.
        self._preview_static(mode, data_source)

        # Reuse the live player when staying inside the VLC family: no
        # unmap/respawn gap, the windows (and their covers) survive.
        if self._try_switch_media(mode):
            perf.mark(f"server: player switched in place [{mode}]")
            self._refresh_systray(mode)
            return

        # Quit current (keep its wallpaper: the new player overwrites it)
        # then create a new player
        self._quit_player(restore_wallpaper=False)

        # Terminate old player process and wait for it to finish
        if self.player_process:
            self.player_process.terminate()
            self.player_process.join(timeout=5)  # Wait up to 5 seconds
            if self.player_process.is_alive():
                logger.warning("[Server] Player process didn't terminate, killing it")
                self.player_process.kill()
                self.player_process.join(timeout=2)
            self.player_process = None

        if mode in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]:
            self.player_process = Process(
                name=f"apricus-player-{self._player_count}", target=_video_player_target
            )
        elif mode == MODE_WEBPAGE:
            self.player_process = Process(
                name=f"apricus-player-{self._player_count}", target=_web_player_target
            )
        elif mode == MODE_NULL:
            pass
        else:
            raise ValueError("[Server] Unknown mode")
        if self.player_process is not None:
            self.player_process.start()
            self._player_count += 1
            perf.mark(f"server: player process started [{mode}]")
            logger.info(f"[Server] player pid={self.player_process.pid}")

        # Refresh systray icon if the mode changed
        self._refresh_systray(mode)

    def _refresh_systray(self, mode):
        if self.config[CONFIG_KEY_SYSTRAY]:
            if self._prev_mode != self.mode:
                if self.sys_icon_process:
                    self.sys_icon_process.terminate()
                    self.sys_icon_process.join(timeout=3)
                    if self.sys_icon_process.is_alive():
                        self.sys_icon_process.kill()
                        self.sys_icon_process.join(timeout=1)
                self.sys_icon_process = Process(
                    name="apricus-systray",
                    target=_systray_target,
                    args=(mode, self.localedir),
                )
                self.sys_icon_process.start()
            self._prev_mode = self.mode

    def _try_switch_media(self, mode):
        """Hot-switch inside the live VLC player (no unmap/respawn gap).

        Works when both sides are the VLC family (video/stream/playlist);
        webpage runs a different player class and always respawns.
        Returns True when the switch was handed off.
        """
        if mode not in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]:
            return False
        if not (self.player_process and self.player_process.is_alive()):
            return False
        try:
            player = get_instance(DBUS_NAME_PLAYER)
            if (
                player is None
                or not hasattr(player, "switch_media")
                or player.mode not in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]
            ):
                return False
            player.switch_media(mode)
            return True
        except (GLib.Error, AttributeError):
            return False

    @staticmethod
    def _preview_static(mode, data_source):
        video = None
        if mode == MODE_VIDEO and data_source:
            video = data_source
        elif mode == MODE_STREAM and data_source:
            video = data_source
        elif mode == MODE_PLAYLIST:
            playlist = ConfigUtil().load().get(CONFIG_KEY_PLAYLIST) or {}
            for queue in [playlist.get("Default")] + [
                q for k, q in playlist.items() if k != "Default"
            ]:
                if isinstance(queue, list) and queue:
                    video = queue[0]
                    break
        if not video:
            return
        try:
            player = get_instance(DBUS_NAME_PLAYER)
            if player and hasattr(player, "preview_static"):
                player.preview_static(video)
        except (GLib.Error, AttributeError):
            pass

    @staticmethod
    def _quit_player(restore_wallpaper=True):
        """Quit current player. Internal respawns pass restore_wallpaper=False
        so the desktop keeps the current static frame instead of flashing
        the pre-Apricus wallpaper between switches."""
        try:
            player = get_instance(DBUS_NAME_PLAYER)
            if player and hasattr(player, "quit_player"):
                player.quit_player(restore_wallpaper)
        except (GLib.Error, AttributeError):
            pass

    def video(self, video_path=None, monitor="", dbus_context=None):
        _authorize("video", dbus_context)
        if video_path and (video_path.startswith("-") or not os.path.isfile(video_path)):
            logger.warning(f"[Server] Rejected video path: {video_path}")
            return
        self._setup_player(MODE_VIDEO, video_path, monitor)

    def stream(self, stream_url=None, monitor="", dbus_context=None):
        _authorize("stream", dbus_context)
        self._setup_player(MODE_STREAM, stream_url, monitor)

    def webpage(self, webpage_url=None, monitor="", dbus_context=None):
        _authorize("webpage", dbus_context)
        self._setup_player(MODE_WEBPAGE, webpage_url, monitor)

    def playlist(self, video_paths=None, monitor=None, dbus_context=None):
        """Play a queue of videos, looping. Muted by design (PipeWire safety)."""
        _authorize("playlist", dbus_context)
        self._load_config()
        video_paths = [p for p in (video_paths or []) if p]
        if not video_paths:
            logger.warning("[Server] Empty playlist, ignoring")
            return
        if len(video_paths) > 500:
            logger.warning(f"[Server] Playlist capped at 500 items ({len(video_paths)} given)")
            video_paths = video_paths[:500]
        playlist = self.config.setdefault(CONFIG_KEY_PLAYLIST, {})
        if monitor and monitor != "all":
            playlist[monitor] = list(video_paths)
        else:
            for key in list(playlist):
                playlist[key] = list(video_paths)
            playlist["Default"] = list(video_paths)
        self._save_config()
        self._setup_player(MODE_PLAYLIST)

    @staticmethod
    def pause_playback(dbus_context=None):
        _authorize("pause_playback", dbus_context)
        player = get_instance(DBUS_NAME_PLAYER)
        if player:
            player.pause_playback()

    @staticmethod
    def start_playback(dbus_context=None):
        _authorize("start_playback", dbus_context)
        player = get_instance(DBUS_NAME_PLAYER)
        if player:
            player.start_playback()

    def reload(self, dbus_context=None):
        _authorize("reload", dbus_context)
        if self.config[CONFIG_KEY_MODE] == MODE_VIDEO:
            self.video()
        elif self.config[CONFIG_KEY_MODE] == MODE_STREAM:
            self.stream()
        elif self.config[CONFIG_KEY_MODE] == MODE_WEBPAGE:
            self.webpage()
        elif self.config[CONFIG_KEY_MODE] == MODE_PLAYLIST:
            self._setup_player(MODE_PLAYLIST)
        elif self.config[CONFIG_KEY_MODE] == MODE_NULL:
            pass
        else:
            raise ValueError("[Server] Unknown mode")

    def feeling_lucky(self, dbus_context=None):
        """Random play a video from the directory"""
        _authorize("feeling_lucky", dbus_context)
        self._load_config()
        monitors = Monitors().get_monitors()
        video_dir = resolve_video_dir(self.config)
        for monitor in monitors:
            file_list = get_video_paths(video_dir)
            # Remove current data source from the random selection
            current = self.config[CONFIG_KEY_DATA_SOURCE].get(monitor)
            if current in file_list:
                file_list.remove(current)
            if not file_list:
                logger.warning(f"[Server] No videos available for monitor {monitor}, skipping")
                continue
            video_path = random.choice(file_list)
            self.config[CONFIG_KEY_MODE] = MODE_VIDEO
            self.config[CONFIG_KEY_DATA_SOURCE][monitor] = video_path
            self._save_config()
            self.video(video_path)

    def show_gui(self, dbus_context=None):
        """Show main GUI (singleton: reuse the live one if present)."""
        _authorize("show_gui", dbus_context)
        if self.gui_process and self.gui_process.is_alive():
            logger.debug("[Server] GUI already running")
            return
        # The GTK4 panel uses the "spawn" start method (fresh interpreter):
        # the forkserver image may legitimately hold Gdk 3.0 by the time the
        # GUI starts (player/systray are Gtk3), and gi forbids a second major
        # in a forked child.
        gui_ctx = mp.get_context("spawn")
        self.gui_process = gui_ctx.Process(
            name="apricus-gui",
            target=_gui_gtk4_target,
            args=(
                self.version,
                self.pkgdatadir,
                self.localedir,
            ),
        )
        self.gui_process.start()

    def quit(self, dbus_context=None):
        _authorize("quit", dbus_context)
        try:
            self._quit_player()
        except GLib.Error:
            pass

        # Quit all processes with proper cleanup
        for process in [self.player_process, self.gui_process, self.sys_icon_process]:
            if process and process.is_alive():
                process.terminate()
                process.join(timeout=3)
                if process.is_alive():
                    logger.warning(f"[Server] Process {process.name} didn't terminate, killing it")
                    process.kill()
                    process.join(timeout=1)

        loop.quit()
        logger.info("[Server] Stopped")

    @property
    def mode(self):
        return self.config[CONFIG_KEY_MODE]

    @property
    def volume(self):
        return self.config[CONFIG_KEY_VOLUME]

    @volume.setter
    def volume(self, volume):
        self.config[CONFIG_KEY_VOLUME] = volume
        self._save_config()
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.volume = volume

    @property
    def is_mute(self):
        return self.config[CONFIG_KEY_MUTE]

    @is_mute.setter
    def is_mute(self, is_mute):
        self.config[CONFIG_KEY_MUTE] = is_mute
        self._save_config()
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.is_mute = is_mute

    @property
    def is_playing(self):
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            return player.is_playing
        return False

    @property
    def is_paused_by_user(self):
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None and player.mode in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]:
            try:
                return player.is_paused_by_user
            except (GLib.Error, AttributeError):
                return None
        return None

    @is_paused_by_user.setter
    def is_paused_by_user(self, is_paused_by_user):
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None and player.mode in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]:
            try:
                player.is_paused_by_user = bool(is_paused_by_user)
            except (GLib.Error, AttributeError):
                pass

    @property
    def is_static_wallpaper(self):
        return self.config[CONFIG_KEY_STATIC_WALLPAPER]

    @is_static_wallpaper.setter
    def is_static_wallpaper(self, is_static_wallpaper):
        self.config[CONFIG_KEY_STATIC_WALLPAPER] = is_static_wallpaper
        self._save_config()
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.reload_config()

    @property
    def is_pause_when_maximized(self):
        return self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED]

    @is_pause_when_maximized.setter
    def is_pause_when_maximized(self, is_pause_when_maximized):
        self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED] = is_pause_when_maximized
        self._save_config()
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.reload_config()

    @property
    def is_mute_when_maximized(self):
        return self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]

    @is_mute_when_maximized.setter
    def is_mute_when_maximized(self, is_mute_when_maximized):
        self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED] = is_mute_when_maximized
        self._save_config()
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.reload_config()

    @property
    def playback_error(self):
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None and player.mode in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]:
            try:
                return getattr(player, "last_error", "") or ""
            except (GLib.Error, AttributeError):
                return ""
        return ""


_bus_singleton = None


def get_instance(dbus_name):
    if is_snap():
        from apricus.snap_ipc import get_service

        return get_service(dbus_name)
    global _bus_singleton
    if _bus_singleton is None:
        _bus_singleton = SessionBus()
    try:
        instance = _bus_singleton.get(dbus_name)
    except GLib.Error:
        return None
    return instance


def main(version, pkgdatadir, localedir, args):
    server = get_instance(DBUS_NAME_SERVER)
    if server is not None:
        server.show_gui()
    else:
        # Pause before launching
        time.sleep(args.p)
        server = ApricusServer(version, pkgdatadir, localedir, args)
        try:
            if is_snap():
                from apricus.snap_ipc import publish_service

                publish_service(DBUS_NAME_SERVER, server)
            else:
                bus = SessionBus()
                bus.publish(DBUS_NAME_SERVER, server)
            loop.run()
        except RuntimeError as e:
            raise Exception("Failed to create server") from e
