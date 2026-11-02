"""Real Gtk3 nullable-title restoration; fake string titles masked this bug."""

import os
import pathlib
import shutil
import subprocess
import sys

import pytest


def test_real_untitled_gtk_window_survives_two_interactions():
    if not shutil.which("xvfb-run"):
        pytest.skip("private Xvfb display required")
    root = pathlib.Path(__file__).resolve().parents[1]
    driver = r"""
import os
from types import SimpleNamespace
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk
from apricus.player import web_player

web_player.ConfigUtil = lambda: SimpleNamespace(load=lambda: {"is_stopped": False})
window = Gtk.ApplicationWindow()
window.set_default_size(800, 600)
assert window.get_title() is None
view = Gtk.Entry()
view.get_uri = lambda: "file:///native-title-fixture.html"
window.add(view)
window._WebWindow__webview = view
window.show_all()
monitor = Gdk.Display.get_default().get_monitor(0)
player = SimpleNamespace(_paused=False, windows={monitor: window})
player.web_interaction_restore = lambda *args: web_player.WebPlayer.web_interaction_restore(player, *args)
for token in ("a" * 32, "b" * 32):
    web_player.WebPlayer.web_interaction_prepare(player, token, os.getpid())
    assert window.get_title() == f"Apricus Web [{token}]"
    assert player.web_interaction_restore(token, True, os.getpid())
    assert window.get_title() is None
    assert window.get_visible() and window.get_realized()
    assert window._WebWindow__webview is view
    assert not getattr(window, "_apricus_quarantined", False)
window.destroy()
print("PASS: exact None title and original widgets survive both restorations")
"""
    result = subprocess.run(
        ["xvfb-run", "-a", sys.executable, "-c", driver],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(root / "src"), GDK_BACKEND="x11", GIO_USE_VFS="local"),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS:" in result.stdout
