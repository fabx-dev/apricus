import ctypes
import logging
import multiprocessing as mp
import sys
import time
from abc import abstractmethod

import gi
import setproctitle

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk
from pydbus import SessionBus

from apricus.commons import DBUS_NAME_PLAYER, DBUS_PROJECT, LOGGER_NAME
from apricus.gnome_shell import (
    WALLPAPER_WINDOW_ROLE,
    _shell_settings,
    integration_active,
    log_status,
)
from apricus.utils import _authorize, gnome_desktop_icon_workaround, is_snap

logger = logging.getLogger(LOGGER_NAME)

APP_ID = f"{DBUS_PROJECT}.player"

# EWMH restack message fields: source indication 2 (pager) + detail 1 (Below).
# Mutter honors _NET_RESTACK_WINDOW unconditionally, unlike XLowerWindow
# (ConfigureRequest), which it silently drops when the focused window belongs
# to another client. Used to keep our DESKTOP windows below DING's icons.
_NET_RESTACK_WINDOW_DETAIL_BELOW = 1
_NET_RESTACK_WINDOW_SOURCE_PAGER = 2
_X11_CLIENT_MESSAGE = 33
_X_SUBSTRUCTURE_MASK = (1 << 20) | (1 << 19)

_xlib_handle = None


def _x11_lib():
    """ctypes handle for libX11, cached, with all signatures declared.

    None when unavailable. Signatures are applied at load time so no
    call site can pass a truncated pointer (segfault, uncatchable).
    """
    global _xlib_handle
    if _xlib_handle is None:
        for lib in ("libX11.so.6", "libX11.so"):
            try:
                handle = ctypes.cdll.LoadLibrary(lib)
            except OSError:
                continue
            for name, (argtypes, restype) in _X11_FUNCS.items():
                try:
                    func = getattr(handle, name)
                except AttributeError:
                    continue
                func.argtypes = argtypes
                func.restype = restype
            _xlib_handle = handle
            break
    return _xlib_handle


def _is_x11_display():
    """True only on an X11/XWayland backend (layer-shell Wayland skips this)."""
    try:
        from gi.repository import GdkX11

        return isinstance(Gdk.Display.get_default(), GdkX11.X11Display)
    except Exception:  # noqa: BLE001 - no X11 backend, nothing to restack
        return False


class _XClientMessageEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("message_type", ctypes.c_ulong),
        ("format", ctypes.c_int),
        ("data", ctypes.c_long * 5),
    ]


class _XPropertyEvent(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.c_int),
        ("serial", ctypes.c_ulong),
        ("send_event", ctypes.c_int),
        ("display", ctypes.c_void_p),
        ("window", ctypes.c_ulong),
        ("atom", ctypes.c_ulong),
        ("time", ctypes.c_ulong),
        ("state", ctypes.c_int),
        ("pad", ctypes.c_byte * 192),
    ]


# Exact ctypes signatures for every libX11 entry point we call.
# Declared once, applied in _x11_lib(): without explicit argtypes,
# ctypes truncates 64-bit pointers to C int and libX11 segfaults
# (fatal, uncatchable — observed in the Flatpak sandbox).
_X11_FUNCS = {
    "XOpenDisplay": ([ctypes.c_char_p], ctypes.c_void_p),
    "XInternAtom": (
        [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int],
        ctypes.c_ulong,
    ),
    "XDefaultRootWindow": ([ctypes.c_void_p], ctypes.c_ulong),
    "XSendEvent": (
        [
            ctypes.c_void_p,
            ctypes.c_ulong,
            ctypes.c_int,
            ctypes.c_long,
            ctypes.POINTER(_XClientMessageEvent),
        ],
        ctypes.c_int,
    ),
    "XFlush": ([ctypes.c_void_p], ctypes.c_int),
    "XCloseDisplay": ([ctypes.c_void_p], ctypes.c_int),
}


def _x11_restack_below(xid):
    """Ask the WM (EWMH) to place window *xid* below its siblings.

    Unlike gdk_win.lower() this is honored by Mutter even when another
    client's window has focus. Best-effort: False when X11 is unavailable.
    """
    if not xid or not _is_x11_display():
        return False
    lib = _x11_lib()
    if lib is None:
        return False
    try:
        dpy = lib.XOpenDisplay(None)
        if not dpy:
            return False
        try:
            msgtype = lib.XInternAtom(dpy, b"_NET_RESTACK_WINDOW", False)
            root = lib.XDefaultRootWindow(dpy)
            ev = _XClientMessageEvent()
            ev.type = _X11_CLIENT_MESSAGE
            ev.send_event = 1
            ev.window = xid
            ev.message_type = msgtype
            ev.format = 32
            ev.data[0] = _NET_RESTACK_WINDOW_SOURCE_PAGER
            ev.data[1] = 0
            ev.data[2] = _NET_RESTACK_WINDOW_DETAIL_BELOW
            ev.data[3] = 0
            ev.data[4] = 0
            lib.XSendEvent(dpy, root, False, _X_SUBSTRUCTURE_MASK, ctypes.byref(ev))
            lib.XFlush(dpy)
            return True
        finally:
            lib.XCloseDisplay(dpy)
    except Exception:  # noqa: BLE001 - stacking is best-effort
        return False


def _restack_window_below(gdk_win):
    """Lower one Gdk window below its siblings (GDK + EWMH, best-effort)."""
    if gdk_win is None:
        return False
    try:
        gdk_win.lower()
    except Exception:  # noqa: BLE001
        pass
    try:
        xid = gdk_win.get_xid()
    except Exception:  # noqa: BLE001 - not yet realized
        return False
    return _x11_restack_below(xid)


class DummyWindow(Gtk.ApplicationWindow):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)


class BasePlayer(Gtk.Application):
    """
    <node>
    <interface name='io.github.fabx_dev.apricus.player'>
        <property name="mode" type="s" access="read"/>
        <property name="data_source" type="s" access="readwrite"/>
        <property name="volume" type="i" access="readwrite"/>
        <property name="is_mute" type="b" access="readwrite"/>
        <property name="is_playing" type="b" access="read"/>
        <method name='pause_playback'/>
        <method name='start_playback'/>
        <method name='quit_player'>
            <arg type='b' name='restore_wallpaper' direction='in'/>
        </method>
    </interface>
    </node>
    """

    def __init__(self, *args, **kwargs):
        app_flags = (
            Gio.ApplicationFlags.NON_UNIQUE if is_snap() else Gio.ApplicationFlags.FLAGS_NONE
        )
        super().__init__(*args, application_id=APP_ID, flags=app_flags, **kwargs)
        setproctitle.setproctitle(mp.current_process().name)
        self.windows = dict()
        self._monitor_detect()

    def _monitor_detect(self):
        display = Gdk.Display.get_default()
        if display is None:
            # No usable display (e.g. sandbox without a forwarded socket):
            # stay idle with a clear error instead of crashing on None.
            logger.error("[Player] No Gdk display available; player stays idle")
            return
        screen = display.get_default_screen()

        for i in range(display.get_n_monitors()):
            monitor = display.get_monitor(i)
            if monitor not in self.windows:
                self.windows[monitor] = None

        screen.connect("size-changed", self._on_size_changed)
        display.connect("monitor-added", self._on_monitor_added)
        display.connect("monitor-removed", self._on_monitor_removed)

    def new_window(self, gdk_monitor):
        # Override here for different window
        # NOTE: Don't forget to set the application=self, otherwise the application will quit immediately lol
        return DummyWindow(application=self)

    def _on_size_changed(self, *args):
        logger.info("[Player] size-changed")
        for monitor, window in self.windows.items():
            if window is None:
                continue
            rect = monitor.get_geometry()
            window.resize(rect.width, rect.height)
            window.move(rect.x, rect.y)

    def _on_monitor_added(self, _, gdk_monitor, *args):
        logger.info("[Player] monitor-added")
        self.windows[gdk_monitor] = None
        self.do_activate()

    def _on_monitor_removed(self, _, gdk_monitor, *args):
        logger.info("[Player] monitor-removed")
        del self.windows[gdk_monitor]

    def do_startup(self):
        Gtk.Application.do_startup(self)

    def _lower_desktop_windows(self):
        # Keep our DESKTOP windows at the bottom of the desktop layer, below
        # DING's icons window. Called directly and from the debounced stacking
        # guard; repeated calls are cheap no-ops once already at the bottom.
        self._lower_guard_until = time.monotonic() + 0.15
        for window in self.windows.values():
            if window is None:
                continue
            try:
                gdk_win = window.get_window()
                if gdk_win is not None:
                    _restack_window_below(gdk_win)
            except Exception:  # noqa: BLE001
                pass
        return False

    def _schedule_lower(self):
        """Coalesce restack requests: run at 60 ms and 320 ms (after DING's
        idle_add and 250 ms _moveIntoPlace timer)."""
        if getattr(self, "_shell_managed", False):
            # The Shell extension owns stacking now; stay out of its way.
            return False
        if getattr(self, "_lower_pending", False):
            return False
        self._lower_pending = True
        GLib.timeout_add(60, self._do_scheduled_lower)
        GLib.timeout_add(320, self._do_followup_lower)
        return False

    def _do_scheduled_lower(self):
        self._lower_pending = False
        if time.monotonic() < getattr(self, "_lower_guard_until", 0):
            return False
        self._lower_desktop_windows()
        return False

    def _do_followup_lower(self):
        self._lower_desktop_windows()
        return False

    def request_lower(self):
        """Thread-safe entry point for media-ready hooks (first frame,
        page loaded, hot-switch) to re-assert bottom stacking."""
        if getattr(self, "_shell_managed", False):
            return
        GLib.idle_add(self._schedule_lower)

    def _start_stacking_safety_net(self):
        """Bounded safety net: re-lower every 2 s.

        The event guard above cannot see pure-Wayland restacks (e.g. DING
        lowering itself with no X11 client change), and DING always moves
        last via its low-priority idle handler. A 2 s re-lower guarantees
        convergence within 2 s of any invisible move at negligible cost
        (one no-op X round-trip when already at the bottom).
        """
        if getattr(self, "_safety_net_installed", False):
            return
        self._safety_net_installed = True
        GLib.timeout_add_seconds(2, self._safety_lower)

    def _safety_lower(self):
        try:
            self._lower_desktop_windows()
        except Exception:  # noqa: BLE001 - best-effort only
            pass
        return True

    def _watch_stacking(self):
        """Watch X11 root properties via libX11 + GLib.io_add_watch.

        Note: GdkX11.X11Window.add_filter is marked (skip) in GI typelibs
        and raises AttributeError in PyGObject. Watching the X11 connection
        FD directly in the GLib main loop is 100% event-driven (zero threads,
        zero polling) and catches Mutter's _NET_CLIENT_LIST_STACKING /
        _NET_ACTIVE_WINDOW updates on both X11 and XWayland.
        """
        if getattr(self, "_stacking_watched", False):
            return
        try:
            if not _is_x11_display():
                return
            lib = _x11_lib()
            if lib is None:
                return
            lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
            lib.XOpenDisplay.restype = ctypes.c_void_p
            dpy = lib.XOpenDisplay(None)
            if not dpy:
                return
            lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            lib.XDefaultRootWindow.restype = ctypes.c_ulong
            lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            lib.XInternAtom.restype = ctypes.c_ulong
            lib.XSelectInput.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_long]
            lib.XSelectInput.restype = ctypes.c_int
            lib.XConnectionNumber.argtypes = [ctypes.c_void_p]
            lib.XConnectionNumber.restype = ctypes.c_int
            lib.XPending.argtypes = [ctypes.c_void_p]
            lib.XPending.restype = ctypes.c_int
            lib.XNextEvent.argtypes = [ctypes.c_void_p, ctypes.POINTER(_XPropertyEvent)]
            lib.XNextEvent.restype = ctypes.c_int
            lib.XFlush.argtypes = [ctypes.c_void_p]
            lib.XFlush.restype = ctypes.c_int

            root = lib.XDefaultRootWindow(dpy)
            self._watch_dpy = dpy
            self._watch_atoms = {
                lib.XInternAtom(dpy, b"_NET_CLIENT_LIST_STACKING", False),
                lib.XInternAtom(dpy, b"_NET_CLIENT_LIST", False),
                lib.XInternAtom(dpy, b"_NET_ACTIVE_WINDOW", False),
                lib.XInternAtom(dpy, b"_NET_CURRENT_DESKTOP", False),
            }
            lib.XSelectInput(dpy, root, 1 << 22)  # PropertyChangeMask
            lib.XFlush(dpy)
            fd = lib.XConnectionNumber(dpy)
            GLib.io_add_watch(fd, GLib.IOCondition.IN, self._on_x11_fd_ready)
            self._stacking_watched = True
            logger.info("[Player] stacking guard armed (X11 root watch)")
        except Exception as e:  # noqa: BLE001 - stacking guard is best-effort
            logger.debug(f"[Player] stacking watch unavailable: {e}")

    def _on_x11_fd_ready(self, _fd, _cond):
        try:
            lib = _x11_lib()
            dpy = getattr(self, "_watch_dpy", None)
            if not lib or not dpy:
                return False
            ev = _XPropertyEvent()
            triggered = False
            while lib.XPending(dpy) > 0:
                lib.XNextEvent(dpy, ctypes.byref(ev))
                if ev.type == 28 and ev.atom in self._watch_atoms:
                    triggered = True
            if triggered and time.monotonic() >= getattr(self, "_lower_guard_until", 0):
                self._schedule_lower()
        except Exception:  # noqa: BLE001 - never break the event stream
            pass
        return True

    def do_activate(self):
        # When the Shell extension hosts our windows in the background
        # layer, it owns stacking: skip the in-app guard, safety net and
        # the DING reload so the two never compete.
        self._evaluate_shell_managed()
        if not getattr(self, "_shell_status_logged", False):
            log_status()
            self._shell_status_logged = True
        for monitor in self.windows:
            window = self.windows[monitor]
            if window is None:
                window = self.new_window(monitor)
                window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
                # Identity for the Shell extension (WM_WINDOW_ROLE): only
                # wallpaper windows carry it, never the settings window.
                try:
                    window.set_role(WALLPAPER_WINDOW_ROLE)
                except Exception:  # noqa: BLE001 - role is advisory only
                    pass
                # Never steal focus or raise above the desktop icons.
                # (Video windows additionally disable focus themselves;
                # WebWindow keeps keyboard focus for interactive pages and
                # relies on the stacking guard below instead, unless the
                # Shell extension is managing stacking.)
                window.set_keep_below(True)
                window.set_focus_on_map(False)
                window.connect("map-event", lambda *_: self._schedule_lower())
                rect = monitor.get_geometry()
                x, y, width, height = rect.x, rect.y, rect.width, rect.height
                window.set_size_request(width, height)
                window.move(x, y)
                self.windows[monitor] = window
            # Never present(): it sends _NET_ACTIVE_WINDOW and raises us
            # over DING. show_all() maps without activating.
            if not window.get_visible():
                window.show_all()
        # Watch for the integration being toggled mid-session (event-driven
        # GSettings notify, no polling): enabling/disabling must take effect
        # without restarting the player.
        self._watch_shell_integration()
        if self._shell_managed:
            logger.info("[Player] stacking managed by GNOME Shell integration")
            return
        self._start_legacy_stacking()

    def _evaluate_shell_managed(self):
        """Recompute whether the Shell extension owns stacking right now."""
        self._shell_managed = bool(integration_active())
        return self._shell_managed

    def _start_legacy_stacking(self):
        """(Re)start the in-app stacking guard (idempotent parts stay so)."""
        self._lower_desktop_windows()
        self._watch_stacking()
        self._start_stacking_safety_net()
        for delay_ms in (150, 500, 1200):
            GLib.timeout_add(delay_ms, self._lower_desktop_windows)
        # Workaround for DING extension
        gnome_desktop_icon_workaround()

    def _watch_shell_integration(self):
        """Re-evaluate integration when extensions are toggled (no polling).

        The Shell loads newly enabled extensions only after login, but a
        running player must still adopt when the user enables ours
        mid-session (and fall back when it is disabled): recreating the
        wallpaper windows triggers the proven window-created adoption.
        """
        if getattr(self, "_integration_watched", False):
            return
        # Gio.Settings.new() aborts the process when the schema is absent
        # (Flatpak runtime); _shell_settings() probes safely first.
        settings = _shell_settings()
        if settings is None:
            logger.debug("[Player] Shell integration watcher unavailable")
            return
        # Keep a reference: without it the proxy is GC'd and notify stops.
        self._integration_settings = settings
        try:
            settings.connect("changed::enabled-extensions", self._on_shell_integration_changed)
            self._integration_watched = True
        except Exception:  # noqa: BLE001 - best effort only
            logger.debug("[Player] Shell integration watch failed")

    def _on_shell_integration_changed(self, *_args):
        was_managed = bool(getattr(self, "_shell_managed", False))
        try:
            now_managed = bool(integration_active())
        except Exception:  # noqa: BLE001 - never break on a settings signal
            logger.debug("[Player] integration re-check failed")
            return
        if now_managed == was_managed:
            return
        self._shell_managed = now_managed
        log_status()
        if now_managed:
            logger.info(
                "[Player] Shell integration became active: "
                "recreating wallpaper windows for adoption"
            )
            self._recreate_wallpaper_windows()
        else:
            logger.info("[Player] Shell integration no longer active: resuming legacy stacking")
            self._start_legacy_stacking()

    def _recreate_wallpaper_windows(self):
        """Destroy and recreate wallpaper windows (adoptable fresh ones)."""
        for monitor, window in list(self.windows.items()):
            if window is None:
                continue
            cleanup = getattr(window, "cleanup", None)
            if callable(cleanup):
                try:
                    cleanup()
                except Exception:  # noqa: BLE001 - best effort
                    pass
            try:
                window.destroy()
            except Exception:  # noqa: BLE001 - best effort
                pass
            self.windows[monitor] = None
        self.do_activate()

    @property
    @abstractmethod
    def mode(self):
        pass

    @property
    @abstractmethod
    def data_source(self):
        pass

    @data_source.setter
    def data_source(self, data_source):
        pass

    @property
    @abstractmethod
    def volume(self):
        pass

    @volume.setter
    def volume(self, volume):
        pass

    @property
    @abstractmethod
    def is_mute(self):
        pass

    @is_mute.setter
    def is_mute(self, is_mute):
        pass

    @property
    @abstractmethod
    def is_playing(self):
        pass

    @abstractmethod
    def pause_playback(self):
        pass

    @abstractmethod
    def start_playback(self):
        pass

    def quit_player(self, restore_wallpaper=True, dbus_context=None):
        _authorize("player.quit", dbus_context)
        self.quit()


def main():
    bus = SessionBus()
    app = BasePlayer()
    try:
        bus.publish(DBUS_NAME_PLAYER, app)
    except RuntimeError as e:
        logger.error(e)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
