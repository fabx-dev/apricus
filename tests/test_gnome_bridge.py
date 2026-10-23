# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""State, authentication and fallback tests without a compositor."""

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from apricus import gnome_bridge, gnome_shell
from apricus.gnome_bridge import Bridge, Registration


@pytest.fixture
def state():
    clocks = [100.0, 100.0]
    state = Registration(clock=lambda: clocks[0], monotonic=lambda: clocks[1])
    return state, clocks


def register(state):
    return state.register(":1.14", 1, 1, True, True, 0)


def test_valid_registration_without_wallpaper(state):
    state, _ = state
    token = register(state)
    assert len(token) == 32
    status, detail = state.status()
    assert status == "active"
    assert detail["loaded"] and detail["operational"]
    assert detail["adopted"] == 0


def test_loaded_not_operational(state):
    state, _ = state
    state.register(":1.14", 1, 1, True, False, 0)
    assert state.status()[0] == "loaded"


@pytest.mark.parametrize("sequence", [0, 1, 2])
def test_duplicate_out_of_order_cannot_renew(state, sequence):
    state, clocks = state
    token = register(state)
    state.update(":1.14", token, 2, True, True, 1)
    deadline = state.deadline
    clocks[0] += 15
    clocks[1] += 15
    with pytest.raises(ValueError):
        state.update(":1.14", token, sequence, True, True, 0)
    assert state.deadline == deadline


def test_disable_enable_invalidates_generation(state):
    state, _ = state
    token = register(state)
    state.unregister(":1.14", token, 2)
    assert state.status()[0] == "disabled"
    with pytest.raises(ValueError):
        state.update(":1.14", token, 3, True, True, 0)
    new = register(state)
    assert new != token
    assert state.status()[0] == "active"


def test_lease_expiry_and_late_renew(state):
    state, clocks = state
    token = register(state)
    clocks[:] = [190, 190]
    assert state.status()[0] == "unknown"
    with pytest.raises(ValueError):
        state.update(":1.14", token, 2, True, True, 0)
    assert state.status()[0] == "unknown"


def test_regular_renew_then_interruption(state):
    state, clocks = state
    token = register(state)
    for sequence in range(2, 7):
        clocks[0] += 30
        clocks[1] += 30
        state.update(":1.14", token, sequence, True, True, 0)
        assert state.status()[0] == "active"
    clocks[0] += 90
    clocks[1] += 90
    assert state.status()[0] == "unknown"


def test_suspend_invalidates_even_short_sleep(state):
    state, clocks = state
    register(state)
    clocks[0] += 10  # CLOCK_BOOTTIME includes suspend, monotonic does not.
    assert state.status() == ("unknown", {"bridge": True, "reason": "resume", "lease_seconds": 90})


def test_server_restart_rejects_old_generation(state):
    state, _ = state
    token = register(state)
    restarted = Registration()
    assert restarted.status() is None
    with pytest.raises(ValueError):
        restarted.update(":1.14", token, 2, True, True, 0)
    assert register(restarted) != token


def test_ownership_invalidation(state):
    state, _ = state
    register(state)
    state.invalidate("shell_owner_changed")
    assert state.status()[0] == "unknown"
    assert not state.snapshot


class Invocation:
    error = None
    reply = None

    def return_dbus_error(self, _name, error):
        self.error = error

    def return_value(self, value):
        self.reply = value.unpack()


def fake_bridge(monkeypatch, owners, uid=None, pid=42):
    """Drive the actual async dispatch/credential chain, not a second auth policy."""
    from gi.repository import GLib

    bridge = Bridge.__new__(Bridge)
    bridge.GLib = GLib
    bridge.registration = Registration()
    bridge.closed = False
    bridge.epoch = 0
    bridge.pending = 0
    bridge.senders = set()
    bus = SimpleNamespace(call_finish=lambda result: result)
    replies = iter(owners)

    def query(method, _argument, callback):
        if method == "GetNameOwner":
            result = GLib.Variant("(s)", (next(replies),))
        else:
            result = GLib.Variant(
                "(a{sv})",
                (
                    {
                        "UnixUserID": GLib.Variant(
                            "u", gnome_bridge.os.getuid() if uid is None else uid
                        ),
                        "ProcessID": GLib.Variant("u", pid),
                    },
                ),
            )
        callback(bus, result)

    bridge._query = query
    return bridge, bus, GLib


def authenticate(bridge, bus, glib, sender=":1.14"):
    invocation = Invocation()
    bridge._call(
        bus,
        sender,
        None,
        None,
        "Register",
        glib.Variant("(uubbu)", (1, 1, True, True, 0)),
        invocation,
    )
    return invocation


def test_bus_authenticated_registration(monkeypatch):
    bridge, bus, glib = fake_bridge(monkeypatch, [":1.14", ":1.14"])
    invocation = authenticate(bridge, bus, glib)
    assert not invocation.error
    assert invocation.reply[1:] == (90, 30)
    assert bridge.registration.status()[0] == "active"
    assert bridge.pending == 0 and not bridge.senders


def test_same_uid_independent_gjs_is_rejected(monkeypatch):
    bridge, bus, glib = fake_bridge(monkeypatch, [":1.14"])
    invocation = authenticate(bridge, bus, glib, sender=":1.99")
    assert "not current" in invocation.error
    assert bridge.registration.status() is None


def test_owner_changed_during_authentication(monkeypatch):
    bridge, bus, glib = fake_bridge(monkeypatch, [":1.14", ":1.15"])
    assert "ownership changed" in authenticate(bridge, bus, glib).error
    assert bridge.registration.status() is None


def test_wrong_uid_fails_closed(monkeypatch):
    bridge, bus, glib = fake_bridge(monkeypatch, [":1.14"], uid=0xFFFF)
    assert "credentials" in authenticate(bridge, bus, glib).error


def test_credential_denial_fails_closed(monkeypatch):
    bridge, bus, glib = fake_bridge(monkeypatch, [])
    bus.call_finish = lambda _: (_ for _ in ()).throw(RuntimeError("AppArmor denied"))
    bridge._query = lambda _method, _arg, callback: callback(bus, None)
    assert "AppArmor denied" in authenticate(bridge, bus, glib).error


def test_bridge_absent_and_host_receipt_fallback(monkeypatch):
    monkeypatch.setattr(gnome_bridge, "_bridge", None)
    monkeypatch.setattr(gnome_shell, "is_gnome", lambda: True)
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: True)
    monkeypatch.setattr(gnome_shell, "shell_version", lambda: "50")
    monkeypatch.setattr(gnome_shell, "extension_raw_state", lambda: gnome_shell._UNREACHABLE)
    monkeypatch.setattr(
        gnome_shell,
        "read_host_receipt",
        lambda *a, **kw: {
            "installed": True,
            "loaded": True,
            "enabled": True,
            "stale": False,
        },
    )
    assert gnome_shell.integration_status([])[0] == "active"
    monkeypatch.setattr(gnome_shell, "read_host_receipt", lambda *a, **kw: None)
    assert gnome_shell.integration_status([])[0] == "unknown"


def test_expired_bridge_not_masked_by_receipt(monkeypatch, state):
    registration, clocks = state
    register(registration)
    clocks[:] = [190, 190]
    monkeypatch.setattr(gnome_bridge, "_bridge", SimpleNamespace(registration=registration))
    monkeypatch.setattr(gnome_shell, "is_gnome", lambda: True)
    monkeypatch.setattr(gnome_shell, "is_wayland", lambda: True)
    monkeypatch.setattr(
        gnome_shell, "read_host_receipt", lambda *a, **kw: pytest.fail("stale fallback")
    )
    assert gnome_shell.integration_status([])[0] == "unknown"


def test_real_private_bus_roundtrip():
    if not shutil.which("gjs") or not shutil.which("dbus-run-session"):
        pytest.skip("GJS/private session bus unavailable")
    repo = Path(__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONPATH=str(repo / "src"))
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(repo / "tests/gnome_bridge_bus_driver.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
