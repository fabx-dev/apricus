import glob
import hashlib
import logging
import os
import pathlib
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, GLib, Gtk, WebKit2
from pydbus import SessionBus

from apricus.backends import create_wallpaper_backend
from apricus.commons import (
    CONFIG_DIR,
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_STATIC_WALLPAPER,
    CONFIG_KEY_VOLUME,
    DBUS_NAME_PLAYER,
    LOGGER_NAME,
    MODE_WEBPAGE,
)
from apricus.menu import build_menu
from apricus.player.base_player import BasePlayer
from apricus.utils import ConfigUtil, _authorize, public_http_url, setup_logging

logger = logging.getLogger(LOGGER_NAME)


def setup_render_device():
    """
    WebKitGTK's DMA-BUF renderer cannot allocate GBM buffers on the NVIDIA
    driver ("Failed to create GBM buffer: Invalid argument"), which leaves the
    webpage blank. When an NVIDIA GPU is present, steer WebKit to a Mesa
    render node instead, or fall back to shared-memory buffers on NVIDIA-only
    systems. Must be called before the first WebView is created.
    """
    overrides = (
        "WEBKIT_WEB_RENDER_DEVICE_FILE",
        "WEBKIT_DMABUF_RENDERER_FORCE_SHM",
        "WEBKIT_DISABLE_DMABUF_RENDERER",
    )
    if any(var in os.environ for var in overrides):
        return
    drivers = {}
    for node in sorted(glob.glob("/dev/dri/renderD*")):
        driver = os.path.realpath(f"/sys/class/drm/{os.path.basename(node)}/device/driver")
        drivers[node] = os.path.basename(driver)
    if "nvidia" not in drivers.values():
        return
    for node, driver in drivers.items():
        if driver != "nvidia":
            logger.info(f"[WebPlayer] Using render device {node} ({driver})")
            os.environ["WEBKIT_WEB_RENDER_DEVICE_FILE"] = node
            return
    logger.info("[WebPlayer] NVIDIA-only system, using SHM buffers for WebKit")
    os.environ["WEBKIT_DMABUF_RENDERER_FORCE_SHM"] = "1"


class WebWindow(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Hardened defaults: a wallpaper must not read local files on behalf
        # of remote pages (nor vice versa). JavaScript stays on: most
        # wallpaper pages need it.
        settings = WebKit2.Settings(
            allow_file_access_from_file_urls=False,
            allow_universal_access_from_file_urls=False,
            enable_media_stream=False,
            enable_webaudio=False,
        )
        self.__webview = WebKit2.WebView.new_with_settings(settings)
        self.add(self.__webview)
        self.__webview.show()

        self.menu = None
        self._is_remote = False
        self._snapshot_cb = None
        self._load_token = 0
        self.__webview.connect("button-press-event", self._on_button_press_event)
        self.__webview.connect("permission-request", self._on_permission_request)
        self.__webview.connect("decide-policy", self._on_decide_policy)
        self.__webview.connect("load-changed", self._on_load_changed)

    @staticmethod
    def _on_permission_request(_webview, request):
        request.deny()
        return True

    def _on_decide_policy(self, _webview, decision, decision_type):
        if decision_type == WebKit2.PolicyDecisionType.NEW_WINDOW_ACTION:
            decision.ignore()
            return True
        if decision_type == WebKit2.PolicyDecisionType.NAVIGATION_ACTION and self._is_remote:
            try:
                uri = decision.get_navigation_action().get_request().get_uri() or ""
            except Exception:  # noqa: BLE001
                uri = ""
            if uri.startswith("file://") or (
                uri.startswith(("http://", "https://")) and not public_http_url(uri)
            ):
                logger.warning("[WebPlayer] Blocked navigation to non-public URI")
                decision.ignore()
                return True
        return False

    def load_uri(self, uri, snapshot_cb=None):
        self._is_remote = uri.startswith(("http://", "https://"))
        self._snapshot_cb = snapshot_cb
        self._load_token += 1
        self.__webview.load_uri(uri)

    def _on_load_changed(self, _webview, load_event):
        if load_event != WebKit2.LoadEvent.FINISHED or self._snapshot_cb is None:
            return
        token = self._load_token
        for delay_ms in (1500, 4500):
            GLib.timeout_add(delay_ms, self._request_snapshot, token)

    def _request_snapshot(self, token):
        if token != self._load_token or self._snapshot_cb is None:
            return False
        try:
            self.__webview.get_snapshot(
                WebKit2.SnapshotRegion.VISIBLE,
                WebKit2.SnapshotOptions.NONE,
                None,
                self._on_snapshot_ready,
                token,
            )
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[WebPlayer] snapshot request failed: {e}")
        return False

    def _on_snapshot_ready(self, webview, result, token):
        if token != self._load_token or self._snapshot_cb is None:
            return
        try:
            surface = webview.get_snapshot_finish(result)
            if surface is None:
                return
            w, h = surface.get_width(), surface.get_height()
            if w <= 0 or h <= 0:
                return
            pixbuf = Gdk.pixbuf_get_from_surface(surface, 0, 0, w, h)
            if pixbuf is not None:
                self._snapshot_cb(pixbuf)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[WebPlayer] snapshot finish failed: {e}")

    def set_is_mute(self, is_mute):
        self.__webview.set_is_muted(is_mute)

    def reload(self):
        self.__webview.reload()

    def _on_button_press_event(self, widget, event):
        if event.type == Gdk.EventType.BUTTON_PRESS and event.button == 3:
            if not self.menu:
                self.menu = build_menu(MODE_WEBPAGE)
            self.menu.popup_at_pointer()
            return True
        return False


class WebPlayer(BasePlayer):
    """
    <node>
    <interface name='io.github.fabx_dev.apricus.player'>
        <property name="mode" type="s" access="read"/>
        <property name="data_source" type="s" access="readwrite"/>
        <property name="volume" type="i" access="readwrite"/>
        <property name="is_mute" type="b" access="readwrite"/>
        <property name="is_playing" type="b" access="read"/>
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
        self.config = None
        self.wallpaper = create_wallpaper_backend()
        self.reload_config()

    @staticmethod
    def _static_cache_path(source):
        digest = hashlib.sha256((source or "").encode("utf-8")).hexdigest()[:16]
        return os.path.join(CONFIG_DIR, f"static-{digest}.png")

    def _save_web_snapshot(self, raw_source, pixbuf):
        try:
            os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
            cached = self._static_cache_path(raw_source)
            tmp = f"{cached}.tmp.png"
            pixbuf.savev(tmp, "png", [], [])
            os.replace(tmp, cached)
            if (
                self.config.get(CONFIG_KEY_STATIC_WALLPAPER, True)
                and self.wallpaper.supports_static
            ):
                self.wallpaper.apply_static(cached)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[WebPlayer] static save failed: {e}")

    def new_window(self, gdk_monitor):
        return WebWindow(application=self)

    def do_activate(self):
        super().do_activate()
        self.data_source = self.config[CONFIG_KEY_DATA_SOURCE]["Default"]

    @property
    def mode(self):
        return self.config[CONFIG_KEY_MODE]

    @property
    def data_source(self):
        return self.config[CONFIG_KEY_DATA_SOURCE]

    @data_source.setter
    def data_source(self, data_source: str):
        self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = data_source
        if self.mode != MODE_WEBPAGE:
            raise ValueError("Invalid mode")

        sources = self.config[CONFIG_KEY_DATA_SOURCE]
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

        for monitor, window in self.windows.items():
            mkey = key_of.get(id(monitor), monitor.get_model())
            raw_source = sources.get(mkey) or sources.get(monitor.get_model()) or data_source
            if not raw_source:
                continue
            source = raw_source
            if source.startswith(("http://", "https://")):
                if not public_http_url(source):
                    logger.warning("[WebPlayer] Refusing non-public URL")
                    continue
            else:
                path_str = source[7:] if source.startswith("file://") else source
                resolved = pathlib.Path(path_str).resolve()
                if not resolved.is_file() or resolved.suffix.lower() not in (".html", ".htm"):
                    logger.warning("[WebPlayer] Refusing non-HTML local file")
                    continue
                source = resolved.as_uri()
            is_primary = monitor.is_primary()
            cb = (
                (lambda pb, src=raw_source: self._save_web_snapshot(src, pb))
                if is_primary
                else None
            )
            window.load_uri(source, snapshot_cb=cb)
            if not is_primary:
                window.set_is_mute(True)
            elif (
                self.config.get(CONFIG_KEY_STATIC_WALLPAPER, True)
                and self.wallpaper.supports_static
            ):
                cached = self._static_cache_path(raw_source)
                if os.path.isfile(cached):
                    self.wallpaper.apply_static(cached)
        self.volume = self.config[CONFIG_KEY_VOLUME]
        self.is_mute = self.config[CONFIG_KEY_MUTE]

    @property
    def volume(self):
        return self.config[CONFIG_KEY_VOLUME]

    @volume.setter
    def volume(self, volume):
        # TODO: How to set volume of webview?
        self.config[CONFIG_KEY_VOLUME] = volume

    @property
    def is_mute(self):
        return self.config[CONFIG_KEY_MUTE]

    @is_mute.setter
    def is_mute(self, is_mute):
        self.config[CONFIG_KEY_MUTE] = is_mute
        for monitor, window in self.windows.items():
            if monitor.is_primary():
                window.set_is_mute(is_mute)

    @property
    def is_playing(self):
        return True

    def pause_playback(self, dbus_context=None):
        _authorize("player.pause_playback", dbus_context)

    def start_playback(self, dbus_context=None):
        _authorize("player.start_playback", dbus_context)

    def reload_config(self, dbus_context=None):
        _authorize("player.reload_config", dbus_context)
        self.config = ConfigUtil().load()


def main():
    setup_logging()
    setup_render_device()
    bus = SessionBus()
    app = WebPlayer()
    try:
        bus.publish(DBUS_NAME_PLAYER, app)
    except RuntimeError as e:
        logger.error(e)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
