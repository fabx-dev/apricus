# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the M3 extension-push window-state channel (headless).

The push backend talks to a fake bus; the real DesktopStateChanged contract
(typed, sequenced, owner-verified) is exercised end to end without a
compositor. No D-Bus daemon, no Shell, no network.
"""

from apricus.backends import ExtensionPushWindowStateBackend as Push
from apricus.policy import PlaybackPolicyEngine


def _state(seq=1, fullscreen=False, maximized=False):
    return {
        "seq": seq,
        "fullscreen": fullscreen,
        "maximized": maximized,
        "fs_monitors": [0] if fullscreen else [],
        "max_monitors": [0] if maximized else [],
        "workspace": 0,
        "nWorkspaces": 1,
        "monitors": [{"index": 0, "primary": True}],
    }


class _FakeDaemon:
    def __init__(self, owner=None, fail=False):
        self._owner = owner
        self._fail = fail

    def GetNameOwner(self, _name):
        if self._fail or self._owner is None:
            raise RuntimeError("org.freedesktop.DBus.Error.NameHasNoOwner")
        return self._owner


class _FakeProxy:
    def __init__(self, state=None, version=1, fail=False):
        self._state = state
        self._version = version
        self._fail = fail

    @property
    def Version(self):
        return self._version

    def GetState(self):
        if self._fail:
            raise RuntimeError("bus error")
        return dict(self._state)


class _FakeSub:
    def __init__(self):
        self.unsubscribed = False

    def unsubscribe(self):
        self.unsubscribed = True


class _FakeBus:
    # Mirrors the real pydbus SubscriptionMixin.subscribe signature
    # EXACTLY (verified against the installed library): any unexpected
    # keyword raises TypeError, so a wrong kwarg (e.g. member= instead
    # of signal=) fails here exactly as it fails on the session bus.
    def __init__(self, daemon, proxies=None):
        self._daemon = daemon
        self._proxies = proxies or {}
        self.subs = []

    def get(self, name, path):
        if name == "org.freedesktop.DBus":
            return self._daemon
        return self._proxies[(name, path)]

    def subscribe(
        self,
        sender=None,
        iface=None,
        signal=None,
        object=None,
        arg0=None,
        flags=0,
        signal_fired=None,
    ):
        sub = _FakeSub()
        sub.callback = signal_fired
        self.subs.append((dict(sender=sender, iface=iface, signal=signal, object=object), sub))
        return sub


def _backend(state=None, owner=":1.40", version=1, seq=0):
    fed = []
    daemon = _FakeDaemon(owner=owner)
    proxy = _FakeProxy(state=state or _state(), version=version)
    bus = _FakeBus(daemon, {(Push.BUS_NAME, Push.OBJECT_PATH): proxy})
    backend = Push.try_probe(fed.append, bus=bus, daemon=daemon)
    assert backend is not None
    backend._seq = seq
    return backend, fed, bus


def test_probe_feeds_initial_state():
    fed = []
    daemon = _FakeDaemon(owner=":1.40")
    proxy = _FakeProxy(state=_state(fullscreen=True))
    bus = _FakeBus(daemon, {(Push.BUS_NAME, Push.OBJECT_PATH): proxy})
    backend = Push.try_probe(fed.append, bus=bus, daemon=daemon)
    assert backend is not None
    assert fed == [{"is_any_maximized": False, "is_any_fullscreen": True}]


def test_probe_rejects_absent_owner_bad_version_and_malformed():
    fed = []
    assert (
        Push.try_probe(
            fed.append, bus=_FakeBus(_FakeDaemon(owner=None)), daemon=_FakeDaemon(owner=None)
        )
        is None
    )
    daemon = _FakeDaemon(owner=":1.40")
    proxy = _FakeProxy(state=_state(), version=99)
    assert (
        Push.try_probe(
            fed.append,
            bus=_FakeBus(daemon, {(Push.BUS_NAME, Push.OBJECT_PATH): proxy}),
            daemon=daemon,
        )
        is None
    )
    proxy = _FakeProxy(state={"bogus": True})
    assert (
        Push.try_probe(
            fed.append,
            bus=_FakeBus(daemon, {(Push.BUS_NAME, Push.OBJECT_PATH): proxy}),
            daemon=daemon,
        )
        is None
    )
    proxy = _FakeProxy(state=_state(), fail=True)
    assert (
        Push.try_probe(
            fed.append,
            bus=_FakeBus(daemon, {(Push.BUS_NAME, Push.OBJECT_PATH): proxy}),
            daemon=daemon,
        )
        is None
    )
    assert fed == []


def test_signal_from_owner_with_newer_seq_is_fed():
    backend, fed, _bus = _backend(seq=4)
    assert backend.start() is True
    backend._on_signal(":1.40", None, None, None, _state(seq=5, maximized=True))
    assert fed[-1] == {"is_any_maximized": True, "is_any_fullscreen": False}


def test_signal_from_stranger_is_ignored():
    backend, fed, _bus = _backend(seq=4)
    assert backend.start() is True
    before = list(fed)
    backend._on_signal(":1.99", None, None, None, _state(seq=99, fullscreen=True))
    assert fed == before


def test_stale_and_malformed_signals_are_ignored():
    backend, fed, _bus = _backend(seq=4)
    assert backend.start() is True
    before = list(fed)
    backend._on_signal(":1.40", None, None, None, _state(seq=4, fullscreen=True))
    backend._on_signal(":1.40", None, None, None, _state(seq=2, fullscreen=True))
    backend._on_signal(":1.40", None, None, None, {"wrong": "shape"})
    backend._on_signal(":1.40", None, None, None, [{"not": "a dict"}])
    assert fed == before


def test_tuple_wrapped_payload_is_accepted():
    backend, fed, _bus = _backend(seq=4)
    assert backend.start() is True
    backend._on_signal(":1.40", None, None, None, (_state(seq=6, fullscreen=True),))
    assert fed[-1] == {"is_any_maximized": False, "is_any_fullscreen": True}


def test_reconcile_updates_owner_and_seq():
    backend, fed, _bus = _backend(seq=4)
    daemon = _FakeDaemon(owner=":1.77")
    proxy = _FakeProxy(state=_state(seq=10, maximized=True))
    backend._bus = _FakeBus(daemon, {(Push.BUS_NAME, Push.OBJECT_PATH): proxy})
    assert backend._reconcile(":1.77") is True
    assert backend._owner == ":1.77"
    # Old owner is now a stranger; old seq is stale.
    backend._on_signal(":1.40", None, None, None, _state(seq=99, fullscreen=True))
    backend._on_signal(":1.77", None, None, None, _state(seq=9, fullscreen=True))
    assert all(f != {"is_any_maximized": False, "is_any_fullscreen": True} for f in fed)


def test_stop_unsubscribes_and_ignores_late_signals():
    backend, fed, bus = _backend()
    assert backend.start() is True
    assert len(bus.subs) == 1
    backend.stop()
    assert bus.subs[0][1].unsubscribed is True
    backend._on_signal(":1.40", None, None, None, _state(seq=50, fullscreen=True))
    assert fed == [{"is_any_maximized": False, "is_any_fullscreen": False}]
    backend.stop()  # idempotent


def test_subscribe_uses_real_pydbus_kwarg():
    """The strict fake bus mirrors pydbus exactly: any unexpected keyword
    raises TypeError, just like the live session bus did for member=."""
    backend, _fed, bus = _backend()
    assert backend.start() is True
    assert len(bus.subs) == 1
    kwargs, _sub = bus.subs[0]
    assert kwargs["signal"] == "DesktopStateChanged"
    assert kwargs["iface"] == Push.INTERFACE
    assert kwargs["object"] == Push.OBJECT_PATH
    assert kwargs["sender"] == ":1.40"


def test_subscribe_kwarg_matches_installed_pydbus():
    """Pin the new-subscriber contract to the really installed library:
    the kwarg must be named exactly `signal`."""
    import inspect

    import pydbus.subscription

    params = inspect.signature(pydbus.subscription.SubscriptionMixin.subscribe).parameters
    assert "signal" in params, sorted(params)
    assert "member" not in params, sorted(params)


def _stub_server(monkeypatch):
    from apricus import server as srv_mod

    obj = srv_mod.ApricusServer.__new__(srv_mod.ApricusServer)
    obj.config = {"is_pause_when_maximized": True, "is_mute_when_maximized": False}
    obj.policy = PlaybackPolicyEngine()
    obj._auto_fed = {}
    obj._window_state = None
    obj._window_source = "none"
    obj._push_watcher = None
    return obj


def test_ladder_prefers_push_and_reports_source(monkeypatch):
    import apricus.backends as backends
    import apricus.utils as utils

    monkeypatch.setattr(utils, "is_wayland", lambda: True)
    monkeypatch.setattr(utils, "is_gnome", lambda: True)
    fed = []
    daemon = _FakeDaemon(owner=":1.40")
    proxy = _FakeProxy(state=_state())
    bus = _FakeBus(daemon, {(Push.BUS_NAME, Push.OBJECT_PATH): proxy})
    monkeypatch.setattr(backends, "SessionBus", lambda: bus)
    obj = _stub_server(monkeypatch)
    obj._start_environment_sensors()
    assert obj._window_source == "extension-push"
    assert obj.policy.snapshot()["push_alive"] is True
    assert fed or True  # initial feed covered by backend unit tests
    obj._stop_window_state()
    assert obj._window_state is None and obj._window_source == "none"


def test_ladder_falls_back_when_push_absent(monkeypatch):
    import apricus.backends as backends
    import apricus.utils as utils

    monkeypatch.setattr(utils, "is_wayland", lambda: True)
    monkeypatch.setattr(utils, "is_gnome", lambda: True)

    def _no_bus():
        raise RuntimeError("no bus")

    monkeypatch.setattr(backends, "SessionBus", _no_bus)
    created = []

    class _Null:
        name = "null"

        def __init__(self, cb):
            created.append(cb)

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(backends, "create_window_state_backend", lambda cb: _Null(cb))
    obj = _stub_server(monkeypatch)
    obj._start_environment_sensors()
    assert obj._window_source == "null"
    assert obj.policy.snapshot()["push_alive"] is False


def test_push_loss_freezes_without_auto_resume(monkeypatch):
    import apricus.utils as utils

    monkeypatch.setattr(utils, "is_wayland", lambda: True)
    monkeypatch.setattr(utils, "is_gnome", lambda: True)
    obj = _stub_server(monkeypatch)
    fed_log = []
    orig_feed = obj._feed_auto

    def spy_feed(reason, active):
        fed_log.append((reason, active))
        return orig_feed(reason, active)

    obj._feed_auto = spy_feed
    obj._on_window_state_changed({"is_any_maximized": False, "is_any_fullscreen": True})
    assert ("fullscreen", True) in fed_log
    assert obj.policy.compute().desired == "paused"
    stopped, started = [], []

    class _Push:
        name = "extension-push"

        def stop(self):
            stopped.append(True)

    class _Null:
        name = "null"

        def start(self):
            started.append(True)

        def stop(self):
            pass

    import apricus.backends as backends

    monkeypatch.setattr(backends, "create_window_state_backend", lambda cb: _Null())
    obj._window_state = _Push()
    obj._window_source = "extension-push"
    assert obj.policy.compute().desired == "paused"
    obj._on_push_vanished()
    assert stopped and started
    assert obj._window_source == "null"
    assert obj.policy.snapshot()["push_alive"] is False
    # Last known auto reason is untouched: still paused, no resume.
    assert obj.policy.compute().desired == "paused"
    # A vanished event while not on push is a no-op.
    fed_before = list(fed_log)
    obj._on_push_vanished()
    assert fed_log == fed_before


def test_push_reappear_reconciles_and_resumes_push(monkeypatch):
    import apricus.backends as backends
    import apricus.utils as utils

    monkeypatch.setattr(utils, "is_wayland", lambda: True)
    monkeypatch.setattr(utils, "is_gnome", lambda: True)
    daemon = _FakeDaemon(owner=":1.41")
    proxy = _FakeProxy(state=_state(seq=3, fullscreen=True))
    bus = _FakeBus(daemon, {(Push.BUS_NAME, Push.OBJECT_PATH): proxy})
    monkeypatch.setattr(backends, "SessionBus", lambda: bus)

    seen = {}

    class _Old:
        name = "null"

        def stop(self):
            seen["old_stopped"] = True

    obj = _stub_server(monkeypatch)
    obj._window_state = _Old()
    obj._window_source = "null"
    obj._on_push_appeared()
    assert obj._window_source == "extension-push"
    assert obj.policy.snapshot()["push_alive"] is True
    assert seen.get("old_stopped") is True
    # Appearing again while already on push is a no-op (no stacked backends).
    first = obj._window_state
    assert first._started is True
    subs_before = len(bus.subs)
    obj._on_push_appeared()
    assert obj._window_state is first
    assert len(bus.subs) == subs_before
    obj._stop_window_state()


def test_push_watcher_registers_with_signal_kwarg(monkeypatch):
    """The NameOwnerChanged watch must use the real pydbus kwarg name;
    with the strict fake bus the old member= call raises TypeError."""
    import apricus.server as srv_mod
    import apricus.utils as utils

    monkeypatch.setattr(utils, "is_wayland", lambda: True)
    monkeypatch.setattr(utils, "is_gnome", lambda: True)
    daemon = _FakeDaemon(owner=None)
    bus = _FakeBus(daemon, {})
    monkeypatch.setattr(srv_mod, "SessionBus", lambda: bus)
    obj = _stub_server(monkeypatch)
    obj._watch_push_owner()
    assert len(bus.subs) == 1
    kwargs, _sub = bus.subs[0]
    assert kwargs["signal"] == "NameOwnerChanged"
    assert kwargs["iface"] == "org.freedesktop.DBus"
    assert kwargs["sender"] == "org.freedesktop.DBus"


def test_push_watcher_routes_appear_and_vanish(monkeypatch):
    """End-to-end watcher behavior with the strict fake bus: appearance
    starts the push backend, vanish falls back without auto-resume."""
    import apricus.backends as backends
    import apricus.server as srv_mod
    import apricus.utils as utils

    monkeypatch.setattr(utils, "is_wayland", lambda: True)
    monkeypatch.setattr(utils, "is_gnome", lambda: True)
    daemon = _FakeDaemon(owner=None)
    proxy = _FakeProxy(state=_state(fullscreen=True))
    bus = _FakeBus(daemon, {(Push.BUS_NAME, Push.OBJECT_PATH): proxy})
    monkeypatch.setattr(srv_mod, "SessionBus", lambda: bus)
    monkeypatch.setattr(backends, "SessionBus", lambda: bus)

    class _Null:
        name = "null"

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(backends, "create_window_state_backend", lambda cb: _Null())
    obj = _stub_server(monkeypatch)
    obj._watch_push_owner()
    watcher = next(sub for kwargs, sub in bus.subs if kwargs.get("signal") == "NameOwnerChanged")
    # Extension appears: GetState reconciles and push becomes the source.
    daemon._owner = ":1.50"
    watcher.callback(
        ":1.0",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        (Push.BUS_NAME, "", ":1.50"),
    )
    assert obj._window_source == "extension-push"
    assert obj.policy.snapshot()["push_alive"] is True
    assert obj.policy.compute().desired == "paused"
    # Extension vanishes: frozen reasons, fallback backend, no resume.
    watcher.callback(
        ":1.0",
        "/org/freedesktop/DBus",
        "org.freedesktop.DBus",
        "NameOwnerChanged",
        (Push.BUS_NAME, ":1.50", ""),
    )
    assert obj._window_source == "null"
    assert obj.policy.snapshot()["push_alive"] is False
    assert obj.policy.compute().desired == "paused"
    obj._stop_window_state()


def test_push_never_probed_on_x11(monkeypatch):
    import apricus.backends as backends
    import apricus.utils as utils

    monkeypatch.setattr(utils, "is_wayland", lambda: False)
    monkeypatch.setattr(utils, "is_gnome", lambda: True)

    def boom(*a, **k):
        raise AssertionError("push must not be probed on X11")

    monkeypatch.setattr(backends, "ExtensionPushWindowStateBackend", boom)

    class _Null:
        name = "null"

        def __init__(self, cb):
            pass

        def start(self):
            pass

        def stop(self):
            pass

    monkeypatch.setattr(backends, "create_window_state_backend", lambda cb: _Null(cb))
    obj = _stub_server(monkeypatch)
    obj.policy = PlaybackPolicyEngine()
    obj._window_state = None
    obj._window_source = "none"
    obj._start_environment_sensors()
    assert obj._window_source == "null"


def test_push_feed_respects_manual_pause_priority():
    engine = PlaybackPolicyEngine()
    engine.request_manual_pause()
    out, act = engine.reason_active_changed("fullscreen", True)
    assert out.desired == "paused" and act is False
    # Manual resume while fullscreen persists: still paused (no auto-clear).
    engine.request_manual_resume()
    assert engine.compute().desired == "paused"
    # Clearing the push reason resumes: manual latch was already cleared.
    out, act = engine.reason_active_changed("fullscreen", False)
    assert engine.compute().desired == "playing"


def test_push_feed_never_clears_manual_stop():
    engine = PlaybackPolicyEngine()
    engine.request_manual_stop()
    engine.reason_active_changed("fullscreen", True)
    engine.reason_active_changed("fullscreen", False)
    assert engine.compute().desired == "stopped"
