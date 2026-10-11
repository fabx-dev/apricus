import ctypes
import hashlib
import logging
import os
import subprocess
import sys
import threading
import time
from threading import Timer

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import vlc
from gi.repository import Gdk, GLib, Gtk
from pydbus import SessionBus

from apricus import mediacache, perf
from apricus.backends import create_wallpaper_backend, create_window_state_backend
from apricus.commons import (
    CONFIG_DIR,
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_FADE_DURATION_SEC,
    CONFIG_KEY_FADE_INTERVAL,
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED,
    CONFIG_KEY_STATIC_WALLPAPER,
    CONFIG_KEY_VOLUME,
    DBUS_NAME_PLAYER,
    LOGGER_NAME,
    MODE_STREAM,
    MODE_VIDEO,
)
from apricus.menu import build_menu
from apricus.player.base_player import BasePlayer
from apricus.utils import (
    ActiveHandler,
    ConfigUtil,
    setup_logging,
)
from apricus.yt_utils import get_best_audio, get_formats, get_optimal_video

logger = logging.getLogger(LOGGER_NAME)


class Fade:
    def __init__(self):
        self.timer = None
        self.is_active = False

    def start(
        self,
        cur,
        target,
        step,
        fade_interval,
        update_callback: callable = None,
        complete_callback: callable = None,
    ):
        # Cancel any existing timer first
        self.cancel()
        self.is_active = True
        self._fade_step(cur, target, step, fade_interval, update_callback, complete_callback)

    def _fade_step(self, cur, target, step, fade_interval, update_callback, complete_callback):
        if not self.is_active:
            return

        new_cur = cur + step
        if (step < 0 and new_cur <= target) or (step > 0 and new_cur >= target):
            new_cur = target
            if update_callback:
                update_callback(int(new_cur))
            if complete_callback:
                complete_callback()
            self.is_active = False
        else:
            if update_callback:
                update_callback(int(new_cur))
            self.timer = Timer(
                fade_interval,
                self._fade_step,
                args=[new_cur, target, step, fade_interval, update_callback, complete_callback],
            )
            self.timer.daemon = True  # Make timer daemon to prevent blocking shutdown
            self.timer.start()

    def cancel(self):
        self.is_active = False
        if self.timer:
            self.timer.cancel()
            self.timer = None


class VLCWidget(Gtk.DrawingArea):
    """
    Simple VLC widget.
    Its player can be controlled through the 'player' attribute, which
    is a vlc.MediaPlayer() instance.
    """

    __gtype_name__ = "VLCWidget"

    def __init__(self, width, height):
        Gtk.DrawingArea.__init__(self)

        # Spawn a VLC instance and create a new media player to embed.
        # Some options need to be specified when instantiating VLC.
        # --no-disable-screensaver: Allow screensaver.
        # --aout=pulse: Force PulseAudio output. VLC's PipeWire audio-output
        #   plugin segfaults (pw_thread_loop_lock) when multiple instances are
        #   active, e.g. one per monitor. See the Flatpak, which uses Pulse too.
        vlc_options = ["--no-disable-screensaver", "--aout=pulse"]
        self.instance = vlc.Instance(vlc_options)
        self.player = self.instance.media_player_new()

        def handle_embed(*args):
            self.player.set_xwindow(self.get_window().get_xid())
            return True

        # Embed and set size.
        self.connect("realize", handle_embed)
        self.set_size_request(width, height)

    def cleanup(self):
        """Cleanup VLC resources to prevent memory leaks"""
        try:
            if self.player:
                self.player.stop()
                self.player.release()
                self.player = None
            if self.instance:
                self.instance.release()
                self.instance = None
        except Exception as e:
            logger.warning(f"[VLCWidget] Cleanup error: {e}")


class PlayerWindow(Gtk.ApplicationWindow):
    def __init__(self, name, width, height, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Setup a VLC widget given the provided width and height.
        self.width = width
        self.height = height
        self.name = name
        self.__vlc_widget = VLCWidget(width, height)
        self.add(self.__vlc_widget)
        self.__vlc_widget.show()

        # These are to allow us to right click. VLC can't hijack mouse input, and probably not key inputs either in
        # Case we want to add keyboard shortcuts later on.
        self.__vlc_widget.player.video_set_mouse_input(False)
        self.__vlc_widget.player.video_set_key_input(False)

        # A timer that handling fade-in/out
        self.fade = Fade()

        # One-shot first-frame timing (Fase 1b baseline). The same media
        # player instance survives media changes, so attach once here.
        self._first_frame_reported = False
        self.__vlc_widget.player.event_manager().event_attach(
            vlc.EventType.MediaPlayerTimeChanged, self._on_first_time_changed
        )

        self.menu = None
        self.connect("button-press-event", self._on_button_press_event)

    def play(self):
        self.__vlc_widget.player.play()

    def play_fade(self, target, fade_duration_sec, fade_interval):
        self.play()
        cur = 0
        step = (target - cur) / (fade_duration_sec / fade_interval)
        self.fade.cancel()
        self.fade.start(
            cur=cur,
            target=target,
            step=step,
            fade_interval=fade_interval,
            update_callback=self.set_volume,
        )

    def is_playing(self):
        return self.__vlc_widget.player.is_playing()

    def pause(self):
        if self.is_playing():
            self.__vlc_widget.player.pause()

    def pause_fade(self, fade_duration_sec, fade_interval):
        cur = self.get_volume()
        target = 0
        step = (target - cur) / (fade_duration_sec / fade_interval)
        self.fade.cancel()
        self.fade.start(
            cur=cur,
            target=target,
            step=step,
            fade_interval=fade_interval,
            update_callback=self.set_volume,
            complete_callback=self.pause,
        )

    def volume_fade(self, target, fade_duration_sec, fade_interval):
        cur = self.get_volume()
        step = (target - cur) / (fade_duration_sec / fade_interval)
        self.fade.cancel()
        self.fade.start(
            cur=cur,
            target=target,
            step=step,
            fade_interval=fade_interval,
            update_callback=self.set_volume,
        )

    def media_new(self, *args):
        return self.__vlc_widget.instance.media_new(*args)

    def set_media(self, *args):
        self.__vlc_widget.player.set_media(*args)

    def set_volume(self, *args):
        self.__vlc_widget.player.audio_set_volume(*args)

    def get_volume(self):
        return self.__vlc_widget.player.audio_get_volume()

    def set_mute(self, is_mute):
        return self.__vlc_widget.player.audio_set_mute(is_mute)

    def get_position(self):
        return self.__vlc_widget.player.get_position()

    def set_position(self, *args):
        self.__vlc_widget.player.set_position(*args)

    def snapshot(self, *args):
        return self.__vlc_widget.player.video_take_snapshot(*args)

    def centercrop(self, video_width=None, video_height=None):
        # Getting dimension from libvlc is not reliable enough (need to consider timing)
        if (video_width, video_height) == (None, None):
            video_width, video_height = self.__vlc_widget.player.video_get_size()
            if video_width == 0 or video_height == 0:
                logger.warning("[CenterCrop] video_get_size is not ready yet")
                return
        logger.debug(f"[CenterCrop] Dimension {video_width}x{video_height}")
        window_ratio = self.width / self.height
        video_ratio = video_width / video_height
        if window_ratio == video_ratio:
            return
        elif video_ratio < window_ratio:
            # If window is wider than video
            # For example video ratio (4:3)=1.33..., window ratio (16:9)=1.77...
            crop_height = video_width / window_ratio
            top_offset = (video_height - crop_height) / 2
            crop_geometry = (
                f"{int(video_width)}x{int(crop_height + top_offset)}+0+{int(top_offset)}"
            )

        else:
            # If video is wider than window
            crop_width = video_height * window_ratio
            left_offset = (video_width - crop_width) / 2
            crop_geometry = (
                f"{int(crop_width + left_offset)}x{int(video_height)}+{int(left_offset)}+0"
            )

        # Crop geometry WxH+L+T: Width x Height + Left Offset + top Offset
        logger.debug(f"[CenterCrop] Crop geometry: {crop_geometry}")
        self.__vlc_widget.player.video_set_crop_geometry(crop_geometry)

    def add_audio_track(self, audio):
        self.__vlc_widget.player.add_slave(vlc.MediaSlaveType(1), audio, True)

    def _on_first_time_changed(self, _event):
        if self._first_frame_reported:
            return
        self._first_frame_reported = True
        try:
            self.__vlc_widget.player.event_manager().event_detach(
                vlc.EventType.MediaPlayerTimeChanged
            )
        except Exception as e:
            logger.warning(f"[Perf] event_detach failed: {e}")
        perf.mark(f"player: first frame [{self.name}]")

    def _on_button_press_event(self, widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            if not self.menu:
                self.menu = build_menu(MODE_VIDEO)
            self.menu.popup_at_pointer()
            return True
        return False

    def get_name(self):
        return self.name

    def cleanup(self):
        """Cleanup resources to prevent memory leaks"""
        self.fade.cancel()
        if self.__vlc_widget:
            self.__vlc_widget.cleanup()


class VideoPlayer(BasePlayer):
    """
    <node>
    <interface name='io.github.fabx_dev.apricus.player'>
        <property name="mode" type="s" access="read"/>
        <property name="data_source" type="s" access="readwrite"/>
        <property name="volume" type="i" access="readwrite"/>
        <property name="is_mute" type="b" access="readwrite"/>
        <property name="is_playing" type="b" access="read"/>
        <property name="is_paused_by_user" type="b" access="readwrite"/>
        <method name='reload_config'/>
        <method name='pause_playback'/>
        <method name='start_playback'/>
        <method name='quit_player'/>
    </interface>
    </node>
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Initialize X11 threads so VLC can use hardware decoding.
        # `libX11.so.6` fix for Fedora 33
        x11 = None
        for lib in ["libX11.so", "libX11.so.6"]:
            try:
                x11 = ctypes.cdll.LoadLibrary(lib)
            except OSError:
                pass
            if x11 is not None:
                x11.XInitThreads()
                break

        self.config = None
        self.reload_config()

        # DE-specific behavior (wallpaper + window state) lives in backends.
        self.wallpaper = create_wallpaper_backend()
        self.wallpaper.capture_original()

        # Handler should be created after everything initialized
        self.active_handler, self.window_state = None, None
        self.is_any_maximized, self.is_any_fullscreen = False, False
        self.is_paused_by_user = False

    def new_window(self, gdk_monitor):
        rect = gdk_monitor.get_geometry()
        return PlayerWindow(gdk_monitor.get_model(), rect.width, rect.height, application=self)

    def do_activate(self):
        super().do_activate()
        self.data_source = self.config[CONFIG_KEY_DATA_SOURCE]

    def _on_monitor_added(self, _, gdk_monitor, *args):
        super()._on_monitor_added(_, gdk_monitor, *args)
        self.monitor_sync()

    def _on_active_changed(self, active):
        if active:
            self.pause_playback()
        else:
            if self._should_playback_start():
                self.start_playback()
            else:
                self.pause_playback()

    def _on_window_state_changed(self, state):
        self.is_any_maximized, self.is_any_fullscreen = (
            state["is_any_maximized"],
            state["is_any_fullscreen"],
        )
        logger.info(
            f"is_any_maximized: {self.is_any_maximized}, is_any_fullscreen: {self.is_any_fullscreen}"
        )

        if self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED]:
            if self._should_playback_start():
                self.start_playback()
            else:
                self.pause_playback()
        elif self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]:
            for monitor, window in self.windows.items():
                if not monitor.is_primary():
                    continue
                if self.is_any_fullscreen or self.is_any_maximized:
                    window.volume_fade(
                        target=0,
                        fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                        fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL],
                    )
                else:
                    window.volume_fade(
                        target=self.volume,
                        fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                        fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL],
                    )

    def _should_playback_start(self):
        if self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED] and (
            self.is_any_maximized or self.is_any_fullscreen
        ):
            return False
        if self.is_paused_by_user:
            return False
        return True

    @property
    def mode(self):
        return self.config[CONFIG_KEY_MODE]

    @property
    def data_source(self):
        return self.config[CONFIG_KEY_DATA_SOURCE]

    @staticmethod
    def _source_for(data_source, key, model):
        """Resolve (source, matched_key) for a monitor.

        Priority: stable `key`, then legacy bare `model` (pre-migration
        configs), then `Default`. Empty strings count as unset.
        """
        for candidate in (key, model):
            if candidate in data_source and len(data_source[candidate]) != 0:
                return data_source[candidate], candidate
        return data_source.get("Default", ""), "Default"

    @data_source.setter
    def data_source(self, data_source):
        self.config[CONFIG_KEY_DATA_SOURCE] = data_source

        # Stable config keys for live monitors (unique models keep the bare
        # model; duplicates get Model#i). Order matches self.windows.
        key_of = {}
        if self.windows:
            from apricus.monitor import assign_keys

            keyed = assign_keys(
                [
                    {
                        "name": m.get_model(),
                        "x": m.get_geometry().x,
                        "y": m.get_geometry().y,
                    }
                    for m in self.windows
                ]
            )
            for gdk_monitor, info in zip(self.windows, keyed, strict=True):
                key_of[id(gdk_monitor)] = info["key"]

        if self.mode == MODE_VIDEO:
            # Video dimensions come from the sidecar cache: a stat() on hit,
            # a single ffprobe per new/changed video on miss.
            video_width, video_height = {}, {}
            for monitor, video in data_source.items():
                # fallback to Default video
                if len(video) == 0:
                    video = data_source["Default"]
                width, height, _duration = mediacache.probe_video(video)
                video_width[monitor] = width
                video_height[monitor] = height

            for monitor, window in self.windows.items():
                mkey = key_of.get(id(monitor), monitor.get_model())
                source, srckey = self._source_for(data_source, mkey, monitor.get_model())
                logger.info(f"Setting source {source} to {mkey}")
                media = window.media_new(source)
                """
                This loops the media itself. Using -R / --repeat and/or -L / --loop don't seem to work. However,
                based on reading, this probably only repeats 65535 times, which is still a lot of time, but might
                cause the program to stop playback if it's left on for a very long time.
                """
                media.add_option("input-repeat=65535")
                # Prevent awful ear-rape with multiple instances.
                if not monitor.is_primary():
                    media.add_option("no-audio")
                window.set_media(media)
                # No set_position(0.0): a freshly set media starts at 0, and the
                # extra seek costs a demuxer round-trip on the startup path.
                dims = (video_width.get(srckey), video_height.get(srckey))
                if dims == (None, None):
                    dims = (video_width.get("Default"), video_height.get("Default"))
                window.centercrop(*dims)

        elif self.mode == MODE_STREAM:
            source = data_source["Default"]
            formats = get_formats(source)
            max_height = (
                max(self.windows, key=lambda m: m.get_geometry().height).get_geometry().height
            )
            video_url, video_width, video_height = get_optimal_video(formats, max_height)
            audio_url = get_best_audio(formats)

            for monitor, window in self.windows.items():
                media = window.media_new(video_url)
                media.add_option("input-repeat=65535")
                window.set_media(media)
                if monitor.is_primary():
                    window.add_audio_track(audio_url)
                else:
                    # `get_optimal_video` now might return video with audio.
                    media.add_option("no-audio")
                window.centercrop(video_width, video_height)
        else:
            raise ValueError("Invalid mode")

        self.volume = self.config[CONFIG_KEY_VOLUME]
        self.is_mute = self.config[CONFIG_KEY_MUTE]
        self.start_playback()

        # Everything is initialized. Create handlers if haven't (singleton pattern).
        if not self.active_handler:
            self.active_handler = ActiveHandler(self._on_active_changed)
        if not self.window_state:
            self.window_state = create_window_state_backend(self._on_window_state_changed)
            self.window_state.start()

        if self.config[CONFIG_KEY_STATIC_WALLPAPER] and self.mode == MODE_VIDEO:
            self.set_static_wallpaper()
        else:
            self.set_original_wallpaper()

    @property
    def volume(self):
        return self.config[CONFIG_KEY_VOLUME]

    @volume.setter
    def volume(self, volume):
        self.config[CONFIG_KEY_VOLUME] = volume
        for monitor in self.windows:
            if monitor.is_primary():
                self.windows[monitor].set_volume(volume)

    @property
    def is_mute(self):
        return self.config[CONFIG_KEY_MUTE]

    @is_mute.setter
    def is_mute(self, is_mute):
        self.config[CONFIG_KEY_MUTE] = is_mute
        for monitor, window in self.windows.items():
            if monitor.is_primary():
                window.set_mute(is_mute)

    @property
    def is_playing(self):
        return not self.is_paused_by_user

    def pause_playback(self):
        for _monitor, window in self.windows.items():
            window.pause_fade(
                fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL],
            )

    def start_playback(self):
        if self._should_playback_start():
            for _monitor, window in self.windows.items():
                window.play_fade(
                    target=self.volume,
                    fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                    fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL],
                )

    def monitor_sync(self):
        primary_monitor = None
        for monitor, _window in self.windows.items():
            if monitor.is_primary:
                primary_monitor = monitor
                break
        if primary_monitor:
            primary_window = self.windows[primary_monitor]
            primary_pos = primary_window.get_position()
            primary_playing = primary_window.is_playing()
            for monitor, window in self.windows.items():
                if monitor == primary_monitor:
                    continue
                # `set_position()` requires the playback to be enabled before calling.
                window.play()
                window.set_position(primary_pos)
                if not primary_playing:
                    window.pause()

    @staticmethod
    def _static_cache_path(video_path):
        """Deterministic cache file for a video (stable across restarts)."""
        digest = hashlib.sha1(os.path.abspath(video_path).encode()).hexdigest()[:16]
        return os.path.join(CONFIG_DIR, f"static-{digest}.png")

    def set_static_wallpaper(self):
        if not self.wallpaper.supports_static:
            return
        video = self.data_source["Default"]
        cached = self._static_cache_path(video)
        if os.path.isfile(cached):
            # Instant path: show last frame immediately, no ffmpeg involved.
            perf.mark("player: static cache hit")
            self._apply_static_wallpaper(cached)
        else:
            perf.mark("player: static cache miss")
        # Refresh the frame in background unless fast login startup.
        if os.environ.get("APRICUS_FAST") == "1":
            perf.mark("player: static refresh skipped (fast mode)")
            return
        self._static_token = getattr(self, "_static_token", 0) + 1
        thread = threading.Thread(
            target=self._generate_static_wallpaper,
            args=(video, cached, self._static_token),
            daemon=True,
        )
        thread.start()

    def _generate_static_wallpaper(self, video, cached, token):
        _width, _height, duration = mediacache.probe_video(video)
        if duration is None:
            duration = 0
        # Find the golden ratio
        ss = time.strftime("%H:%M:%S", time.gmtime(duration / 3.14))
        # Keep a proper image extension so ffmpeg infers the muxer.
        tmp_path = f"{cached}.tmp.png"
        ret = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                ss,
                "-i",
                video,
                "-vframes",
                "1",
                tmp_path,
            ],
            shell=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
        )
        if ret.returncode != 0 or not os.path.isfile(tmp_path):
            logger.warning("[StaticWallpaper] frame extraction failed")
            return
        # Sharp frame, no blur: the static wallpaper sits underneath the video
        # and blur only added startup cost for no visible benefit.
        try:
            os.replace(tmp_path, cached)
        except OSError as e:
            logger.warning(f"[StaticWallpaper] {e}")
            return
        finally:
            if os.path.isfile(tmp_path) and tmp_path != cached:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        perf.mark("player: static cache refreshed")
        if token == getattr(self, "_static_token", None):
            GLib.idle_add(self._apply_static_wallpaper, cached)

    def _apply_static_wallpaper(self, static_wallpaper_path):
        self.wallpaper.apply_static(static_wallpaper_path)
        return False

    def set_original_wallpaper(self):
        self.wallpaper.restore_original()
        # Prune stale static-wallpaper caches, keeping the ones for the
        # currently configured videos (instant wallpaper on next login).
        # Legacy random-named caches (static-XXXXXX.png) match nothing and go.
        keep = set()
        try:
            for video in self.data_source.values():
                if video:
                    keep.add(os.path.abspath(self._static_cache_path(video)))
        except AttributeError:
            pass
        self.wallpaper.prune_cache(keep)

    def reload_config(self):
        self.config = ConfigUtil().load()

    def quit_player(self):
        self.set_original_wallpaper()

        # Cleanup handlers
        if self.active_handler:
            self.active_handler.cleanup()
            self.active_handler = None

        if self.window_state:
            self.window_state.stop()
            self.window_state = None

        # Cleanup all windows
        for _monitor, window in self.windows.items():
            if window:
                window.cleanup()

        super().quit_player()


def main():
    # Dump Python tracebacks on C-level crashes (segfaults in VLC/Gtk).
    # Invaluable for diagnosing silent player deaths; always on, negligible cost.
    import faulthandler
    import os

    faulthandler.enable()
    setup_logging()
    logger.info(f"[Player] pid={os.getpid()}")
    bus = SessionBus()
    app = VideoPlayer()
    try:
        bus.publish(DBUS_NAME_PLAYER, app)
    except RuntimeError as e:
        logger.error(e)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
