"""Local-only M3 regressions: no session bus, Shell or user configuration."""

import pytest

from apricus.gui.policy_support import window_policy_support
from apricus.policy import PlaybackPolicyEngine


def test_real_private_bus_gui_policy_and_getall():
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    assert shutil.which("dbus-run-session"), "private D-Bus test runner required"
    root = Path(__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONPATH=str(root / "src"), M3_PRIVATE_BUS_TEST="1")
    env.pop("DISPLAY", None)
    env.pop("WAYLAND_DISPLAY", None)
    proc = subprocess.run(
        ["dbus-run-session", "--", sys.executable, str(root / "tests/dbus/m3-local.py")],
        env=env,
        capture_output=True,
        text=True,
        timeout=40,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize(
    "wayland,gnome,snap,flatpak,verified,enabled",
    [
        (False, False, False, False, False, True),
        (False, True, True, False, False, True),
        (True, True, False, False, False, True),
        (True, False, False, False, False, False),
        (True, True, True, False, False, False),
        (True, True, True, False, True, True),
        (True, True, False, True, False, False),
        (True, True, False, True, True, True),
    ],
)
def test_packaging_capability(wayland, gnome, snap, flatpak, verified, enabled):
    actual, _message = window_policy_support(
        wayland=wayland, gnome=gnome, snap=snap, flatpak=flatpak, verified=verified
    )
    assert actual is enabled


def test_settled_snapshot_is_atomic_without_second_debounce():
    engine = PlaybackPolicyEngine(clock=lambda: 0)
    output, immediate = engine.reasons_active_changed({"fullscreen": True}, settled=True)
    assert immediate and output.desired == "paused"
    output, immediate = engine.reasons_active_changed(
        {"fullscreen": False, "maximized": True}, settled=True
    )
    assert immediate and output.desired == "paused"
    assert engine.pop_due(now=10) is None
    # Non-push producers still receive their original protection.
    output, immediate = engine.reason_active_changed("maximized", False)
    assert not immediate
    assert engine.pop_due(now=0.1) is None
    assert engine.pop_due(now=0.3).desired == "playing"


def test_preference_setter_recomputes_existing_push_state(monkeypatch):
    from apricus import server

    obj = server.ApricusServer.__new__(server.ApricusServer)
    obj.config = {"is_pause_when_maximized": True, "is_mute_when_maximized": False}
    obj.policy = PlaybackPolicyEngine()
    obj._auto_fed = {}
    obj._window_source = "extension-push"
    applied = []
    obj._apply_policy_output = applied.append
    obj._save_config = lambda: None
    monkeypatch.setattr(server, "get_instance", lambda _name: None)
    obj._on_window_state_changed({"is_any_maximized": True})
    assert applied[-1].desired == "paused"
    obj.is_mute_when_maximized = True
    obj.is_pause_when_maximized = False
    assert applied[-1].desired == "playing" and applied[-1].wanted_mute
    assert obj.policy.snapshot()["active_reasons"] == ["maximized_mute"]
    obj.is_mute_when_maximized = False
    assert not applied[-1].wanted_mute
    # No redundant actuation from duplicate snapshots.
    count = len(applied)
    obj._on_window_state_changed({"is_any_maximized": True})
    assert len(applied) == count
    obj._window_source = "null"
    obj.is_pause_when_maximized = True
    assert len(applied) == count  # do not replay stale source state


def test_preferences_do_not_reload_away_effective_player_mute(monkeypatch):
    from types import SimpleNamespace

    from apricus import server

    obj = server.ApricusServer.__new__(server.ApricusServer)
    obj.config = {"is_pause_when_maximized": True, "is_mute_when_maximized": False}
    obj.policy = PlaybackPolicyEngine()
    obj._auto_fed = {}
    obj._window_source = "extension-push"
    obj._last_applied = None
    obj._save_config = lambda: None
    player = SimpleNamespace(is_mute=False, is_paused_by_user=False)

    def unexpected_reload():
        pytest.fail("server-owned window policy must not reload player config")

    player.reload_config = unexpected_reload
    player.start_playback = lambda: None
    player.pause_playback = lambda: None
    monkeypatch.setattr(server, "get_instance", lambda _name: player)
    obj._on_window_state_changed({"is_any_maximized": True})
    obj.is_mute_when_maximized = True
    obj.is_pause_when_maximized = False
    assert obj.policy.compute().wanted_mute
    assert player.is_mute is True  # a later reload would overwrite this mirror
    obj.is_mute_when_maximized = True  # repeated settings signal is harmless
    assert player.is_mute is True
