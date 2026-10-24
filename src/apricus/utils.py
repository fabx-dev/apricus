import gettext
import json
import locale
import logging
import os
import shutil
import subprocess
import sys
from pprint import pformat

import gi
import pydbus
from gi.repository import Gio, GLib

_Wnck = None


def wnck():
    """Lazily loaded Wnck (X11-only): importing utils must never require a
    display server or the Wnck typelib (headless tools, tests, future
    Wayland-native processes)."""
    global _Wnck
    if _Wnck is None:
        gi.require_version("Wnck", "3.0")
        from gi.repository import Wnck

        _Wnck = Wnck
    return _Wnck


from apricus.commons import (
    AUTOSTART_DESKTOP_CONTENT,
    AUTOSTART_DESKTOP_CONTENT_FLATPAK,
    AUTOSTART_DESKTOP_CONTENT_SNAP,
    AUTOSTART_DESKTOP_PATH,
    AUTOSTART_DIR,
    CONFIG_DIR,
    CONFIG_KEY_BLUR_RADIUS,
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_FADE_DURATION_SEC,
    CONFIG_KEY_FADE_INTERVAL,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED,
    CONFIG_KEY_PLAYLIST,
    CONFIG_KEY_PLAYLIST_POS,
    CONFIG_KEY_RENDERER,
    CONFIG_KEY_SHUFFLE,
    CONFIG_KEY_STREAM_FAVORITES,
    CONFIG_KEY_STREAM_HISTORY,
    CONFIG_KEY_VIDEO_DIR,
    CONFIG_KEY_VOLUME,
    CONFIG_KEY_WEB_FAVORITES,
    CONFIG_KEY_WEB_HISTORY,
    CONFIG_PATH,
    CONFIG_TEMPLATE,
    CONFIG_VERSION,
    LEGACY_CONFIG_PATH,
    LOGGER_NAME,
    MODE_VIDEO,
    RENDERER_AUTO,
    TRANSLATION_DOMAIN,
    VALID_RENDERERS,
    ensure_template_monitors,
)

logger = logging.getLogger(LOGGER_NAME)


def _caller_uid(sender):
    """UID owning a bus name, via the daemon (SO_PEERCRED, not spoofable)."""
    bus = pydbus.SessionBus()
    daemon = bus.get("org.freedesktop.DBus")
    return daemon.GetConnectionUnixUser(sender)


def _authorize(action, dbus_context=None):
    """Log the caller of a mutating method; reject foreign UIDs.

    dbus_context is None on direct (non-D-Bus) calls: trusted.
    Property writes go through pydbus internals without context and are
    intentionally out of scope (documented limitation).
    """
    if dbus_context is None:
        return
    sender = dbus_context.sender
    try:
        uid = _caller_uid(sender)
    except Exception as e:
        raise PermissionError(f"{action}: cannot identify caller {sender}: {e}") from e
    logger.info(f"[Server] {action} from {sender} (uid={uid})")
    if uid != os.getuid():
        raise PermissionError(f"{action}: foreign uid {uid} rejected")


def setup_logging():
    """Configure logging in forkserver-spawned children (player/gui/systray).

    The parent configures logging in `__main__`, but forkserver children are
    fresh interpreters: without this, all their INFO/DEBUG logs are silently
    dropped and only WARNING+ surfaces via the last-resort handler. Debug
    mode propagates through the APRICUS_DEBUG env var (see `__main__`).
    """
    level = logging.DEBUG if os.environ.get("APRICUS_DEBUG") == "1" else logging.INFO
    logging.basicConfig(level=level)


def init_translations(localedir):
    """Bind the gettext text domain for the current process.

    The forkserver children (GUI, systray) don't inherit the launcher's gettext
    setup, so each entry point binds it itself. We bind at both the C-library
    level (for GtkBuilder/.ui strings) and the Python level (for _()); missing
    catalogs simply fall back to the original strings, so this can't crash.
    """
    try:
        locale.bindtextdomain(TRANSLATION_DOMAIN, localedir)
        locale.textdomain(TRANSLATION_DOMAIN)
    except (AttributeError, OSError) as e:
        logger.debug("[i18n] C locale bind skipped: %s", e)
    gettext.bindtextdomain(TRANSLATION_DOMAIN, localedir)
    gettext.textdomain(TRANSLATION_DOMAIN)


def is_gnome():
    """
    Check if current DE is GNOME or not.
    On Ubuntu 20.04, $XDG_CURRENT_DESKTOP = ubuntu:GNOME
    On Fedora 34, $XDG_CURRENT_DESKTOP = GNOME
    Hence we do the detection by looking for the word "gnome"
    """
    return "gnome" in str(os.environ.get("XDG_CURRENT_DESKTOP") or "").lower()


def is_wayland():
    """
    Check if current session is Wayland or not.
    $XDG_SESSION_TYPE = x11 | wayland
    """
    return os.environ.get("XDG_SESSION_TYPE") == "wayland"


def is_flatpak():
    """
    Check if Apricus is running as a Flatpak
    Reference:
    https://gitlab.gnome.org/jrb/crosswords/-/blob/master/src/crosswords-init.c#L179
    """
    return os.path.isfile("/.flatpak-info")


def is_snap():
    """Check if Apricus is running inside a Snap package."""
    return bool(os.environ.get("SNAP"))


def valid_http_url(url):
    """Return a stripped http(s) URL, or None when it must not be fetched.

    Minimal SSRF gate for user-supplied and metadata-supplied URLs: only
    http/https, a real hostname, no embedded credentials. Redirect targets
    and DNS rebinding are NOT resolved here (same-user desktop threat
    model); callers keep timeouts and size caps, and validation probes
    re-check the landed URL.
    """
    if not url or not isinstance(url, str):
        return None
    url = url.strip()
    try:
        from urllib.parse import urlsplit
    except ImportError:  # pragma: no cover - stdlib always present
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    if not parts.hostname:
        return None
    if parts.username or parts.password or "@" in (parts.netloc or ""):
        return None
    return url


def resolves_public(hostname):
    """True when *hostname* resolves only to public IP addresses.

    Returns False for loopback, private, link-local, multicast, reserved
    and unspecified addresses, and when DNS resolution fails (fail closed:
    a name that cannot be resolved cannot be fetched either). Used to keep
    validation probes and metadata fetches off the local network; it does
    not cover DNS rebinding between check and use (documented residual).
    """
    import ipaddress
    import socket

    if not hostname:
        return False
    try:
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC)
    except OSError:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_multicast
            or ip.is_reserved
            or ip.is_unspecified
        ):
            return False
    return True


def public_http_url(url):
    """Return *url* when it is fetchable http(s) on a public host, else None.

    Combines :func:`valid_http_url` with :func:`resolves_public`. Local
    development and intranet hosts (including `localhost`) are rejected:
    streaming from the local network is intentionally unsupported because
    pasted URLs are resolved and probed automatically (SSRF). Callers
    surface a dedicated message for this case.
    """
    from urllib.parse import urlsplit

    clean = valid_http_url(url)
    if clean is None:
        return None
    try:
        host = urlsplit(clean).hostname
    except ValueError:
        return None
    if not resolves_public(host):
        return None
    return clean


def fetch_public(url, timeout, max_bytes, max_redirects=3):
    """GET *url*, following at most *max_redirects* hops, every hop public.

    Returns (data, status, content_type, reason); reason is 'ok', 'timeout',
    'connection', 'error', 'refused' or 'too_large'. Never raises.

    Redirects are followed manually with ``allow_redirects=False`` and each
    hop is re-validated *before* any byte is read from it, so the HTTP
    library never touches a forbidden host — a post-hoc check of the final
    URL would still have downloaded from it. Bodies are capped
    incrementally like the row-thumbnail path.

    Residual limits (explicit, not solved here): DNS is resolved per hop,
    so rebinding between check and fetch is possible; libraries that
    follow redirects internally (yt-dlp/FFmpeg/VLC) are NOT mediated —
    gate their entry URLs with :func:`public_http_url` instead.
    """
    from urllib.parse import urljoin

    import requests

    current = url
    hops = 0
    while True:
        if public_http_url(current) is None:
            return None, 0, "", "refused"
        try:
            response = requests.get(current, timeout=timeout, stream=True, allow_redirects=False)
        except requests.exceptions.Timeout:
            return None, 0, "", "timeout"
        except requests.exceptions.ConnectionError:
            return None, 0, "", "connection"
        except requests.exceptions.RequestException:
            return None, 0, "", "error"
        try:
            location = response.headers.get("Location", "")
            if response.status_code in (301, 302, 303, 307, 308) and location:
                hops += 1
                if hops > max_redirects:
                    return None, 0, "", "refused"
                current = urljoin(current, location)
                continue
            status = response.status_code
            content_type = response.headers.get("Content-Type", "")
            chunks = []
            total = 0
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > max_bytes:
                    return None, 0, "", "too_large"
                chunks.append(chunk)
            return b"".join(chunks), status, content_type, "ok"
        finally:
            response.close()


def setup_autostart(autostart):
    if is_flatpak():
        """
        Use portal to autostart for Flatpak
        Documentation:
        https://libportal.org/method.Portal.request_background.html
        https://libportal.org/method.Portal.request_background_finish.html
        """

        gi.require_version("Xdp", "1.0")
        from gi.repository import Xdp

        xdp = Xdp.Portal.new()

        # Request Autostart
        xdp.request_background(
            None,  # parent
            "Autostart Apricus in background",  # reason
            ["apricus", "-b"],  # commandline
            Xdp.BackgroundFlags.AUTOSTART if autostart else Xdp.BackgroundFlags.NONE,  # flags
            None,  # cancellable
            lambda portal, result, user_data: logger.debug(
                f"[Utils] autostart={autostart}, request_background sucess={portal.request_background_finish(result)}"
            ),  # callback
            None,  # user_data
        )

    os.makedirs(AUTOSTART_DIR, exist_ok=True)
    logger.debug(f"[Utils] autostart={autostart}, path={AUTOSTART_DESKTOP_PATH}")
    if autostart:
        with open(AUTOSTART_DESKTOP_PATH, mode="w") as f:
            if is_flatpak():
                # Write files to the sandbox as well, for the following reasons:
                # (1) So that we know if autostart is enabled by looking the file in sandbox
                # (2) Acts as a fallback in case the portal doesn't work
                f.write(AUTOSTART_DESKTOP_CONTENT_FLATPAK)
            elif is_snap():
                f.write(AUTOSTART_DESKTOP_CONTENT_SNAP)
            else:
                f.write(AUTOSTART_DESKTOP_CONTENT)
    else:
        if os.path.isfile(AUTOSTART_DESKTOP_PATH):
            os.remove(AUTOSTART_DESKTOP_PATH)


SYSTEMD_UNIT_NAME = "apricus.service"


def _systemd_user_dir():
    return os.path.join(
        os.environ.get("XDG_CONFIG_HOME", os.path.join(os.path.expanduser("~"), ".config")),
        "systemd",
        "user",
    )


def systemd_unit_content():
    """User unit for login autostart (opt-in alternative to .desktop).

    Ordered after graphical-session, restarted on failure, niced down so
    login stays responsive. The launcher command is resolved for the
    current install: inside a venv (uv dev install) the venv python is
    used explicitly, because the installed script's system-python shebang
    may lack venv-only deps.
    """
    script = shutil.which("apricus") or os.path.join(
        os.path.expanduser("~"), ".local", "bin", "apricus"
    )
    if sys.prefix != getattr(sys, "base_prefix", sys.prefix):
        exec_start = f"{sys.executable} {script} --fast -b"
    else:
        exec_start = f"{script} --fast -b"
    return f"""[Unit]
Description=Apricus live wallpaper
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart={exec_start}
Restart=on-failure
RestartSec=5
Nice=10
IOSchedulingClass=best-effort
IOSchedulingPriority=7

[Install]
WantedBy=graphical-session.target
"""


def setup_systemd_autostart(enable):
    """Install/enable or disable/remove the user systemd autostart unit.

    Returns True on success. Never raises.
    """
    unit_path = os.path.join(_systemd_user_dir(), SYSTEMD_UNIT_NAME)
    try:
        if enable:
            os.makedirs(_systemd_user_dir(), exist_ok=True)
            with open(unit_path, mode="w") as f:
                f.write(systemd_unit_content())
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, timeout=15)
            subprocess.run(
                ["systemctl", "--user", "enable", "--now", SYSTEMD_UNIT_NAME],
                check=True,
                timeout=15,
            )
            logger.info(f"[Utils] systemd autostart enabled ({unit_path})")
        else:
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", SYSTEMD_UNIT_NAME],
                check=False,
                timeout=15,
            )
            if os.path.isfile(unit_path):
                os.remove(unit_path)
            subprocess.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=15)
            logger.info("[Utils] systemd autostart disabled")
        return True
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        logger.warning(f"[Utils] systemd autostart failed: {e}")
        return False


def get_video_paths(video_dir=None):
    file_list = []
    if not video_dir or not os.path.isdir(video_dir):
        if video_dir:
            logger.warning(f"[Utils] Video folder missing: {video_dir}")
        return []
    directory = video_dir
    # Flat scan only: subdirectories are ignored on purpose, so choosing a
    # parent folder shows exactly its own videos and nothing else.
    for filename in os.listdir(directory):
        filepath = os.path.join(directory, filename)
        try:
            file = Gio.file_new_for_path(filepath)
            info = file.query_info(
                "standard::fast-content-type,standard::content-type",
                Gio.FileQueryInfoFlags.NONE,
                None,
            )
            fast_fn = getattr(info, "get_attribute_string", None)
            mime_type = (
                fast_fn("standard::fast-content-type") if fast_fn else None
            ) or info.get_content_type()
        except GLib.Error as e:
            logger.debug(f"[Utils] Skipped {filename}: {e}")
            continue
        if mime_type and "video" in mime_type:
            file_list.append(filepath)
    return sorted(file_list)


"""
GNOME extension utils
"""


def gnome_extension_is_enabled(extension_name: str):
    gnome_ext = pydbus.SessionBus().get("org.gnome.Shell.Extensions")
    info: dict = gnome_ext.GetExtensionInfo(extension_name)
    return info["state"] == 1  # ENABLE = 1


def gnome_extension_set_enable(extension_name: str):
    gnome_ext = pydbus.SessionBus().get("org.gnome.Shell.Extensions")
    success: bool = gnome_ext.EnableExtension(extension_name)
    return success


def gnome_extension_set_disable(extension_name: str):
    gnome_ext = pydbus.SessionBus().get("org.gnome.Shell.Extensions")
    success: bool = gnome_ext.DisableExtension(extension_name)
    return success


def gnome_extension_is_installed(extension_name: str):
    gnome_ext = pydbus.SessionBus().get("org.gnome.Shell.Extensions")
    installed: dict = gnome_ext.ListExtensions()
    return extension_name in installed.keys()


def gnome_desktop_icon_workaround():
    """
    Workaround for GNOME desktop icon extensions not displaying the icons on top of Apricus.
    Call this right after the wallpaper is shown.
    """
    if not is_gnome() or is_snap():
        return
    extension_list = [
        "ding@rastersoft.com",
        "desktopicons-neo@darkdemon",
        "gtk4-ding@smedius.gitlab.com",
        "zorin-desktop-icons@zorinos.com",
    ]
    for ext in extension_list:
        try:
            # Check if installed and enabled
            if gnome_extension_is_installed(ext) and gnome_extension_is_enabled(ext):
                # Reload the extension
                logger.info(f"[Utils] Apply workaround for {ext}")
                gnome_extension_set_disable(ext)
                gnome_extension_set_enable(ext)
        except GLib.Error as e:
            logger.warning(f"[Utils] Desktop icon workaround failed for {ext}: {e}")


"""
Handlers
"""


class ActiveHandler:
    """
    Handler for monitoring screen lock
    GNOME:
    https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/main/data/dbus-interfaces/org.gnome.ScreenSaver.xml
    Cinamon:
    https://github.com/linuxmint/cinnamon-screensaver/blob/master/libcscreensaver/org.cinnamon.ScreenSaver.xml
    Freedesktop:
    https://github.com/KDE/kscreenlocker/blob/master/dbus/org.freedesktop.ScreenSaver.xml
    """

    def __init__(self, on_active_changed: callable):
        self.session_bus = pydbus.SessionBus()
        self.proxies = []
        self.signal_subscriptions = []

        screensaver_list = [
            "org.gnome.ScreenSaver",
            "org.cinnamon.ScreenSaver",
            "org.freedesktop.ScreenSaver",
        ]
        for s in screensaver_list:
            try:
                proxy = self.session_bus.get(s)
                # Store proxy reference to prevent garbage collection
                self.proxies.append(proxy)
                subscription = proxy.ActiveChanged.connect(on_active_changed)
                self.signal_subscriptions.append((proxy, subscription))
            except GLib.Error:
                pass

    def cleanup(self):
        """Cleanup signal subscriptions"""
        # pydbus has no disconnect; connections drop when the proxies are GC'd
        self.signal_subscriptions.clear()
        self.proxies.clear()


class EndSessionHandler:
    """
    Handler for monitoring end session
    References:
    https://github.com/backloop/gendsession

    PrepareForShutdown() signal from logind is not handled
    https://gitlab.gnome.org/GNOME/gnome-shell/-/issues/787
    """

    def __init__(self, on_end_session: callable):
        self.on_end_session = on_end_session
        self.session_client = None
        if is_snap():
            return
        try:
            if is_gnome():
                session_bus = pydbus.SessionBus()
                proxy = session_bus.get("org.gnome.SessionManager")
                client_id = proxy.RegisterClient("", "")
                self.session_client = session_bus.get("org.gnome.SessionManager", client_id)
                self.session_client.QueryEndSession.connect(self.__query_end_session_handler_gnome)
                self.session_client.EndSession.connect(self.__end_session_handler_gnome)
            else:
                system_bus = pydbus.SystemBus()
                proxy = system_bus.get(".login1")
                proxy.PrepareForShutdown.connect(self.__end_session_handler)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[EndSessionHandler] unavailable in this sandbox: {e}")

    def __end_session_response_gnome(self, ok=True):
        if ok:
            self.session_client.EndSessionResponse(True, "")
        else:
            self.session_client.EndSessionResponse(False, "Not ready")

    def __query_end_session_handler_gnome(self, flags):
        # Ignore flags, always agree on the QueryEndSesion
        self.__end_session_response_gnome(True)

    def __end_session_handler_gnome(self, flags):
        logger.debug("[EndSessionHandler] called")
        self.on_end_session()
        self.__end_session_response_gnome(True)

    def __end_session_handler(self, *_args):
        logger.debug("[EndSessionHandler] called")
        self.on_end_session()


class WindowHandler:
    """
    Handler for monitoring window events (maximized and fullscreen mode) for X11
    """

    def __init__(self, on_window_state_changed: callable):
        self.on_window_state_changed = on_window_state_changed
        self.screen = wnck().Screen.get_default()
        self.screen.force_update()

        # Store signal handler IDs for cleanup
        self.signal_handlers = []
        self.window_signal_handlers = {}

        # Connect screen signals and store handler IDs
        handler_id = self.screen.connect("window-opened", self.window_opened, None)
        self.signal_handlers.append((self.screen, handler_id))

        handler_id = self.screen.connect("window-closed", self.eval, None)
        self.signal_handlers.append((self.screen, handler_id))

        handler_id = self.screen.connect("active-workspace-changed", self.eval, None)
        self.signal_handlers.append((self.screen, handler_id))

        # Connect to existing windows
        for window in self.screen.get_windows():
            self._connect_window(window)

        self.prev_state = None
        # Initial check
        self.eval()

    def _connect_window(self, window):
        """Connect to a window and store the handler ID"""
        if window not in self.window_signal_handlers:
            handler_id = window.connect("state-changed", self.eval, None)
            self.window_signal_handlers[window] = handler_id

    def window_opened(self, screen, window, _):
        self._connect_window(window)

    def eval(self, *args):
        # TODO: #28 (Wallpaper stops animating on other monitor when app maximized on other)
        is_changed = False

        is_any_maximized, is_any_fullscreen = False, False
        W = wnck().Window
        for window in self.screen.get_windows():
            base_state = not W.is_minimized(window) and W.is_on_workspace(
                window, self.screen.get_active_workspace()
            )
            is_maximized = W.is_maximized(window) and base_state
            is_fullscreen = W.is_fullscreen(window) and base_state
            if is_maximized is True:
                is_any_maximized = True
            if is_fullscreen is True:
                is_any_fullscreen = True

        cur_state = {"is_any_maximized": is_any_maximized, "is_any_fullscreen": is_any_fullscreen}
        if self.prev_state is None or self.prev_state != cur_state:
            is_changed = True
            self.prev_state = cur_state

        if is_changed:
            self.on_window_state_changed(
                {"is_any_maximized": is_any_maximized, "is_any_fullscreen": is_any_fullscreen}
            )
            logger.debug(f"[WindowHandler] {cur_state}")

    def cleanup(self):
        """Cleanup all signal handlers to prevent memory leaks"""
        # Disconnect screen signals
        for obj, handler_id in self.signal_handlers:
            try:
                obj.disconnect(handler_id)
            except Exception as e:
                logger.warning(f"[WindowHandler] Error disconnecting screen signal: {e}")
        self.signal_handlers.clear()

        # Disconnect window signals
        for window, handler_id in self.window_signal_handlers.items():
            try:
                window.disconnect(handler_id)
            except Exception as e:
                logger.warning(f"[WindowHandler] Error disconnecting window signal: {e}")
        self.window_signal_handlers.clear()


class ConfigUtil:
    def generate_template(self):
        ensure_template_monitors()
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.save(CONFIG_TEMPLATE)

    @staticmethod
    def _check(config: dict):
        """Check if the config is valid"""
        is_all_keys_match = all(key in config for key in CONFIG_TEMPLATE)
        is_version_match = config.get("version") == CONFIG_VERSION
        return is_all_keys_match and is_version_match

    def _invalid(self):
        logger.debug("[Config] Invalid. A new config will be generated.")
        self.generate_template()
        return CONFIG_TEMPLATE

    def _migrateV3To4(self, config: dict):
        logger.debug("[Config] Migration from version 3 to 4.")
        curr_data_source = config["data_source"]
        config["data_source"] = CONFIG_TEMPLATE[CONFIG_KEY_DATA_SOURCE]
        config["data_source"]["Default"] = curr_data_source
        config["is_pause_when_maximized"] = config["is_detect_maximized"]
        del config["is_detect_maximized"]
        config["is_mute_when_maximized"] = CONFIG_TEMPLATE[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]
        config["version"] = 4
        # save config file
        self.save(config)

    def _migrateV4To5(self, config: dict):
        logger.debug("[Config] Migration from version 4 to 5.")
        config[CONFIG_KEY_PLAYLIST] = dict(CONFIG_TEMPLATE[CONFIG_KEY_PLAYLIST])
        config[CONFIG_KEY_SHUFFLE] = False
        config["version"] = 5
        self.save(config)

    def _ensurePlaylistKeys(self, config: dict):
        """Backfill playlist queues for monitors missing one (same policy as
        _checkMissingMonitors, but queues default to [] not Default)."""
        playlist = config.get(CONFIG_KEY_PLAYLIST)
        if not isinstance(playlist, dict):
            return
        playlist.setdefault("Default", [])
        for key in CONFIG_TEMPLATE[CONFIG_KEY_PLAYLIST]:
            playlist.setdefault(key, [])

    @staticmethod
    def _sanitize_stream_entries(entries):
        """Keep at most 30 well-formed history entries, newest first."""
        clean = []
        seen = set()
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not isinstance(url, str) or not url.startswith(("http://", "https://")):
                continue
            if len(url) > 2048 or url in seen:
                continue
            seen.add(url)
            clean.append(
                {
                    "url": url,
                    "title": entry.get("title") or "",
                    "site": entry.get("site") or "",
                    "kind": entry.get("kind") or "page",
                    "last_used": entry.get("last_used") or 0,
                    "is_live": bool(entry.get("is_live")),
                }
            )
        clean.sort(key=lambda e: e["last_used"], reverse=True)
        return clean[:30]

    @staticmethod
    def _sanitize_web_entries(entries):
        """Keep at most 30 well-formed web entries (http URLs or .html paths)."""
        clean = []
        seen = set()
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            url = entry.get("url")
            if not isinstance(url, str):
                continue
            is_remote = url.startswith(("http://", "https://"))
            is_file = os.path.isabs(url) and url.lower().endswith((".html", ".htm"))
            if not (is_remote or is_file):
                continue
            if len(url) > 2048 or url in seen:
                continue
            seen.add(url)
            clean.append(
                {
                    "url": url,
                    "title": entry.get("title") or "",
                    "site": entry.get("site") or "",
                    "last_used": entry.get("last_used") or 0,
                }
            )
        clean.sort(key=lambda e: e["last_used"], reverse=True)
        return clean[:30]

    def _migrateLegacyMonitorKeys(self, config: dict):
        """Move per-monitor sources keyed by bare model to stable keys.

        Unique models keep the bare model (no-op). Duplicate models now use
        `Model#i`: copy the legacy value to every new key missing one, then
        drop the legacy entry. `Default` is never touched.
        """
        from apricus.monitor import MonitorInfo

        data_source = config.get(CONFIG_KEY_DATA_SOURCE, {})
        if not isinstance(data_source, dict):
            return
        infos = MonitorInfo.monitors()
        current_keys = {info["key"] for info in infos}
        moved = []
        touched = set()
        for info in infos:
            model, key = info["name"], info["key"]
            if model == key:
                continue
            if (key not in data_source or not data_source[key]) and data_source.get(model):
                data_source[key] = data_source[model]
                moved.append(key)
                touched.add(model)
        for model in touched:
            if model not in current_keys and model in data_source:
                del data_source[model]
        if moved:
            logger.debug(f"[Config] Migrated legacy monitor keys {sorted(touched)} to {moved}")
            self.save(config)

    def _checkMissingMonitors(self, old_config: dict, template: dict):
        # Extract the monitors from both configurations
        old_monitors = old_config.get("data_source", {}).keys()
        template_monitors = template.get("data_source", {}).keys()
        # Find monitors in the template that are not in the old configuration
        missing_monitors = set(template_monitors) - set(old_monitors)
        if len(missing_monitors) > 0:
            logger.warning(
                f"[Config] There are missing {len(missing_monitors)} monitors in config. Creating default one"
            )
            self._createMissingMonitors(missing_monitors, old_config)

    def _createMissingMonitors(self, keys: set, config: dict):
        # we will set to Default new monitor sources
        for key in keys:
            config["data_source"][key] = config["data_source"]["Default"]
        self.save(config)

    def _checkDefaultSource(self, config: dict):
        # Check if the 'Default' source is empty
        default_source = config["data_source"].get("Default", "")
        mode = config.get("mode")
        if mode == MODE_VIDEO and not os.path.isfile(default_source):
            logger.warning(
                "[Config] Default source is empty or not a valid file. Setting to the first on available."
            )

            # Get all values from the 'data_source' dictionary
            values = list(config["data_source"].values())
            # If there are no values in 'data_source', return early
            if not values:
                return

            # Set the 'Default' source to the first value available
            for value in values:
                if len(value) > 0 and os.path.isfile(value):
                    config["data_source"]["Default"] = value
                    self.save(config)
                    break

    def load(self):
        # Seed per-monitor template keys for this process (toolkit already
        # committed here; never at import — see ensure_template_monitors).
        ensure_template_monitors()
        if not os.path.isfile(CONFIG_PATH) and os.path.isfile(LEGACY_CONFIG_PATH):
            # First run after migrating from Hidamari: adopt its config so the
            # setup (videos, volume, flags) carries over untouched.
            try:
                os.makedirs(CONFIG_DIR, exist_ok=True)
                with open(LEGACY_CONFIG_PATH, encoding="utf-8") as f:
                    legacy = json.load(f)
                with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                    json.dump(legacy, f, indent=3)
                logger.info(f"[Config] Imported Hidamari config from {LEGACY_CONFIG_PATH}")
            except (OSError, ValueError) as e:
                logger.warning(f"[Config] Legacy import failed: {e}")
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                json_str = f.read()
                try:
                    config = json.loads(json_str)
                    if config.get("version") is None:
                        # Key lost (e.g. written by an older writer): the
                        # shape is current, only the stamp is missing. Stamp
                        # it instead of crashing or mis-migrating.
                        config["version"] = CONFIG_VERSION
                    # Seeded-template values may be absent (e.g. a config
                    # written from an unseeded template): restore them from
                    # the seeded template instead of crashing downstream.
                    if not isinstance(config.get(CONFIG_KEY_DATA_SOURCE), dict):
                        config[CONFIG_KEY_DATA_SOURCE] = dict(
                            CONFIG_TEMPLATE[CONFIG_KEY_DATA_SOURCE]
                        )
                    if not isinstance(config.get(CONFIG_KEY_PLAYLIST), dict):
                        config[CONFIG_KEY_PLAYLIST] = dict(CONFIG_TEMPLATE[CONFIG_KEY_PLAYLIST])
                    # migration to version 4 for data_source type change
                    if config.get("version") <= 3 and CONFIG_VERSION >= 4:
                        self._migrateV3To4(config)
                    # migration to version 5 for playlist queues
                    if config.get("version") == 4 and CONFIG_VERSION >= 5:
                        self._migrateV4To5(config)
                    self._ensurePlaylistKeys(config)
                    # video_dir was added after v4 without a version bump:
                    # default it instead of wiping the whole config.
                    config.setdefault(CONFIG_KEY_VIDEO_DIR, None)
                    # Stored renderer id (same no-bump policy): no UI
                    # exposes it anymore, but old configs may carry it —
                    # sanitize to a known id, defaulting to automatic.
                    if config.get(CONFIG_KEY_RENDERER) not in VALID_RENDERERS:
                        config[CONFIG_KEY_RENDERER] = RENDERER_AUTO
                    # Per-monitor playlist positions: same no-bump backfill,
                    # sanitized to non-negative ints.
                    pos = config.get(CONFIG_KEY_PLAYLIST_POS)
                    if not isinstance(pos, dict):
                        pos = {}
                    config[CONFIG_KEY_PLAYLIST_POS] = {
                        str(k): v
                        for k, v in pos.items()
                        if isinstance(v, int) and not isinstance(v, bool) and v >= 0
                    }
                    # Stream pick-list, same no-bump policy (sanitized).
                    config[CONFIG_KEY_STREAM_HISTORY] = self._sanitize_stream_entries(
                        config.get(CONFIG_KEY_STREAM_HISTORY)
                    )
                    config[CONFIG_KEY_STREAM_FAVORITES] = [
                        u
                        for u in (config.get(CONFIG_KEY_STREAM_FAVORITES) or [])
                        if isinstance(u, str) and u.startswith(("http://", "https://"))
                    ][:50]
                    config[CONFIG_KEY_WEB_HISTORY] = self._sanitize_web_entries(
                        config.get(CONFIG_KEY_WEB_HISTORY)
                    )
                    config[CONFIG_KEY_WEB_FAVORITES] = [
                        u
                        for u in (config.get(CONFIG_KEY_WEB_FAVORITES) or [])
                        if isinstance(u, str)
                        and (
                            u.startswith(("http://", "https://"))
                            or (os.path.isabs(u) and u.lower().endswith((".html", ".htm")))
                        )
                    ][:50]
                    # blur was removed: drop the legacy key, keep the rest.
                    config.pop(CONFIG_KEY_BLUR_RADIUS, None)
                    self._migrateLegacyMonitorKeys(config)
                    self._checkDefaultSource(config)
                    self._checkMissingMonitors(config, CONFIG_TEMPLATE)
                    self._clamp_config(config)
                    if self._check(config):
                        if logger.isEnabledFor(logging.DEBUG):
                            logs = [
                                "--------- Config ---------",
                                pformat(config, indent=3),
                                "--------------------------",
                            ]
                            logger.debug(f"[Config] Loaded {CONFIG_PATH}\n" + "\n".join(logs))
                        return config
                except json.decoder.JSONDecodeError:
                    logger.debug("[Config] JSONDecodeError")
                    # Atomic saves make this rare, but a corrupt file must
                    # never wipe the setup: try the backup generation first.
                    bak_path = CONFIG_PATH + ".bak"
                    if os.path.isfile(bak_path):
                        try:
                            with open(bak_path, encoding="utf-8") as f:
                                config = json.load(f)
                            if self._check(config):
                                logger.info(f"[Config] Recovered from {bak_path}")
                                return config
                        except (OSError, ValueError):
                            pass
        return self._invalid()

    @staticmethod
    def _clamp_config(config):
        """Validate user-editable numerics: a hand-edited config must never
        crash the player (ZeroDivisionError on fade_interval=0, overflow
        volume, negative durations). Repairs in place."""
        try:
            volume = int(config.get(CONFIG_KEY_VOLUME, 50))
        except (TypeError, ValueError):
            volume = 50
        config[CONFIG_KEY_VOLUME] = max(0, min(100, volume))
        try:
            interval = float(config.get(CONFIG_KEY_FADE_INTERVAL, 0.1))
        except (TypeError, ValueError):
            interval = 0.1
        config[CONFIG_KEY_FADE_INTERVAL] = min(1.0, max(0.05, interval)) if interval > 0 else 0.1
        try:
            duration = float(config.get(CONFIG_KEY_FADE_DURATION_SEC, 1.5))
        except (TypeError, ValueError):
            duration = 1.5
        config[CONFIG_KEY_FADE_DURATION_SEC] = min(5.0, max(0.0, duration))
        return config

    def save(self, config):
        old_config = None
        old_raw = None
        if os.path.isfile(CONFIG_PATH):
            with open(CONFIG_PATH) as f:
                json_str = f.read()
                try:
                    old_config = json.loads(json_str)
                    if not self._check(old_config):
                        # Valid JSON but outdated (e.g. pre-backfill): keep
                        # the raw text for backup, so the first save after a
                        # migration still leaves a rollback copy. Recovery
                        # keeps accepting only _check-valid backups.
                        old_raw = json_str
                        old_config = None
                except json.decoder.JSONDecodeError:
                    old_config = None
        # Skip if the config is identical
        if old_config == config:
            return
        # Atomic write: a kill between truncate and content no longer wipes
        # the setup. Keep one backup generation for corrupt-file recovery.
        os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
        bak_path = CONFIG_PATH + ".bak"
        if old_config is not None:
            try:
                with open(bak_path, "w", encoding="utf-8") as f:
                    json.dump(old_config, f, indent=3)
                os.chmod(bak_path, 0o600)
            except OSError as e:
                logger.warning(f"[Config] Backup failed: {e}")
        elif old_raw is not None and old_raw != json.dumps(config, indent=3):
            try:
                with open(bak_path, "w", encoding="utf-8") as f:
                    f.write(old_raw if old_raw.endswith("\n") else old_raw + "\n")
                os.chmod(bak_path, 0o600)
            except OSError as e:
                logger.warning(f"[Config] Backup failed: {e}")
        tmp_path = CONFIG_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json_str = json.dumps(config, indent=3)
            print(json_str, file=f)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, CONFIG_PATH)
        logger.debug(f"[Config] Saved {CONFIG_PATH} (mode={config.get('mode')})")
