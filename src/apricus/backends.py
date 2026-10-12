"""Desktop-environment backends (Fase 2).

All DE-specific behavior lives here behind two small interfaces, so the
players stay DE-agnostic:

- WallpaperBackend: capture/apply/restore the static wallpaper.
- WindowStateBackend: report maximized/fullscreen state changes.

Selection rules (same behavior as Hidamari 3.7, just centralized):

- Wallpaper: GNOME -> GnomeWallpaperBackend, anything else -> Null.
- Window state: X11 -> Wnck (unchanged), Wayland -> GNOME Shell Introspect
  when the compositor allows it (GNOME 50 denies GetWindows even to named
  clients, so this degrades to Null with a clear log instead of crashing).
"""

import logging
import os
import subprocess

from gi.repository import Gio, GLib
from pydbus import SessionBus

from apricus.commons import LOGGER_NAME
from apricus.utils import (
    gnome_desktop_icon_workaround,
    is_flatpak,
    is_gnome,
    is_wayland,
)

logger = logging.getLogger(LOGGER_NAME)


class WallpaperBackend:
    """Static-wallpaper handling for one desktop environment."""

    name = "null"
    supports_static = False

    def capture_original(self):
        """Remember the current wallpaper for later restore."""

    def apply_static(self, image_path):
        """Apply an image file as the system wallpaper."""

    def restore_original(self):
        """Restore the wallpaper captured by capture_original()."""

    def prune_cache(self, keep_paths):
        """Drop stale generated static-wallpaper caches (absolute paths kept)."""

    def raise_desktop_icons(self):
        """Re-assert desktop icons above the wallpaper, if the DE needs it."""


class NullWallpaperBackend(WallpaperBackend):
    pass


class GnomeWallpaperBackend(WallpaperBackend):
    name = "gnome"
    supports_static = True

    def __init__(self, flatpak=None):
        self._flatpak = is_flatpak() if flatpak is None else flatpak
        self.original_wallpaper_uri = None
        self.original_wallpaper_uri_dark = None

    def _settings(self):
        return Gio.Settings.new("org.gnome.desktop.background")

    def capture_original(self):
        self.original_wallpaper_uri = None
        self.original_wallpaper_uri_dark = None
        if self._flatpak:
            try:
                self.original_wallpaper_uri = subprocess.check_output(
                    [
                        "flatpak-spawn",
                        "--host",
                        "gsettings",
                        "get",
                        "org.gnome.desktop.background",
                        "picture-uri",
                    ],
                    shell=False,
                    encoding="UTF-8",
                    timeout=5,
                )
                self.original_wallpaper_uri_dark = subprocess.check_output(
                    [
                        "flatpak-spawn",
                        "--host",
                        "gsettings",
                        "get",
                        "org.gnome.desktop.background",
                        "picture-uri-dark",
                    ],
                    shell=False,
                    encoding="UTF-8",
                    timeout=5,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
                logger.error(f"[StaticWallpaper] {e}")
        else:
            gso = self._settings()
            self.original_wallpaper_uri = gso.get_string("picture-uri")
            self.original_wallpaper_uri_dark = gso.get_string("picture-uri-dark")

    def apply_static(self, image_path):
        from pathlib import Path

        uri = Path(image_path).resolve().as_uri()
        if self._flatpak:
            try:
                for key in ("picture-uri", "picture-uri-dark"):
                    subprocess.run(
                        [
                            "flatpak-spawn",
                            "--host",
                            "gsettings",
                            "set",
                            "org.gnome.desktop.background",
                            key,
                            uri,
                        ],
                        shell=False,
                    )
            except subprocess.CalledProcessError as e:
                logger.error(f"[StaticWallpaper] {e}")
        else:
            gso = self._settings()
            gso.set_string("picture-uri", uri)
            gso.set_string("picture-uri-dark", uri)

    def restore_original(self):
        if self._flatpak:
            try:
                if self.original_wallpaper_uri is not None:
                    self._spawn_set("picture-uri", self.original_wallpaper_uri)
                if self.original_wallpaper_uri_dark is not None:
                    self._spawn_set("picture-uri-dark", self.original_wallpaper_uri_dark)
            except subprocess.CalledProcessError as e:
                logger.error(f"[StaticWallpaper] {e}")
        else:
            gso = self._settings()
            gso.set_string("picture-uri", self.original_wallpaper_uri)
            gso.set_string("picture-uri-dark", self.original_wallpaper_uri_dark)

    def _spawn_set(self, key, value):
        subprocess.run(
            [
                "flatpak-spawn",
                "--host",
                "gsettings",
                "set",
                "org.gnome.desktop.background",
                key,
                value,
            ],
            shell=False,
            check=True,
        )

    def raise_desktop_icons(self):
        gnome_desktop_icon_workaround()

    def prune_cache(self, keep_paths):
        import glob

        from apricus.commons import CONFIG_DIR

        for f in glob.glob(os.path.join(CONFIG_DIR, "static-*.png")):
            if os.path.abspath(f) not in keep_paths:
                try:
                    os.remove(f)
                except OSError as e:
                    logger.warning(f"[StaticWallpaper] {e}")


def create_wallpaper_backend():
    if is_gnome():
        return GnomeWallpaperBackend()
    logger.info("[Backend] No wallpaper backend for this DE (static wallpaper disabled)")
    return NullWallpaperBackend()


class WindowStateBackend:
    """Maximized/fullscreen change notifications for one display server."""

    name = "null"

    def __init__(self, on_window_state_changed: callable):
        self.on_window_state_changed = on_window_state_changed

    def start(self):
        """Begin reporting. Called once the player is fully initialized."""

    def stop(self):
        """Stop reporting and release resources."""


class NullWindowStateBackend(WindowStateBackend):
    pass


class WnckWindowStateBackend(WindowStateBackend):
    """X11 window-state monitoring (unchanged Hidamari behavior)."""

    name = "x11-wnck"

    def start(self):
        from apricus.utils import WindowHandler

        self._handler = WindowHandler(self.on_window_state_changed)

    def stop(self):
        handler, self._handler = getattr(self, "_handler", None), None
        if handler is not None:
            handler.cleanup()


class GnomeShellWindowStateBackend(WindowStateBackend):
    """Wayland window-state via org.gnome.Shell.Introspect.GetWindows.

    The compositor may deny the call (GNOME 50 denies even named clients);
    use try_probe() and fall back to Null when unavailable. State arrives
    by polling (Introspect offers no change signal).
    """

    name = "gnome-shell"
    POLL_SECONDS = 2

    def __init__(self, on_window_state_changed: callable):
        super().__init__(on_window_state_changed)
        self._proxy = None
        self._name_owner = None
        self._source_id = None
        self._prev = None

    @staticmethod
    def parse_state(windows):
        """Collapse Introspect window dicts to (maximized, fullscreen).

        Field names are matched defensively: unknown shapes yield False
        rather than crashing.
        """
        maximized = fullscreen = False
        for w in windows or []:
            try:
                items = dict(w).items()
            except (TypeError, ValueError):
                continue
            flags = {str(k).lower(): v for k, v in items}
            if any(flags.get(k) for k in ("maximized", "is-maximized", "is_maximized")):
                maximized = True
            if any(flags.get(k) for k in ("fullscreen", "is-fullscreen", "is_fullscreen")):
                fullscreen = True
        return {"is_any_maximized": maximized, "is_any_fullscreen": fullscreen}

    @classmethod
    def try_probe(cls, on_window_state_changed: callable):
        """Return a working backend, or None when Introspect is unavailable."""
        try:
            bus = SessionBus()
            owner = bus.request_name(f"io.apricus.Probe{os.getpid()}")
            proxy = bus.get("org.gnome.Shell.Introspect", "/org/gnome/Shell/Introspect")
            proxy.GetWindows()
        except Exception as e:
            logger.warning(
                f"[Backend] GNOME Shell Introspect unavailable ({e}); "
                "window-state detection disabled on Wayland"
            )
            return None
        backend = cls(on_window_state_changed)
        backend._proxy = proxy
        backend._name_owner = owner
        logger.info("[Backend] GNOME Shell Introspect window-state active")
        return backend

    def start(self):
        if self._proxy is None:
            return
        self._poll()
        self._source_id = GLib.timeout_add_seconds(self.POLL_SECONDS, self._poll)

    def _poll(self):
        try:
            state = self.parse_state(self._proxy.GetWindows())
        except Exception as e:
            logger.warning(f"[Backend] Introspect poll failed: {e}")
            return True
        if state != self._prev:
            self._prev = state
            self.on_window_state_changed(state)
            logger.debug(f"[Backend] {state}")
        return True

    def stop(self):
        if self._source_id is not None:
            GLib.source_remove(self._source_id)
            self._source_id = None
        self._proxy = None
        self._name_owner = None


def create_window_state_backend(on_window_state_changed: callable):
    if not is_wayland():
        return WnckWindowStateBackend(on_window_state_changed)
    probed = GnomeShellWindowStateBackend.try_probe(on_window_state_changed)
    if probed is not None:
        return probed
    logger.info("[Backend] No window-state backend on Wayland (auto-pause disabled)")
    return NullWindowStateBackend(on_window_state_changed)
