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
    """ctypes handle for libX11, cached. None when unavailable."""
    global _xlib_handle
    if _xlib_handle is None:
        for lib in ("libX11.so.6", "libX11.so"):
            try:
                _xlib_handle = ctypes.cdll.LoadLibrary(lib)
                break
            except OSError:
                continue
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
        lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
        lib.XOpenDisplay.restype = ctypes.c_void_p
        dpy = lib.XOpenDisplay(None)
        if not dpy:
            return False
        try:
            lib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
            lib.XInternAtom.restype = ctypes.c_ulong
            msgtype = lib.XInternAtom(dpy, b"_NET_RESTACK_WINDOW", False)
            lib.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
            lib.XDefaultRootWindow.restype = ctypes.c_ulong
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
            lib.XSendEvent.argtypes = [
                ctypes.c_void_p,
                ctypes.c_ulong,
                ctypes.c_int,
                ctypes.c_long,
                ctypes.POINTER(_XClientMessageEvent),
            ]
            lib.XSendEvent.restype = ctypes.c_int
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
        for monitor in self.windows:
            window = self.windows[monitor]
            if window is None:
                window = self.new_window(monitor)
                window.set_type_hint(Gdk.WindowTypeHint.DESKTOP)
                # Never steal focus or raise above the desktop icons.
                # (Video windows additionally disable focus themselves;
                # WebWindow keeps keyboard focus for interactive pages and
                # relies on the stacking guard below instead.)
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
        self._lower_desktop_windows()
        self._watch_stacking()
        self._start_stacking_safety_net()
        for delay_ms in (150, 500, 1200):
            GLib.timeout_add(delay_ms, self._lower_desktop_windows)
        # Workaround for DING extension
        gnome_desktop_icon_workaround()

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
