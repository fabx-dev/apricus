"""Transaction races and policy guards, without a compositor or WebKit process."""

import json
from types import SimpleNamespace

import pytest

from apricus.interaction import (
    ENTERING,
    EXITING,
    INTERACTIVE,
    WALLPAPER,
    InteractionMailbox,
    WebInteraction,
)


class Fixture:
    def __init__(self):
        self.reason = ""
        self.calls = []
        self.messages = []
        self.timers = {}
        self.seq = 0
        self.machine = WebInteraction(
            lambda: self.reason, self.call, self.messages.append, self.later, self.remove
        )
        self.machine.integration(True)

    def call(self, method, args, callback):
        self.calls.append((method, args, callback))

    def later(self, ms, callback):
        assert ms == 4000
        self.seq += 1
        self.timers[self.seq] = callback
        return self.seq

    def remove(self, timer):
        self.timers.pop(timer, None)

    def expire(self):
        _, callback = self.timers.popitem()
        callback()

    def prepare(self):
        self.calls[0][2]({"pid": 42, "title": self.machine.title}, None)

    def enter(self):
        assert self.machine.begin(42)
        self.prepare()
        assert self.machine.acknowledge(self.machine.token, INTERACTIVE, True)

    def restore(self, safe=True):
        self.machine.acknowledge(self.machine.token, WALLPAPER, safe)
        self.calls[-1][2](True, None)


def test_full_cycle_duplicates_and_late_ack():
    f = Fixture()
    m = f.machine
    assert m.begin(42)
    token = m.token
    assert m.state == ENTERING and not m.begin(42)
    f.prepare()
    assert f.messages[-1]["pid"] == 42
    assert not m.acknowledge("a" * 32, INTERACTIVE, True)
    assert m.acknowledge(token, INTERACTIVE, True)
    assert m.state == INTERACTIVE and not f.timers
    assert not m.acknowledge(token, INTERACTIVE, True)
    f.restore()
    assert m.state == WALLPAPER and not m.token and not f.timers
    assert not m.acknowledge(token, WALLPAPER, True)
    assert not m.end("again")
    assert m.begin(42) and m.token != token
    assert not m.acknowledge(token, INTERACTIVE, True)


@pytest.mark.parametrize(
    "reason", ["wallpaper-paused-or-stopped", "no-webpage", "player-not-ready"]
)
def test_reject_incompatible_player(reason):
    f = Fixture()
    f.reason = reason
    assert not f.machine.begin(42)
    assert not f.calls and not f.messages
    assert f.machine.status()["reason"] == reason


def test_no_integration_and_loss():
    f = Fixture()
    f.machine.integration(False)
    assert not f.machine.begin(42)
    f.machine.integration(True)
    f.enter()
    f.machine.integration(False)
    assert f.machine.state == EXITING
    f.expire()
    assert f.calls[-1][1][1] is False  # player must quarantine, not trust Shell
    f.calls[-1][2](True, None)
    assert not f.machine.status()["available"]


@pytest.mark.parametrize("reason", ["policy", "source-changed", "stop", "player-stopped", "quit"])
def test_exit_before_deferred_action(reason):
    f = Fixture()
    f.enter()
    actions = []
    assert f.machine.end(reason, lambda: actions.append(reason))
    assert f.machine.state == EXITING and not actions
    assert f.machine.end(reason, lambda: actions.append(reason))
    assert len(f.messages) == 2
    f.restore()
    assert actions == [reason]
    assert f.machine.state == WALLPAPER


def test_prepare_timeout_and_delayed_completion_do_not_reenter():
    f = Fixture()
    assert f.machine.begin(42)
    f.expire()
    assert f.machine.state == EXITING
    f.prepare()
    assert [message["state"] for message in f.messages] == [EXITING]
    f.expire()
    f.calls[-1][2](True, None)
    assert f.machine.state == WALLPAPER


@pytest.mark.parametrize("descriptor", [{"pid": 43}, {}, None, {"pid": 42, "title": "other"}])
def test_prepared_window_must_match_player_and_generation(descriptor):
    f = Fixture()
    f.machine.begin(42)
    f.calls[0][2](descriptor, None)
    assert f.machine.state == EXITING
    assert all(message["state"] != ENTERING for message in f.messages)


def test_policy_changes_during_prepare_or_ack():
    for before_prepare in (True, False):
        f = Fixture()
        f.machine.begin(42)
        if before_prepare:
            f.reason = "wallpaper-paused-or-stopped"
        f.prepare()
        f.reason = "wallpaper-paused-or-stopped"
        assert not f.machine.acknowledge(f.machine.token, INTERACTIVE, True)
        assert f.machine.state == EXITING


def test_stale_restore_callback_cannot_finish_new_transaction():
    f = Fixture()
    f.enter()
    f.machine.acknowledge(f.machine.token, WALLPAPER, True)
    old_callback = f.calls[-1][2]
    f.expire()  # restore timeout
    assert f.machine.state == WALLPAPER
    f.machine.begin(42)
    token = f.machine.token
    f.machine.end("stop")
    f.machine.acknowledge(token, WALLPAPER, True)
    assert f.machine.state == EXITING
    old_callback(True, None)
    assert f.machine.state == EXITING
    f.calls[-1][2](True, None)
    assert f.machine.state == WALLPAPER


def test_mailbox_only_wakes_on_change_and_cancels_single_reader():
    mailbox = InteractionMailbox()
    got, errors = [], []
    mailbox.watch(0, lambda *args: got.append(args), errors.append)
    assert got[-1][0] == 1
    mailbox.watch(1, lambda *args: got.append(args), errors.append)
    assert len(got) == 1
    mailbox.watch(1, lambda *args: got.append(args), errors.append)
    assert errors == ["replaced"]
    mailbox.update({"state": ENTERING})
    assert got[-1] == (2, {"state": ENTERING})
    mailbox.watch(2, lambda *args: got.append(args), errors.append)
    mailbox.close()
    mailbox.close()
    assert errors == ["replaced", "closed"]


def test_server_eligibility_preserves_manual_latches(monkeypatch):
    from apricus import server
    from apricus.commons import CONFIG_KEY_MODE, MODE_WEBPAGE
    from apricus.policy import PlaybackPolicyEngine

    obj = server.ApricusServer.__new__(server.ApricusServer)
    obj.config = {CONFIG_KEY_MODE: MODE_WEBPAGE}
    obj.policy = PlaybackPolicyEngine()
    obj.player_process = SimpleNamespace(is_alive=lambda: True, pid=42)
    obj._player_ready_event = SimpleNamespace(is_set=lambda: True)
    obj._bridge = None
    obj._interaction().integration(True)
    assert obj._web_interaction_eligible() == ""
    for operation in (obj.policy.request_manual_pause, obj.policy.request_manual_stop):
        operation()
        before = obj.policy.snapshot()
        assert not obj.web_interact()
        assert obj.policy.snapshot() == before
        assert json.loads(obj.web_interaction_status)["reason"] == "wallpaper-paused-or-stopped"


@pytest.fixture
def web_player(monkeypatch):
    from apricus.player import web_player as module

    class Window:
        def __init__(self):
            self.title = "Apricus"
            self.focus = False
            self.below = True
            self.above = False
            self.decorated = False
            self.visible = True
            self.view_visible = True
            self._WebWindow__webview = SimpleNamespace(
                get_visible=lambda: self.view_visible,
                get_uri=lambda: "file:///test.html",
                grab_focus=lambda: None,
            )

        def __getattr__(self, name):
            names = {"accept_focus": "focus", "keep_below": "below", "keep_above": "above"}
            if name.startswith("get_"):
                return lambda: getattr(self, names.get(name[4:], name[4:]))
            if name.startswith("set_"):
                return lambda value: setattr(self, names.get(name[4:], name[4:]), value)
            raise AttributeError(name)

        def get_realized(self):
            return True

        def get_window(self):
            return SimpleNamespace(get_state=lambda: module.Gdk.WindowState.BELOW)

        def hide(self):
            self.visible = False

    window = Window()
    monkeypatch.setattr(
        module.Gdk.Display, "get_default", lambda: SimpleNamespace(flush=lambda: None)
    )
    monkeypatch.setattr(module, "ConfigUtil", lambda: SimpleNamespace(load=lambda: {}))
    monitor = type("Monitor", (), {"is_primary": lambda _: True})()
    player = SimpleNamespace(_paused=False, windows={monitor: window})
    player.web_interaction_restore = lambda *args: module.WebPlayer.web_interaction_restore(
        player, *args
    )
    return module, player, window


def test_web_player_keeps_same_window_and_view_and_restores_once(web_player):
    import os

    module, player, window = web_player
    view = window._WebWindow__webview
    token = "a" * 32
    descriptor = module.WebPlayer.web_interaction_prepare(player, token, os.getpid())
    assert json.loads(descriptor) == {"pid": os.getpid(), "title": f"Apricus Web [{token}]"}
    assert window.focus and not window.below
    assert module.WebPlayer.web_interaction_prepare(player, token, os.getpid()) == descriptor
    assert player.web_interaction_restore(token, True, os.getpid())
    assert player.web_interaction_restore(token, True, os.getpid())
    assert window.title == "Apricus" and not window.focus and window.below and window.visible
    assert window._WebWindow__webview is view
    assert list(player.windows.values()) == [window]
    with pytest.raises(ValueError, match="cancelled"):
        module.WebPlayer.web_interaction_prepare(player, token, os.getpid())


@pytest.mark.parametrize("invalid", ["paused", "frozen", "stopped", "wrong-pid"])
def test_player_refuses_static_or_wrong_identity(web_player, monkeypatch, invalid):
    import os

    module, player, window = web_player
    if invalid == "paused":
        player._paused = True
    elif invalid == "frozen":
        window.view_visible = False
    elif invalid == "stopped":
        monkeypatch.setattr(
            module, "ConfigUtil", lambda: SimpleNamespace(load=lambda: {"is_stopped": True})
        )
    with pytest.raises(ValueError):
        module.WebPlayer.web_interaction_prepare(
            player, "a" * 32, os.getpid() + (invalid == "wrong-pid")
        )
    assert window.title == "Apricus" and not window.focus


def test_unconfirmed_restore_hides_only_original_surface(web_player):
    import os

    module, player, window = web_player
    view = window._WebWindow__webview
    module.WebPlayer.web_interaction_prepare(player, "a" * 32, os.getpid())
    assert player.web_interaction_restore("a" * 32, False, os.getpid())
    assert not window.visible and player._interaction_quarantined
    assert window._WebWindow__webview is view


def test_integration_change_never_recreates_webview(web_player):
    module, player, window = web_player
    calls = []
    player._cancel_web_interaction = lambda: calls.append("cancel")
    player._on_size_changed = lambda: calls.append("geometry")
    module.WebPlayer._recreate_wallpaper_windows(player)
    assert calls == ["cancel", "geometry"]
    assert list(player.windows.values()) == [window]
