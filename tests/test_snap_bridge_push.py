# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Snap bridge-carried M3 push: auth, sequencing, policy feed, no manifest change."""

import pytest

from apricus import server as server_mod
from apricus.gnome_bridge import WindowRegistration
from apricus.policy import PlaybackPolicyEngine


def test_window_registration_rejects_bad_version_and_flags():
    state = WindowRegistration()
    with pytest.raises(ValueError):
        state.register(":1.14", 2, 1, False, False)
    with pytest.raises(ValueError):
        state.register(":1.14", 1, 0, False, False)
    with pytest.raises(ValueError):
        state.register(":1.14", 1, 1, 1, False)
    assert state.current() is None


def test_window_frame_requires_generation_and_strict_order():
    state = WindowRegistration()
    token = state.register(":1.14", 1, 5, True, False)
    assert state.current()["sequence"] == 5
    with pytest.raises(ValueError):
        state.frame(":1.14", token, 5, False, False)
    with pytest.raises(ValueError):
        state.frame(":1.14", token, 3, False, False)
    with pytest.raises(ValueError):
        state.frame(":1.99", token, 6, False, False)
    with pytest.raises(ValueError):
        state.frame(":1.14", "wrong", 6, False, False)
    state.frame(":1.14", token, 6, False, True)
    assert state.current() == {
        "generation": token,
        "sequence": 6,
        "fullscreen": False,
        "maximized": True,
    }
    state.unregister(":1.14", token, 7)
    assert state.current() is None
    with pytest.raises(ValueError):
        state.frame(":1.14", token, 8, False, False)


def test_window_registration_new_generation_rebaselines():
    state = WindowRegistration()
    first = state.register(":1.14", 1, 50, True, True)
    state.unregister(":1.14", first, 51)
    second = state.register(":1.14", 1, 1, False, False)
    assert second != first
    assert state.current()["sequence"] == 1


def _stubbed_server(monkeypatch):
    from apricus import utils as utils_mod

    monkeypatch.setattr(utils_mod, "is_snap", lambda: True)
    obj = server_mod.ApricusServer.__new__(server_mod.ApricusServer)
    obj.config = {"is_pause_when_maximized": True, "is_mute_when_maximized": False}
    obj.policy = PlaybackPolicyEngine()
    obj._auto_fed = {}
    obj._window_state = None
    obj._window_source = "none"
    obj._bridge_push = {"generation": None, "seq": 0}
    obj._save_config = lambda: None
    import apricus.server as srv

    monkeypatch.setattr(srv, "get_instance", lambda _name: None)
    return obj


def test_bridge_frame_feeds_policy_without_second_debounce(monkeypatch):
    obj = _stubbed_server(monkeypatch)
    applied = []
    obj._apply_policy_output = applied.append
    assert obj.window_push_frame("a" * 32, 1, True, False) is True
    assert obj._window_source == "snap-bridge-push"
    assert obj.policy.compute().desired == "paused"
    assert applied and applied[-1].desired == "paused"
    # Duplicate and obsolete sequences are dropped, not re-fed.
    assert obj.window_push_frame("a" * 32, 1, True, False) is False
    assert obj.window_push_frame("a" * 32, 0, False, False) is False
    assert len(applied) == 1
    # Atomic snapshot: fullscreen clears while maximized sets, no resume gap.
    obj.window_push_frame("a" * 32, 2, False, True)
    assert obj.policy.compute().desired == "paused"


def test_bridge_loss_freezes_without_auto_resume(monkeypatch):
    obj = _stubbed_server(monkeypatch)
    applied = []
    obj._apply_policy_output = applied.append
    obj.window_push_frame("b" * 32, 1, True, False)
    assert obj.policy.compute().desired == "paused"
    count = len(applied)
    assert obj.window_push_lost("b" * 32) is True
    assert obj.policy.snapshot()["push_alive"] is False
    assert obj.policy.compute().desired == "paused"
    assert len(applied) == count
    # Unknown generation is ignored.
    assert obj.window_push_lost("c" * 32) is False


def test_bridge_frame_rejected_off_snap_and_malformed(monkeypatch):
    from apricus import utils as utils_mod

    obj = _stubbed_server(monkeypatch)
    monkeypatch.setattr(utils_mod, "is_snap", lambda: False)
    assert obj.window_push_frame("d" * 32, 1, True, False) is False
    assert obj._window_source == "none"
    monkeypatch.setattr(utils_mod, "is_snap", lambda: True)
    with pytest.raises(ValueError):
        obj.window_push_frame("short", 1, True, False)
    with pytest.raises(ValueError):
        obj.window_push_frame("d" * 32, 1, 1, False)
    with pytest.raises(ValueError):
        obj.window_push_frame("d" * 32, "NaN-seq", True, False)


def test_snap_ipc_allows_bridge_push_methods():
    from apricus import snap_ipc

    assert "window_push_frame" in snap_ipc.ServiceProxy._METHODS
    assert "window_push_lost" in snap_ipc.ServiceProxy._METHODS
    assert "gnome_bridge_status" in snap_ipc.ServiceProxy._METHODS


def _sensor_server(monkeypatch, fake_bus=None):
    import apricus.backends as backends
    from apricus import utils as utils_mod
    from apricus.backends import ExtensionPushWindowStateBackend as Push

    monkeypatch.setattr(utils_mod, "is_snap", lambda: True)
    monkeypatch.setattr(utils_mod, "is_gnome", lambda: True)
    monkeypatch.setattr(utils_mod, "is_wayland", lambda: True)
    monkeypatch.setattr(Push, "try_probe", lambda _cb, **_kw: None)

    class _Null:
        name = "null"

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(backends, "create_window_state_backend", lambda _cb: _Null())
    obj = server_mod.ApricusServer.__new__(server_mod.ApricusServer)
    obj.config = {"is_pause_when_maximized": True, "is_mute_when_maximized": False}
    obj.policy = PlaybackPolicyEngine()
    obj._auto_fed = {}
    obj._window_state = None
    obj._window_source = "none"
    obj._bridge_push = {"generation": None, "seq": 0}
    obj._bridge = None
    obj._save_config = lambda: None
    import apricus.server as srv

    monkeypatch.setattr(srv, "get_instance", lambda _name: None)
    return obj


def test_server_owns_bridge_on_snap_and_feeds_frames_directly(monkeypatch):
    from apricus import gnome_bridge

    started = {}
    stopped = []

    def fake_start(changed, on_frame, on_lost):
        started["callbacks"] = (changed, on_frame, on_lost)

        class FakeBridge:
            def stop(self):
                stopped.append(True)

        return FakeBridge()

    monkeypatch.setattr(gnome_bridge, "start", fake_start)
    monkeypatch.setattr(gnome_bridge, "stop", lambda: stopped.append(True))
    obj = _sensor_server(monkeypatch)
    applied = []
    obj._apply_policy_output = applied.append
    obj._start_environment_sensors()
    assert "callbacks" in started
    assert obj._bridge is not None
    # Frames arrive in-process: no GUI hop, no second debounce.
    obj._on_bridge_window_frame(True, False, 1, "e" * 32)
    assert obj._window_source == "snap-bridge-push"
    assert obj.policy.compute().desired == "paused"
    assert applied and applied[-1].desired == "paused"
    # Loss freezes without auto-resume.
    count = len(applied)
    obj._on_bridge_window_lost("e" * 32)
    assert obj._window_source != "snap-bridge-push"
    assert obj.policy.compute().desired == "paused"
    assert len(applied) == count
    # Unknown generation loss is ignored.
    assert obj.window_push_lost("f" * 32) is False


def test_server_bridge_status_property_shape(monkeypatch):
    from apricus import gnome_bridge

    obj = _sensor_server(monkeypatch)
    monkeypatch.setattr(gnome_bridge, "status", lambda: None)
    assert obj.gnome_bridge_status() is None
    monkeypatch.setattr(gnome_bridge, "status", lambda: ("active", {"bridge": True, "adopted": 3}))
    assert obj.gnome_bridge_status() == ["active", {"bridge": True, "adopted": 3}]


def test_snap_policy_support_requires_verified_bridge():
    from apricus.gui.policy_support import window_policy_support

    enabled, _ = window_policy_support(
        wayland=True, gnome=True, snap=True, flatpak=False, verified=False
    )
    assert enabled is False
    enabled, message = window_policy_support(
        wayland=True, gnome=True, snap=True, flatpak=False, verified=True
    )
    assert enabled is True
    assert "extension" in message


def test_bridge_window_dispatch_rejects_stranger(monkeypatch):

    from tests.test_gnome_bridge import Invocation, fake_bridge

    bridge, bus, glib = fake_bridge(monkeypatch, [":1.14", ":1.14", ":1.14", ":1.14", ":1.14"])
    bridge.window_registration = WindowRegistration(lambda: bridge._window_changed())
    bridge._window_notified = None
    frames = []
    lost = []
    bridge._on_window_frame = lambda *args: frames.append(args)
    bridge._on_window_lost = lambda *args: lost.append(args)
    invocation = Invocation()
    bridge._call(
        bus,
        ":1.99",
        None,
        None,
        "WindowRegister",
        glib.Variant("(uubb)", (1, 1, True, False)),
        invocation,
    )
    assert "not current" in invocation.error
    assert bridge.window_registration.current() is None
    assert frames == [] and lost == []
    good = Invocation()
    bridge._call(
        bus,
        ":1.14",
        None,
        None,
        "WindowRegister",
        glib.Variant("(uubb)", (1, 1, True, False)),
        good,
    )
    assert not good.error
    assert bridge.window_push_active() is True
    assert frames and frames[0][:2] == (True, False)
    # Unknown methods stay rejected; the status channel is unaffected.
    unknown = Invocation()
    bridge._call(bus, ":1.14", None, None, "Nope", glib.Variant("()", ()), unknown)
    assert "unknown method" in unknown.error
    assert bridge.registration.status() is None


def test_pipe_ready_needs_no_shm_and_survives_pickling():
    import pickle

    from apricus.server import _PipeReady

    ready = _PipeReady()
    assert ready.is_set() is False
    clone = pickle.loads(pickle.dumps(ready))
    clone.set()
    assert ready.is_set() is True
    assert clone.is_set() is True
    # Idempotent: no second byte, no block, no error.
    clone.set()
    assert ready.is_set() is True


def test_window_lease_lapse_rebaselines_without_reject():
    clocks = [100.0, 100.0]
    state = WindowRegistration(clock=lambda: clocks[0], monotonic=lambda: clocks[1])
    token = state.register(":1.14", 1, 7, True, False)
    clocks[:] = [195.0, 195.0]  # >90 s of compositor silence
    # First frame after quiet: accepted in place, no vanish/fallback churn.
    state.frame(":1.14", token, 8, False, True)
    assert state.current()["sequence"] == 8
    assert state.current()["fullscreen"] is False
    assert state.current()["maximized"] is True
    # Stale, foreign and post-unregister frames still fail closed.
    import pytest

    with pytest.raises(ValueError):
        state.frame(":1.14", token, 8, False, True)
    with pytest.raises(ValueError):
        state.frame(":1.14", "wrong", 9, False, True)
    with pytest.raises(ValueError):
        state.frame(":1.99", token, 9, False, True)
    state.unregister(":1.14", token, 9)
    with pytest.raises(ValueError):
        state.frame(":1.14", token, 10, False, False)
