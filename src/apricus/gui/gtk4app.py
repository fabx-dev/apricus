# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""GTK4 + Libadwaita control panel (migration target).

Talks to the UNCHANGED ApricusServer over the existing D-Bus API: no
backend or protocol changes. Started with APRICUS_GTK4=1 (see __main__).
"""

import logging
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from pydbus import SessionBus

from apricus.commons import DBUS_NAME_SERVER, LOGGER_NAME, PROJECT
from apricus.utils import init_translations, setup_logging

logger = logging.getLogger(LOGGER_NAME)

APP_ID = f"{PROJECT}.gui4"


class ControlPanel(Adw.Application):
    def __init__(self, version, *args, **kwargs):
        super().__init__(
            *args,
            application_id=APP_ID,
            flags=Gio.ApplicationFlags.FLAGS_NONE,
            **kwargs,
        )
        self.version = version
        self.window = None

    def do_activate(self):
        if self.window is None:
            self.window = Adw.ApplicationWindow(application=self)
            self.window.set_title("Apricus")
            self.window.set_default_size(480, 320)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
            box.set_margin_top(24)
            box.set_margin_bottom(24)
            box.set_margin_start(24)
            box.set_margin_end(24)
            self.status = Gtk.Label(label="Connecting…")
            box.append(self.status)
            btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            btn_row.set_halign(Gtk.Align.CENTER)
            play = Gtk.Button(label="Play/Pause")
            play.connect("clicked", self.on_play_pause)
            quit_btn = Gtk.Button(label="Quit")
            quit_btn.connect("clicked", lambda *_: self.quit())
            btn_row.append(play)
            btn_row.append(quit_btn)
            box.append(btn_row)
            self.window.set_content(box)
        self._refresh_status()
        self.window.present()

    def _server(self):
        try:
            return SessionBus().get(DBUS_NAME_SERVER)
        except GLib.Error:
            logger.error("[GUI4] Couldn't connect to server")
            return None

    def _refresh_status(self):
        server = self._server()
        if server is None:
            self.status.set_text("Server not running")
        else:
            self.status.set_text(f"Apricus {self.version} — mode: {server.mode}")

    def on_play_pause(self, *_args):
        server = self._server()
        if server is None:
            return
        prev = server.is_paused_by_user
        server.is_paused_by_user = not prev
        if not prev:
            server.pause_playback()
        else:
            server.start_playback()
        self._refresh_status()


def main(version="devel", pkgdatadir="/usr/share/apricus", localedir="/usr/share/locale"):
    del pkgdatadir  # GTK4 UI has no compiled resources yet
    setup_logging()
    init_translations(localedir)
    Adw.init()
    app = ControlPanel(version)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
