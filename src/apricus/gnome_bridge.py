# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded Shell → GUI status channel; deliberately not a player control API.

Ownership authenticates the current Shell connection, not an extension or a
binary cryptographically. Other code within Shell shares that identity.
"""

import logging
import os
import secrets
import time

NAME = "io.github.fabxdev.Apricus.GnomeBridge"
PATH = "/io/github/fabxdev/Apricus/GnomeBridge"
INTERFACE = NAME + "1"
LEASE_SECONDS = 90
RENEW_SECONDS = 30
logger = logging.getLogger(__name__)


def boot_time():
    return time.clock_gettime(time.CLOCK_BOOTTIME)


class Registration:
    """Pure state machine. Clock includes suspend; client timestamps are ignored."""

    def __init__(self, changed=lambda: None, clock=boot_time, monotonic=time.monotonic):
        self.changed = changed
        self.clock = clock
        self.monotonic = monotonic
        self.offset = clock() - monotonic()
        self.seen = False
        self.generation = ""
        self.sender = None
        self.sequence = 0
        self.deadline = 0
        self.state = "unknown"
        self.snapshot = {}
        self.reason = "not_registered"

    def invalidate(self, reason):
        self.generation = ""
        self.sender = None
        self.deadline = 0
        self.snapshot = {}
        self.state = "unknown"
        self.reason = reason
        self.changed()

    def check(self):
        offset = self.clock() - self.monotonic()
        if abs(offset - self.offset) > 2:
            self.offset = offset
            self.invalidate("resume")
        elif self.deadline and self.clock() >= self.deadline:
            self.invalidate("lease_expired")

    @staticmethod
    def validate_snapshot(loaded, operational, adopted):
        if type(loaded) is not bool or type(operational) is not bool:
            raise ValueError("invalid flags")
        if type(adopted) is not int or not 0 <= adopted <= 4096:
            raise ValueError("invalid adopted count")
        if not loaded:
            raise ValueError("inconsistent snapshot")
        return {"loaded": loaded, "operational": operational, "adopted": adopted}

    def register(self, sender, version, sequence, loaded, operational, adopted):
        if version != 1 or sequence != 1:
            raise ValueError("unsupported version or initial sequence")
        snapshot = self.validate_snapshot(loaded, operational, adopted)
        self.check()
        self.seen = True
        self.sender = sender
        self.generation = secrets.token_hex(16)
        self.sequence = sequence
        self.snapshot = snapshot
        self.state = "active" if operational else "loaded"
        self.reason = "registered"
        self.deadline = self.clock() + LEASE_SECONDS
        self.changed()
        return self.generation

    def update(self, sender, generation, sequence, loaded, operational, adopted):
        snapshot = self.validate_snapshot(loaded, operational, adopted)
        self._match(sender, generation, sequence)
        self.sequence = sequence
        self.snapshot = snapshot
        self.state = "active" if operational else "loaded"
        self.reason = "updated"
        self.deadline = self.clock() + LEASE_SECONDS
        self.changed()

    def unregister(self, sender, generation, sequence):
        self._match(sender, generation, sequence)
        self.sequence = sequence
        self.snapshot = {"loaded": False, "operational": False, "adopted": 0}
        self.generation = ""
        self.sender = None
        self.state = "disabled"
        self.reason = "unregistered"
        # Explicit disable is also bounded; it is not a perpetual assertion.
        self.deadline = self.clock() + LEASE_SECONDS
        self.changed()

    def _match(self, sender, generation, sequence):
        self.check()
        if not self.generation or self.sender != sender or generation != self.generation:
            raise ValueError("registration expired or replaced")
        if not self.sequence < sequence <= 0xFFFFFFFF:
            raise ValueError("duplicate or out-of-order sequence")

    def status(self):
        self.check()
        if not self.seen:
            return None
        return self.state, {
            "bridge": True,
            **self.snapshot,
            "reason": self.reason,
            "lease_seconds": LEASE_SECONDS,
        }


XML = f"""<node><interface name="{INTERFACE}">
 <method name="Register">
  <arg type="u" direction="in" name="version"/>
  <arg type="u" direction="in" name="sequence"/>
  <arg type="b" direction="in" name="loaded"/>
  <arg type="b" direction="in" name="operational"/>
  <arg type="u" direction="in" name="adopted"/>
  <arg type="s" direction="out" name="generation"/>
  <arg type="u" direction="out" name="lease"/>
  <arg type="u" direction="out" name="renew"/>
 </method>
 <method name="Update">
  <arg type="s" direction="in" name="generation"/>
  <arg type="u" direction="in" name="sequence"/>
  <arg type="b" direction="in" name="loaded"/>
  <arg type="b" direction="in" name="operational"/>
  <arg type="u" direction="in" name="adopted"/>
 </method>
 <method name="Unregister">
  <arg type="s" direction="in" name="generation"/>
  <arg type="u" direction="in" name="sequence"/>
 </method>
</interface></node>"""


class Bridge:
    def __init__(self, changed):
        from gi.repository import Gio, GLib

        self.Gio, self.GLib = Gio, GLib
        self.registration = Registration(changed)
        self.bus = None
        self.object_id = 0
        self.subscription = 0
        self.pending = 0
        self.senders = set()
        self.epoch = 0
        self.closed = False
        self.closed_id = 0
        self.node = Gio.DBusNodeInfo.new_for_xml(XML)
        self.name_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            NAME,
            Gio.BusNameOwnerFlags.DO_NOT_QUEUE,
            self._bus_acquired,
            lambda *_: logger.info("[GnomeBridge] listening %s", NAME),
            lambda *_: self._invalidate("service_unavailable"),
        )
        # Local lease/suspend clock watchdog, no D-Bus polling. Expiry is
        # checked on every read/message too; worst timer lateness ~1 second.
        self.timer = GLib.timeout_add_seconds(1, self._tick)

    def _tick(self):
        self.registration.check()
        return True

    def _invalidate(self, reason):
        self.epoch += 1
        self.registration.invalidate(reason)

    def _bus_acquired(self, bus, _name):
        self.bus = bus
        self.closed_id = bus.connect("closed", lambda *_: self._invalidate("bus_closed"))
        self.subscription = bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            "org.gnome.Shell",
            self.Gio.DBusSignalFlags.NONE,
            lambda *_: self._invalidate("shell_owner_changed"),
        )
        self.object_id = bus.register_object(PATH, self.node.interfaces[0], self._call, None, None)

    def _query(self, method, argument, callback):
        self.bus.call(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            method,
            self.GLib.Variant("(s)", (argument,)),
            None,
            self.Gio.DBusCallFlags.NONE,
            2000,
            None,
            callback,
        )

    def _call(self, bus, sender, _path, _interface, method, parameters, invocation):
        # Small typed API; no strings except the fixed-size opaque generation.
        args = parameters.unpack()
        if (
            self.closed
            or self.pending >= 8
            or sender in self.senders
            or any(isinstance(arg, str) and len(arg) > 32 for arg in args)
        ):
            invocation.return_dbus_error(INTERFACE + ".Rejected", "busy or invalid request")
            return
        self.pending += 1
        self.senders.add(sender)
        epoch = self.epoch
        suspend_offset = boot_time() - time.monotonic()
        finished = False

        def finish(error=None):
            nonlocal finished
            if finished:
                return
            finished = True
            self.pending -= 1
            self.senders.discard(sender)
            if error:
                invocation.return_dbus_error(INTERFACE + ".Rejected", str(error))

        def apply(bus, result):
            try:
                owner = bus.call_finish(result).unpack()[0]
                if abs(boot_time() - time.monotonic() - suspend_offset) > 2:
                    self._invalidate("resume")
                    raise ValueError("request crossed suspend; re-register")
                if self.closed or epoch != self.epoch or owner != sender:
                    raise ValueError("ownership changed")
                state = self.registration
                if method == "Register":
                    generation = state.register(sender, *args)
                    reply = self.GLib.Variant("(suu)", (generation, LEASE_SECONDS, RENEW_SECONDS))
                elif method == "Update":
                    state.update(sender, *args)
                    reply = self.GLib.Variant("()", ())
                elif method == "Unregister":
                    state.unregister(sender, *args)
                    reply = self.GLib.Variant("()", ())
                else:
                    raise ValueError("unknown method")
                logger.debug("[GnomeBridge] accepted %s sender=%s", method, sender)
                invocation.return_value(reply)
                finish()
            except Exception as error:
                finish(error)

        def credentials(bus, result):
            try:
                data = bus.call_finish(result).unpack()[0]
                uid, pid = data.get("UnixUserID"), data.get("ProcessID")
                if uid != os.getuid() or not isinstance(pid, int) or pid <= 0:
                    raise ValueError("unverifiable Shell credentials")
                logger.debug("[GnomeBridge] sender=%s uid=%s pid=%s", sender, uid, pid)
                self._query("GetNameOwner", "org.gnome.Shell", apply)
            except Exception as error:
                finish(error)

        def owner_checked(bus, result):
            try:
                owner = bus.call_finish(result).unpack()[0]
                if owner != sender or self.closed or epoch != self.epoch:
                    raise ValueError("sender is not current org.gnome.Shell owner")
                # sender == owner: credentials belong to precisely that unique
                # connection, not a PID/UUID supplied by the client.
                self._query("GetConnectionCredentials", sender, credentials)
            except Exception as error:
                finish(error)

        try:
            self._query("GetNameOwner", "org.gnome.Shell", owner_checked)
        except Exception as error:
            finish(error)

    def stop(self):
        self.closed = True
        self._invalidate("stopped")
        if self.timer:
            self.GLib.source_remove(self.timer)
            self.timer = 0
        if self.bus:
            if self.subscription:
                self.bus.signal_unsubscribe(self.subscription)
            if self.object_id:
                self.bus.unregister_object(self.object_id)
            if self.closed_id:
                self.bus.disconnect(self.closed_id)
        self.Gio.bus_unown_name(self.name_id)


_bridge = None


def start(changed):
    """Snap GUI only. Flatpak/native retain their tested detection paths."""
    global _bridge
    from apricus.utils import is_gnome, is_snap, is_wayland

    if _bridge is None and is_snap() and is_gnome() and is_wayland():
        try:
            _bridge = Bridge(changed)
        except Exception:
            logger.exception("[GnomeBridge] unavailable; host receipt fallback")
    return _bridge


def status():
    return _bridge.registration.status() if _bridge else None


def stop():
    global _bridge
    if _bridge:
        _bridge.stop()
        _bridge = None
