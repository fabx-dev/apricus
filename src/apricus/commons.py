import os
import subprocess

LOGGER_NAME = "Apricus"

PROJECT = "io.github.fabx_dev.Apricus"
# Flatpak allows dashes only in the last ID segment, and D-Bus domain
# identifiers (bus names, interfaces, Gtk app IDs, GSettings schema) forbid
# them entirely: the underscore variant is used for every formal ID.
DBUS_PROJECT = PROJECT
DBUS_NAME_SERVER = f"{DBUS_PROJECT}.server"
DBUS_NAME_PLAYER = f"{DBUS_PROJECT}.player"

# gettext text domain (matches po/meson.build and the installed apricus.mo)
TRANSLATION_DOMAIN = "apricus"

HOME = os.environ.get("HOME")
try:
    xdg_video_dir = subprocess.check_output(
        "xdg-user-dir VIDEOS", shell=True, encoding="UTF-8"
    ).replace("\n", "")
    # Kept from Hidamari for compatibility (existing video libraries stay put).
    VIDEO_WALLPAPER_DIR = os.path.join(xdg_video_dir, "Hidamari")
except FileNotFoundError:
    # xdg-user-dir not found, use $HOME/Hidamari for Video directory instead
    VIDEO_WALLPAPER_DIR = os.path.join(HOME, "Hidamari")

xdg_config_home = os.environ.get("XDG_CONFIG_HOME", os.path.join(HOME, ".config"))
AUTOSTART_DIR = os.path.join(xdg_config_home, "autostart")
AUTOSTART_DESKTOP_PATH = os.path.join(AUTOSTART_DIR, f"{PROJECT}.desktop")
AUTOSTART_DESKTOP_CONTENT = """[Desktop Entry]
Name=Apricus
Exec=apricus -b
Icon=io.github.fabx_dev.Apricus
Terminal=false
Type=Application
Categories=GTK;Utility;
StartupNotify=false
"""
AUTOSTART_DESKTOP_CONTENT_FLATPAK = """[Desktop Entry]
Name=Apricus
Exec=/usr/bin/flatpak run --command=apricus io.github.fabx_dev.Apricus -b
Icon=io.github.fabx_dev.Apricus
Terminal=false
Type=Application
Categories=GTK;Utility;
StartupNotify=false
X-Flatpak=io.github.fabx_dev.Apricus
"""

CONFIG_DIR = os.path.join(xdg_config_home, "apricus")
# Previous Hidamari install location; imported once on first run.
LEGACY_CONFIG_PATH = os.path.join(xdg_config_home, "hidamari", "config.json")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")

MODE_NULL = "MODE_NULL"
MODE_VIDEO = "MODE_VIDEO"
MODE_STREAM = "MODE_STREAM"
MODE_WEBPAGE = "MODE_WEBPAGE"
MODE_PLAYLIST = "MODE_PLAYLIST"

CONFIG_VERSION = 5
CONFIG_KEY_VERSION = "version"
CONFIG_KEY_MODE = "mode"
CONFIG_KEY_DATA_SOURCE = "data_source"
CONFIG_KEY_MUTE = "is_mute"
CONFIG_KEY_VOLUME = "audio_volume"
CONFIG_KEY_STATIC_WALLPAPER = "is_static_wallpaper"
CONFIG_KEY_BLUR_RADIUS = "static_wallpaper_blur_radius"  # legacy, removed (popped on load)
CONFIG_KEY_PAUSE_WHEN_MAXIMIZED = "is_pause_when_maximized"
CONFIG_KEY_MUTE_WHEN_MAXIMIZED = "is_mute_when_maximized"
CONFIG_KEY_FADE_DURATION_SEC = "fade_duration_sec"
CONFIG_KEY_FADE_INTERVAL = "fade_interval"
CONFIG_KEY_SYSTRAY = "is_show_systray"
CONFIG_KEY_FIRST_TIME = "is_first_time"
CONFIG_KEY_VIDEO_DIR = "video_dir"
CONFIG_KEY_PLAYLIST = "playlist"
CONFIG_KEY_SHUFFLE = "playlist_shuffle"
CONFIG_TEMPLATE = {
    CONFIG_KEY_VERSION: CONFIG_VERSION,
    CONFIG_KEY_MODE: MODE_NULL,
    CONFIG_KEY_DATA_SOURCE: None,
    CONFIG_KEY_MUTE: False,
    CONFIG_KEY_VOLUME: 50,
    CONFIG_KEY_STATIC_WALLPAPER: True,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED: True,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED: False,
    CONFIG_KEY_FADE_DURATION_SEC: 1.5,
    CONFIG_KEY_FADE_INTERVAL: 0.1,
    CONFIG_KEY_SYSTRAY: False,
    CONFIG_KEY_FIRST_TIME: True,
    # Custom video library folder chosen by the user. None means the default
    # location (~/Video(s)/Hidamari, kept from Hidamari for compatibility).
    CONFIG_KEY_VIDEO_DIR: None,
    # Per-monitor video queues ({monitor_key: [paths]}); empty queue falls
    # back to "Default". Shuffled order is computed at apply time.
    CONFIG_KEY_PLAYLIST: None,
    CONFIG_KEY_SHUFFLE: False,
}


# initialize config according to monitors.
# Must not require a display at import time (headless tools/tests import
# this module); missing monitors are backfilled by _checkMissingMonitors.
# The monitor import is lazy so GTK4 processes can import commons without
# pinning the Gdk 3.0 namespace.
def _default_data_sources():
    from apricus.monitor import MonitorInfo

    data_sources = {}
    try:
        for monitor in MonitorInfo().monitors():
            data_sources[monitor["key"]] = ""
    except Exception:
        pass
    data_sources["Default"] = ""
    return data_sources


CONFIG_TEMPLATE[CONFIG_KEY_DATA_SOURCE] = _default_data_sources()


def _default_playlist():
    from apricus.monitor import MonitorInfo

    playlist = {}
    try:
        for monitor in MonitorInfo().monitors():
            playlist[monitor["key"]] = []
    except Exception:
        pass
    playlist["Default"] = []
    return playlist


CONFIG_TEMPLATE[CONFIG_KEY_PLAYLIST] = _default_playlist()


def resolve_video_dir(config=None):
    """Return the effective video library folder, or None if none chosen.

    Priority: the user-configured folder, then the legacy Hidamari folder
    (compatibility for existing libraries). New users with neither get
    None and the GUI asks them to choose. Never creates directories.
    """
    configured = (config or {}).get(CONFIG_KEY_VIDEO_DIR)
    if configured and os.path.isdir(configured):
        return os.path.abspath(configured)
    if os.path.isdir(VIDEO_WALLPAPER_DIR):
        return VIDEO_WALLPAPER_DIR
    return None
