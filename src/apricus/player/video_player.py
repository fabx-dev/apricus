import ctypes
import hashlib
import logging
import os
import random
import subprocess
import sys
import tempfile
import threading
import time
from threading import Timer

import gi
import yt_dlp

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
import vlc
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk
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
    CONFIG_KEY_PLAYLIST,
    CONFIG_KEY_SHUFFLE,
    CONFIG_KEY_STATIC_WALLPAPER,
    CONFIG_KEY_VOLUME,
    DBUS_NAME_PLAYER,
    LOGGER_NAME,
    MODE_PLAYLIST,
    MODE_STREAM,
    MODE_VIDEO,
)
from apricus.menu import build_menu
from apricus.player.base_player import BasePlayer
from apricus.utils import (
    ActiveHandler,
    ConfigUtil,
    _authorize,
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

    def __init__(self, width, height, disable_audio=False):
        Gtk.DrawingArea.__init__(self)

        # Spawn a VLC instance and create a new media player to embed.
        # Some options need to be specified when instantiating VLC.
        # --no-disable-screensaver: Allow screensaver.
        # --aout=pulse: Force PulseAudio output. VLC's PipeWire audio-output
        #   plugin segfaults (pw_thread_loop_lock) when multiple instances are
        #   active, e.g. one per monitor. See the Flatpak, which uses Pulse too.
        # Playlist mode never needs audio: opening/closing the audio output
        # on every transition locks up PipeWire, so it is disabled entirely.
        if disable_audio:
            vlc_options = [
                "--no-disable-screensaver",
                "--no-interact",
                "--no-audio",
                "--aout=none",
            ]
        else:
            vlc_options = ["--no-disable-screensaver", "--no-interact", "--aout=pulse"]
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
    def __init__(self, name, width, height, *args, mode=MODE_VIDEO, **kwargs):
        super().__init__(*args, **kwargs)
        # Setup a VLC widget given the provided width and height.
        self.width = width
        self.height = height
        self.name = name
        self._mode = mode
        self.__vlc_widget = VLCWidget(width, height, disable_audio=(mode == MODE_PLAYLIST))
        # Opaque black until the first frame lands: otherwise the theme
        # background flashes through on every video switch.
        self.override_background_color(Gtk.StateFlags.NORMAL, Gdk.RGBA(0, 0, 0, 1))
        # Cover image painted over the video area until the real first frame
        # lands (pure GtkImage overlay: no cairo callbacks involved).
        self.__overlay = Gtk.Overlay()
        self.__cover_image = Gtk.Image()
        self.__cover_image.set_no_show_all(True)
        self.__overlay.add(self.__vlc_widget)
        self.__overlay.add_overlay(self.__cover_image)
        self.__overlay.set_overlay_pass_through(self.__cover_image, True)
        # Playlist state (used only in MODE_PLAYLIST).
        self._queue = []
        self._queue_index = 0
        self._transitioning = False
        self.add(self.__overlay)
        self.__vlc_widget.show()
        self.__overlay.show()

        # These are to allow us to right click. VLC can't hijack mouse input, and probably not key inputs either in
        # Case we want to add keyboard shortcuts later on.
        self.__vlc_widget.player.video_set_mouse_input(False)
        self.__vlc_widget.player.video_set_key_input(False)

        # A timer that handling fade-in/out
        self.fade = Fade()

        # One-shot first-frame timing (Fase 1b baseline). The same media
        # player instance survives media changes, so attach once here.
        self._first_frame_reported = False
        # Last VLC failure for the current media (None when healthy).
        # libvlc reports input errors only via events; without this,
        # is_playing() stays true on a dead stream and the GUI shows
        # nothing. Reset on every set_media(), like the frame flag.
        self._play_error = None
        self.__vlc_widget.player.event_manager().event_attach(
            vlc.EventType.MediaPlayerTimeChanged, self._on_first_time_changed
        )
        self.__vlc_widget.player.event_manager().event_attach(
            vlc.EventType.MediaPlayerEncounteredError, self._on_media_error
        )

        self.menu = None
        self.connect("button-press-event", self._on_button_press_event)

    def play(self):
        self.__vlc_widget.player.play()

    def stop(self):
        try:
            self.__vlc_widget.player.stop()
        except Exception as e:
            logger.warning(f"[PlayerWindow] stop failed: {e}")

    def attach_end_reached(self, callback):
        """Call callback() on the GTK main thread when the media ends."""

        def _cb(_event):
            GLib.idle_add(callback)

        self.__vlc_widget.player.event_manager().event_attach(
            vlc.EventType.MediaPlayerEndReached, _cb
        )

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
        # New media, new verdict: drop the previous error/frame state so a
        # stale failure can never mask the fresh attempt (and vice versa).
        self._play_error = None
        self._first_frame_reported = False
        self.__vlc_widget.player.set_media(*args)

    def _on_media_error(self, _event):
        # libvlc gives no message payload: record the fact, never the URL
        # (stream URLs carry expiring signatures — keep them out of logs).
        if self._play_error is None:
            self._play_error = "error"
            logger.warning(f"[Player:{self.name}] Playback failed ({self._mode})")

    def playback_error(self):
        """Human-readable failure for the current media, or None.

        Returns None when healthy, after voluntary stop(), and before any
        media is set. Never contains URLs or tokens.
        """
        if self._play_error is None:
            return None
        if self._first_frame_reported:
            return "Playback failed"
        return "Playback failed before the first frame was shown"

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

    def set_cover(self, image_path):
        """Show a cached frame until the real first frame lands."""
        self._cover_token = getattr(self, "_cover_token", 0) + 1
        token = self._cover_token
        try:
            w = self.get_allocated_width() or self.width
            h = self.get_allocated_height() or self.height
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(image_path, w, h, True)
        except (GLib.Error, OSError):
            return
        self.__cover_image.set_from_pixbuf(pixbuf)
        self.__cover_image.show()
        GLib.timeout_add_seconds(5, self._clear_cover_timeout, token)

    def _clear_cover_timeout(self, token):
        if token == getattr(self, "_cover_token", None):
            self.__cover_image.hide()
        return False

    def _on_first_time_changed(self, _event):
        self.__cover_image.hide()
        if self._first_frame_reported:
            return
        self._first_frame_reported = True
        perf.mark(f"player: first frame [{self.name}]")

    def _on_button_press_event(self, widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            if not self.menu:
                self.menu = build_menu(self._mode)
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
        <property name="last_error" type="s" access="read"/>
        <method name='preview_static'>
            <arg type='s' name='video_path' direction='in'/>
        </method>
        <method name='switch_media'>
            <arg type='s' name='mode' direction='in'/>
        </method>
        <method name='reload_config'/>
        <method name='pause_playback'/>
        <method name='start_playback'/>
        <method name='quit_player'>
            <arg type='b' name='restore_wallpaper' direction='in'/>
        </method>
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
        return PlayerWindow(
            gdk_monitor.get_model(), rect.width, rect.height, application=self, mode=self.mode
        )

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
        self._apply_source(data_source, setup_handlers=True)

    def switch_media(self, mode, dbus_context=None):
        """Hot-switch source inside the live player (no respawn)."""
        _authorize("player.switch_media", dbus_context)
        GLib.idle_add(self._switch_media_main, mode)

    def _switch_media_main(self, mode):
        self.reload_config()
        self._apply_source(self.config[CONFIG_KEY_DATA_SOURCE], setup_handlers=False)
        perf.mark(f"player: switched [{mode}]")
        return False

    def _apply_source(self, data_source, setup_handlers):

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
                if self.wallpaper.supports_static:
                    cover = self._static_cache_path(source)
                    if os.path.isfile(cover):
                        window.set_cover(cover)
                window.stop()
                window.set_media(media)
                # No set_position(0.0): a freshly set media starts at 0, and the
                # extra seek costs a demuxer round-trip on the startup path.
                dims = (video_width.get(srckey), video_height.get(srckey))
                if dims == (None, None):
                    dims = (video_width.get("Default"), video_height.get("Default"))
                window.centercrop(*dims)

        elif self.mode == MODE_STREAM:
            by_source = {}
            for monitor, window in self.windows.items():
                mkey = key_of.get(id(monitor), monitor.get_model())
                source, _srckey = self._source_for(data_source, mkey, monitor.get_model())
                by_source.setdefault(source, []).append((monitor, window))
            for source, targets in by_source.items():
                if not source:
                    continue
                try:
                    formats = get_formats(source)
                except Exception as e:
                    logger.warning(f"[Stream] Cannot resolve {source}: {e}")
                    continue
                max_height = max(m.get_geometry().height for m, _ in targets)
                video_url, video_width, video_height = get_optimal_video(formats, max_height)
                audio_url = get_best_audio(formats)

                for monitor, window in targets:
                    media = window.media_new(video_url)
                    media.add_option("input-repeat=65535")
                    window.set_media(media)
                    if monitor.is_primary():
                        window.add_audio_track(audio_url)
                    else:
                        # `get_optimal_video` now might return video with audio.
                        media.add_option("no-audio")
                    window.centercrop(video_width, video_height)
        elif self.mode == MODE_PLAYLIST:
            playlist = self.config.get(CONFIG_KEY_PLAYLIST) or {}
            shuffle = bool(self.config.get(CONFIG_KEY_SHUFFLE))
            for monitor, window in self.windows.items():
                mkey = key_of.get(id(monitor), monitor.get_model())
                queue = self._queue_for(playlist, mkey, monitor.get_model())
                if shuffle:
                    queue = random.sample(queue, len(queue))
                if not queue:
                    logger.warning(f"[Playlist] No videos for {mkey}, monitor stays blank")
                    continue
                window._queue = queue
                window._queue_index = 0
                window._transitioning = False
                if not getattr(window, "_end_attached", False):
                    window.attach_end_reached(lambda w=window: self._on_media_end(w))
                    window._end_attached = True
                self._play_queue_item(window)
        else:
            raise ValueError("Invalid mode")

        self.volume = self.config[CONFIG_KEY_VOLUME]
        self.is_mute = self.config[CONFIG_KEY_MUTE]
        self.start_playback()

        # Everything is initialized. Create handlers if haven't (singleton pattern).
        if setup_handlers:
            if not self.active_handler:
                self.active_handler = ActiveHandler(self._on_active_changed)
            if not self.window_state:
                self.window_state = create_window_state_backend(self._on_window_state_changed)
                self.window_state.start()

        if self.config[CONFIG_KEY_STATIC_WALLPAPER] and self.mode == MODE_VIDEO:
            self.set_static_wallpaper()
        elif self.config[CONFIG_KEY_STATIC_WALLPAPER] and self.mode == MODE_STREAM:
            # Streams have no local frame: pin the stream thumbnail as the
            # static underneath (previously streams pruned it -> blue).
            default_source = (data_source or {}).get("Default", "")
            if default_source.startswith(("http://", "https://")):
                threading.Thread(
                    target=self._generate_stream_static,
                    args=(default_source,),
                    daemon=True,
                ).start()
        elif self.config[CONFIG_KEY_STATIC_WALLPAPER] and self.mode == MODE_PLAYLIST:
            # Static frame from the first queued video only; per-transition
            # refreshes would thrash ffmpeg on every video change.
            first = self._first_queued_video()
            if first:
                self.set_static_wallpaper(first)
            else:
                self.wallpaper.prune_cache(self._live_cache_paths())
        else:
            self.wallpaper.prune_cache(self._live_cache_paths())

    @staticmethod
    def _queue_for(playlist, key, model):
        """Resolve the play queue for a monitor: stable key, legacy model,
        then Default. Non-list or missing entries count as empty."""
        for candidate in (key, model, "Default"):
            queue = playlist.get(candidate)
            if isinstance(queue, list) and queue:
                return list(queue)
        return []

    def _first_queued_video(self):
        playlist = self.config.get(CONFIG_KEY_PLAYLIST) or {}
        for queue in [playlist.get("Default")] + [q for k, q in playlist.items() if k != "Default"]:
            if isinstance(queue, list) and queue:
                return queue[0]
        return None

    def _play_queue_item(self, window):
        """Play window._queue[window._queue_index]. Main thread only."""
        if getattr(window, "_transitioning", False):
            logger.debug("[Playlist] Transition already in progress, skipping")
            return
        queue = getattr(window, "_queue", [])
        if not queue:
            return
        window._transitioning = True
        try:
            video = queue[window._queue_index % len(queue)]
            logger.info(f"Setting playlist source {video} to {window.get_name()}")
            width, height, _duration = mediacache.probe_video(video)
            media = window.media_new(video)
            media.add_option("no-audio")
            window.stop()
            window.set_media(media)
            window.play()
            window.centercrop(width, height)
        finally:
            window._transitioning = False

    def _on_media_end(self, window):
        """Advance the queue. Invoked on the GTK main thread via idle_add."""
        queue = getattr(window, "_queue", [])
        if not queue:
            return False
        window._queue_index = (window._queue_index + 1) % len(queue)
        self._play_queue_item(window)
        return False

    @property
    def volume(self):
        return self.config[CONFIG_KEY_VOLUME]

    @volume.setter
    def volume(self, volume):
        self.config[CONFIG_KEY_VOLUME] = volume
        if self.mode == MODE_PLAYLIST:
            return  # playlist players have no audio backend
        for monitor in self.windows:
            if monitor.is_primary():
                self.windows[monitor].set_volume(volume)

    @property
    def is_mute(self):
        return self.config[CONFIG_KEY_MUTE]

    @is_mute.setter
    def is_mute(self, is_mute):
        self.config[CONFIG_KEY_MUTE] = is_mute
        if self.mode == MODE_PLAYLIST:
            return  # playlist players have no audio backend
        for monitor, window in self.windows.items():
            if monitor.is_primary():
                window.set_mute(is_mute)

    @property
    def is_playing(self):
        return not self.is_paused_by_user

    def playback_error(self):
        """First window failure for the current media, if any (None else).

        Lets the GUI report a dead stream instead of trusting is_playing,
        which only mirrors the user-pause flag. See PlayerWindow.
        """
        for window in self.windows.values():
            err = window.playback_error()
            if err is not None:
                return err
        return None

    @property
    def last_error(self):
        return self.playback_error() or ""

    def pause_playback(self, dbus_context=None):
        _authorize("player.pause_playback", dbus_context)
        for _monitor, window in self.windows.items():
            window.pause_fade(
                fade_duration_sec=self.config[CONFIG_KEY_FADE_DURATION_SEC],
                fade_interval=self.config[CONFIG_KEY_FADE_INTERVAL],
            )

    def start_playback(self, dbus_context=None):
        _authorize("player.start_playback", dbus_context)
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

    def set_static_wallpaper(self, video=None):
        if not self.wallpaper.supports_static:
            return
        video = video or self.data_source["Default"]
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

    @staticmethod
    def _static_ffmpeg_cmd(video, ss, tmp_path):
        # Force 8-bit output: 10-bit sources (e.g. yuv420p10le) otherwise
        # produce 16-bit PNGs that Mutter refuses as wallpaper (blue fallback).
        return [
            "ffmpeg",
            "-y",
            "-ss",
            ss,
            "-i",
            video,
            "-vframes",
            "1",
            "-pix_fmt",
            "rgb24",
            tmp_path,
        ]

    def _generate_static_wallpaper(self, video, cached, token):
        _width, _height, duration = mediacache.probe_video(video)
        if duration is None:
            duration = 0
        # Find the golden ratio
        ss = time.strftime("%H:%M:%S", time.gmtime(duration / 3.14))
        # Keep a proper image extension so ffmpeg infers the muxer.
        tmp_path = f"{cached}.tmp.png"
        try:
            ret = subprocess.run(
                self._static_ffmpeg_cmd(video, ss, tmp_path),
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                timeout=60,
            )
        except subprocess.TimeoutExpired:
            logger.warning("[StaticWallpaper] ffmpeg timed out")
            return
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
        self._last_static_path = static_wallpaper_path
        self.wallpaper.apply_static(static_wallpaper_path)
        return False

    def _generate_stream_static(self, url):
        """Pin a downloaded thumbnail as static for streams (background)."""
        from apricus.stream_thumbs import check_magic, decode_size
        from apricus.utils import public_http_url, valid_http_url
        from apricus.yt_utils import base_options

        if not valid_http_url(url):
            return
        if not public_http_url(url):
            logger.debug("[StaticWallpaper] refusing non-public URL")
            return
        try:
            from apricus.yt_utils import get_cached_meta, save_cached_meta

            meta = get_cached_meta(url)
            if meta is None:
                with yt_dlp.YoutubeDL(base_options(quiet=True)) as ydl:
                    meta = ydl.extract_info(url, download=False) or {}
                save_cached_meta(url, meta)
            thumb_url = meta.get("thumbnail")
            data = None
            if thumb_url and thumb_url.startswith("https://"):
                from apricus.utils import fetch_public

                data, _status, content_type, _reason = fetch_public(
                    thumb_url, timeout=15, max_bytes=2 * 1024 * 1024
                )
                if data is None or "image" not in content_type:
                    data = None
                elif not check_magic(data) or decode_size(data) is None:
                    data = None
            if data is None:
                # Direct video URLs have no thumbnail metadata: extract a
                # frame like local files do (8-bit forced by the helper).
                import re

                if not re.search(r"\.(mp4|webm|mov)(\?|#|$)", (url or "").lower()):
                    return
                data = self._extract_remote_frame(url)
                if data is None:
                    return
            cached = self._static_cache_path(url)
            tmp_path = f"{cached}.tmp.png"
            loader = GdkPixbuf.PixbufLoader.new()
            loader.write_bytes(GLib.Bytes(data))
            loader.close()
            pixbuf = loader.get_pixbuf()
            if pixbuf is None:
                return
            try:
                pixbuf.savev(tmp_path, "png", [], [])
                os.replace(tmp_path, cached)
            except Exception:
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
                raise
        except Exception as e:  # noqa: BLE001 - static stays as-is
            logger.debug(f"[StaticWallpaper] stream static failed: {e}")
            return
        GLib.idle_add(self._apply_static_wallpaper, cached)

    @staticmethod
    def _extract_remote_frame(url):
        """Single 8-bit frame from a direct video URL, or None."""
        from apricus.utils import public_http_url, valid_http_url

        if not valid_http_url(url):
            return None
        if not public_http_url(url):
            return None
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="apricus-static-")
        os.close(fd)
        try:
            ret = subprocess.run(
                VideoPlayer._static_ffmpeg_cmd(url, "00:00:03", tmp),
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.STDOUT,
                timeout=60,
            )
            if ret.returncode != 0 or not os.path.isfile(tmp):
                return None
            with open(tmp, "rb") as f:
                return f.read()
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.debug(f"[StaticWallpaper] remote frame failed: {e}")
            return None
        finally:
            try:
                if os.path.isfile(tmp):
                    os.remove(tmp)
            except OSError:
                pass

    def preview_static(self, video_path, dbus_context=None):
        """Apply the cached static frame for an upcoming video, if present.

        Called by the server *before* the current player quits, so a video
        switch shows the new frame instead of the previous one. Cache misses
        are ignored: the new player refreshes in background as usual.
        For stream URLs the thumbnail is fetched in background instead.
        """
        if not self.wallpaper.supports_static or not video_path:
            return False
        _authorize("player.preview_static", dbus_context)
        cached = self._static_cache_path(video_path)
        if os.path.isfile(cached):
            self._apply_static_wallpaper(cached)
            perf.mark("player: static preview applied")
            return True
        if video_path.startswith(("http://", "https://")):
            threading.Thread(
                target=self._generate_stream_static, args=(video_path,), daemon=True
            ).start()
        return False

    def _live_cache_paths(self):
        # Static caches for the currently configured videos (instant
        # wallpaper on next login). Legacy random-named caches match
        # nothing and go. The currently applied static is always kept so
        # pruning can never pull the wallpaper from under a live desktop.
        keep = set()
        current = getattr(self, "_last_static_path", None)
        if current:
            keep.add(os.path.abspath(current))
        try:
            for video in self.data_source.values():
                if video:
                    keep.add(os.path.abspath(self._static_cache_path(video)))
        except AttributeError:
            pass
        return keep

    def reload_config(self, dbus_context=None):
        _authorize("player.reload_config", dbus_context)
        self.config = ConfigUtil().load()

    def quit_player(self, restore_wallpaper=True, dbus_context=None):
        _authorize("player.quit", dbus_context)
        # NOTE: we intentionally do NOT restore the pre-Apricus wallpaper:
        # the last static frame stays as the desktop background across
        # logins, so re-entering the session never shows a bare default.
        self.wallpaper.prune_cache(self._live_cache_paths())

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
    app = VideoPlayer()
    try:
        from apricus.utils import is_snap

        if is_snap():
            from apricus.snap_ipc import publish_service

            publish_service(DBUS_NAME_PLAYER, app)
        else:
            bus = SessionBus()
            bus.publish(DBUS_NAME_PLAYER, app)
    except RuntimeError as e:
        logger.error(e)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
