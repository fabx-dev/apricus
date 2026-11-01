"""Regression tests for the Streaming availability info button (2.1.0).

Subprocess isolation (like test_gtk4app.py): the GTK4 panel module cannot
share a process with Gtk 3 callers, so each check runs in a fresh interpreter
with only apricus.gui.gtk4app imported. Unlike test_gtk4app.py these checks
instantiate REAL widgets on a private Xvfb, without touching the live app.
"""

import os
import pathlib
import select
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

TITLE = "Streaming availability"
BODY = (
    "Some YouTube videos may not play reliably due to streaming service "
    "restrictions or compatibility limitations."
)

DRIVER = r"""
import sys
import time

import apricus.gui.gtk4app as app
from gi.repository import Adw, GLib, Gtk


def settle():
    deadline = time.monotonic() + 0.3
    while time.monotonic() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.005)


def _button():
    return app.ControlPanel._stream_availability_info()


checks = {}


def check(name):
    def wrap(fn):
        checks[name] = fn
        return fn
    return wrap


@check("copy")
def _():
    assert app.ControlPanel.STREAM_AVAILABILITY_TITLE == sys.argv[2]
    assert app.ControlPanel.STREAM_AVAILABILITY_BODY == sys.argv[3]


@check("button")
def _():
    btn, pop = _button()
    assert btn.get_icon_name() == "dialog-information-symbolic"
    assert btn.get_tooltip_text() == sys.argv[3]
    assert btn.get_first_child().get_focusable() and btn.get_sensitive()
    assert btn.get_valign() == Gtk.Align.CENTER
    assert isinstance(btn.get_popover(), Gtk.Popover)


@check("popover")
def _():
    btn, pop = _button()
    box = pop.get_child()
    kids = []
    child = box.get_first_child()
    while child is not None:
        kids.append(child)
        child = child.get_next_sibling()
    assert len(kids) == 2
    assert kids[0].get_text() == sys.argv[2]
    assert kids[1].get_text() == sys.argv[3]
    assert kids[1].get_wrap() is True
    assert kids[1].get_max_width_chars() == 38


@check("activate")
def _():
    btn, pop = _button()
    assert pop.get_visible() is False
    assert pop.get_autohide() is True
    win = Gtk.Window()
    win.set_child(btn)
    win.present()
    settle()
    btn.activate()
    settle()
    assert pop.get_visible() is True
    pop.popdown()
    settle()
    assert pop.get_visible() is False
    win.destroy()


@check("layout")
def _():
    obj = app.ControlPanel.__new__(app.ControlPanel)
    # Keep the page alive: GTK children do not reference their parents,
    # so dropping the returned page would let GC destroy the row boxes
    # and clear the widgets' parent links.
    page = obj._streaming_page()
    assert page is not None
    entry = obj.stream_entry
    assert entry.get_hexpand() is True
    row = entry.get_parent()
    assert isinstance(row, Gtk.Box)
    assert row.get_orientation() == Gtk.Orientation.HORIZONTAL
    assert obj.stream_info_btn.get_parent() is row
    assert obj.stream_info_btn.get_icon_name() == "dialog-information-symbolic"
    assert isinstance(obj.stream_info_popover, Gtk.Popover)


@check("keyboard")
def _():
    import ctypes
    import gi
    gi.require_version("GdkX11", "4.0")
    from gi.repository import GdkX11

    x11 = ctypes.CDLL("libX11.so.6")
    xtst = ctypes.CDLL("libXtst.so.6")
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XKeysymToKeycode.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    x11.XSetInputFocus.argtypes = [ctypes.c_void_p, ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    x11.XFlush.argtypes = [ctypes.c_void_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.argtypes = [ctypes.c_void_p]
    x11.XDefaultRootWindow.restype = ctypes.c_ulong
    x11.XTranslateCoordinates.argtypes = [
        ctypes.c_void_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_int, ctypes.c_int,
        ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_int), ctypes.POINTER(ctypes.c_ulong),
    ]
    xtst.XTestFakeKeyEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeMotionEvent.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_ulong]
    xtst.XTestFakeButtonEvent.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_int, ctypes.c_ulong]
    display = x11.XOpenDisplay(None)
    assert display

    def key(keysym):
        code = x11.XKeysymToKeycode(display, keysym)
        xtst.XTestFakeKeyEvent(display, code, True, 0)
        xtst.XTestFakeKeyEvent(display, code, False, 0)
        x11.XFlush(display)
        settle()

    obj = app.ControlPanel.__new__(app.ControlPanel)
    page = obj._streaming_page()
    win = Gtk.Window(default_width=800, default_height=700)
    win.set_child(page)
    win.present()
    settle()
    xid = GdkX11.X11Surface.get_xid(win.get_surface())
    x11.XSetInputFocus(display, xid, 1, 0)
    obj.stream_entry.grab_focus()
    key(0xff09)  # Tab from the URL entry to the native menu button.
    focus = win.get_focus()
    assert focus is obj.stream_info_btn or focus.is_ancestor(obj.stream_info_btn)
    for activation in (0xff0d, 0x20):  # Return, Space
        key(activation)
        assert obj.stream_info_popover.get_visible()
        key(0xff1b)  # Escape
        assert not obj.stream_info_popover.get_visible()

    origin_x, origin_y, child = ctypes.c_int(), ctypes.c_int(), ctypes.c_ulong()
    x11.XTranslateCoordinates(display, xid, x11.XDefaultRootWindow(display), 0, 0,
                              ctypes.byref(origin_x), ctypes.byref(origin_y), ctypes.byref(child))

    def click(x, y):
        xtst.XTestFakeMotionEvent(display, -1, origin_x.value + int(x), origin_y.value + int(y), 0)
        xtst.XTestFakeButtonEvent(display, 1, True, 0)
        xtst.XTestFakeButtonEvent(display, 1, False, 0)
        x11.XFlush(display)
        settle()

    ok, bounds = obj.stream_info_btn.compute_bounds(win)
    assert ok
    click(bounds.get_x() + bounds.get_width() / 2, bounds.get_y() + bounds.get_height() / 2)
    assert obj.stream_info_popover.get_visible()
    click(win.get_width() - 10, win.get_height() - 10)
    assert not obj.stream_info_popover.get_visible()
    win.destroy()
    x11.XCloseDisplay(display)


@check("themes")
def _():
    manager = Adw.StyleManager.get_default()
    for scheme in (Adw.ColorScheme.FORCE_LIGHT, Adw.ColorScheme.FORCE_DARK):
        manager.set_color_scheme(scheme)
        obj = app.ControlPanel.__new__(app.ControlPanel)
        page = obj._streaming_page()
        win = Gtk.Window(default_width=800, default_height=700)
        win.set_child(page)
        win.present()
        settle()
        btn = obj.stream_info_btn
        assert Gtk.IconTheme.get_for_display(win.get_display()).has_icon(btn.get_icon_name())
        assert btn.get_width() >= 16 and btn.get_height() >= 16
        assert obj.stream_entry.get_width() > btn.get_width()
        btn.activate()
        settle()
        pop = obj.stream_info_popover
        assert pop.get_visible()
        assert 0 < pop.get_width() < win.get_width()
        assert 0 < pop.get_height() < win.get_height()
        body = pop.get_child().get_last_child()
        assert body.get_layout().get_line_count() > 1
        pop.popdown()
        settle()
        win.destroy()
        settle()


checks[sys.argv[1]]()
print("CHECK-OK " + sys.argv[1])
"""


@pytest.fixture(scope="module")
def display():
    if shutil.which("Xvfb") is None:
        pytest.skip("Xvfb is required for isolated real-widget checks")
    read_fd, write_fd = os.pipe()
    proc = None
    try:
        proc = subprocess.Popen(
            [
                "Xvfb",
                "-displayfd",
                str(write_fd),
                "-screen",
                "0",
                "1280x800x24",
                "-nolisten",
                "tcp",
            ],
            pass_fds=(write_fd,),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os.close(write_fd)
        write_fd = None
        assert select.select([read_fd], [], [], 10)[0], "Xvfb startup timed out"
        number = os.read(read_fd, 32).decode().strip()
        assert number.isdigit(), "Xvfb failed to allocate a display"
        yield ":" + number
    finally:
        os.close(read_fd)
        if write_fd is not None:
            os.close(write_fd)
        if proc is not None:
            proc.terminate()
            proc.wait(timeout=10)


def _run(display, name):
    env = dict(
        os.environ,
        DISPLAY=display,
        GDK_BACKEND="x11",
        GSK_RENDERER="cairo",
        PYTHONPATH=str(ROOT / "src"),
    )
    proc = subprocess.run(
        [sys.executable, "-c", DRIVER, name, TITLE, BODY],
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert f"CHECK-OK {name}" in proc.stdout


def test_info_copy_matches_spec_verbatim(display):
    _run(display, "copy")


def test_info_button_is_discreet_labeled_and_keyboard_reachable(display):
    _run(display, "button")


def test_popover_shares_single_source_and_wraps(display):
    _run(display, "popover")


def test_button_activates_and_popover_closes(display):
    _run(display, "activate")


def test_url_row_layout_keeps_entry_expanded(display):
    _run(display, "layout")


def test_keyboard_and_mouse_open_and_dismiss_on_real_page(display):
    import ctypes.util

    if not ctypes.util.find_library("Xtst"):
        pytest.skip("optional system XTest library is unavailable")
    _run(display, "keyboard")


def test_symbolic_icon_and_wrapped_popover_in_light_and_dark(display):
    _run(display, "themes")
