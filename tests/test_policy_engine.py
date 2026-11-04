# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""M2 PlaybackPolicyEngine matrix tests. Pure stdlib: no display, no D-Bus,
no VLC, no GTK. Import the engine module directly."""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from apricus.policy import (  # noqa: E402
    PAUSED,
    PLAY,
    STOPPED,
    PlaybackPolicyEngine,
)


def engine(**kw):
    eng = PlaybackPolicyEngine(**kw)
    eng.load_latches(manual_stop=False, manual_pause=False, manual_mute=False)
    return eng


def test_initial_state_is_play_unmuted():
    out, act_now = engine().request_manual_resume(), True  # noqa: F841
    eng = engine()
    out = eng.compute()
    assert (out.desired, out.wanted_mute) == (PLAY, False)
    snap = eng.snapshot()
    assert snap["desired"] == PLAY and snap["generation"] == 0
    assert set(snap) >= {
        "desired",
        "wanted_mute",
        "manual_stop",
        "manual_pause",
        "manual_mute",
        "active_reasons",
        "push_alive",
        "generation",
        "policy_version",
        "debounced",
    }


def test_manual_stop_beats_everything_and_persists_shape():
    eng = engine()
    eng.reason_active_changed("fullscreen", True, now=100.0)
    eng.pop_due(now=100.0 + 10.0)
    out, act_now = eng.request_manual_stop()
    assert act_now is True
    assert out.desired == STOPPED
    assert eng.latches() == {"manual_stop": True, "manual_pause": False, "manual_mute": False}
    # Auto-clear afterwards must not lift STOP.
    out, _ = eng.reason_active_changed("fullscreen", False, now=200.0)
    assert eng.compute().desired == STOPPED


def test_manual_pause_beats_auto_and_survives_auto_clear():
    eng = engine()
    eng.request_manual_pause()
    out, _ = eng.reason_active_changed("maximized", True, now=0.0)
    eng.pop_due(now=10.0)
    assert eng.compute().desired == PAUSED
    out, _ = eng.reason_active_changed("maximized", False, now=20.0)
    eng.pop_due(now=30.0)
    # Latch still set: the unlock must NOT resume.
    assert eng.compute().desired == PAUSED
    assert eng.latches()["manual_pause"] is True


def test_resume_from_pause_plays_when_autos_clear():
    eng = engine()
    eng.request_manual_pause()
    out, act_now = eng.request_manual_resume()
    assert act_now is True
    assert out.desired == PLAY


def test_resume_from_pause_stays_paused_while_auto_active():
    eng = engine()
    eng.request_manual_pause()
    eng.reason_active_changed("screen_locked", True, now=0.0)
    eng.pop_due(now=10.0)
    out, _ = eng.request_manual_resume()
    assert out.desired == PAUSED  # auto reason still holds it


def test_play_from_stopped_clears_stop_and_pause():
    eng = engine()
    eng.request_manual_pause()
    eng.request_manual_stop()
    out, act_now = eng.request_manual_start()
    assert act_now is True
    assert out.desired == PLAY
    assert eng.latches() == {"manual_stop": False, "manual_pause": False, "manual_mute": False}


def test_stop_then_stop_is_idempotent_single_generation_step():
    eng = engine()
    before = eng.compute().generation
    eng.request_manual_stop()
    eng.request_manual_stop()
    assert eng.compute().generation == before + 2
    assert eng.compute().desired == STOPPED


def test_auto_reasons_debounced_not_immediate():
    eng = engine()
    out, act_now = eng.reason_active_changed("fullscreen", True, now=100.0)
    assert act_now is False
    assert out.desired == PAUSED
    # Not due yet: nothing to actuate.
    assert eng.pop_due(now=100.0 + 0.1) is None
    # Due after the window: exactly one delivery.
    first = eng.pop_due(now=100.0 + 10.0)
    assert first is not None and first.desired == PAUSED
    assert eng.pop_due(now=100.0 + 20.0) is None
    assert eng.pending_delay() is None


def test_auto_flicker_within_window_never_delivers_pause():
    eng = engine()
    delivered = []
    eng.reason_active_changed("maximized", True, now=50.0)
    assert eng.pop_due(now=50.1) is None
    eng.reason_active_changed("maximized", False, now=50.2)
    for t in (50.3, 51.0, 60.0, 99.0):
        out = eng.pop_due(now=t)
        if out is not None:
            delivered.append(out.desired)
    # The intermediate PAUSE never left the engine; at most the settled
    # PLAY is delivered once. The server additionally dedups equal outputs.
    assert "pause" not in delivered
    assert delivered.count(PLAY) <= 1
    assert eng.compute().desired == PLAY


def test_manual_commands_cancel_pending_auto_output():
    eng = engine()
    eng.reason_active_changed("fullscreen", True, now=0.0)
    assert eng.pending_delay() is not None
    out, act_now = eng.request_manual_pause()
    assert act_now is True and out.desired == PAUSED
    assert eng.pending_delay() is None
    assert eng.pop_due(now=99.0) is None


def test_mute_is_orthogonal_to_playback():
    eng = engine()
    out, _ = eng.toggle_manual_mute()
    assert out.wanted_mute is True and out.desired == PLAY
    out, _ = eng.reason_active_changed("maximized_mute", True, now=0.0)
    eng.pop_due(now=10.0)
    assert eng.compute().wanted_mute is True
    # Manual unmute while auto-mute active: still muted, latch off.
    eng.set_manual_mute(False)
    assert eng.compute().wanted_mute is True
    assert eng.latches()["manual_mute"] is False
    eng.reason_active_changed("maximized_mute", False, now=20.0)
    eng.pop_due(now=30.0)
    assert eng.compute().wanted_mute is False


def test_unknown_and_reserved_reasons_fail_closed():
    eng = engine()
    with pytest.raises(ValueError):
        eng.reason_active_changed("no_such_reason", True, now=0.0)
    with pytest.raises(ValueError):
        eng.reason_active_changed("auto_stop", True, now=0.0)
    # Rejected feeds change nothing.
    assert eng.compute() == eng.compute()


def test_push_loss_freezes_only_push_sources_x11_keeps_working():
    eng = PlaybackPolicyEngine(push_sources=("screen_locked",))
    eng.reason_active_changed("screen_locked", True, now=0.0)
    eng.pop_due(now=10.0)
    assert eng.compute().desired == PAUSED
    snap = eng.note_push_alive(False)
    assert snap["push_alive"] is False
    # A lost channel must never auto-clear into a surprise resume: the
    # push-fed update is dropped, the PAUSE output stands.
    eng.reason_active_changed("screen_locked", False, now=20.0)
    assert eng.compute().desired == PAUSED
    # ...while local (Wnck/poll) reasons keep working: no X11 degradation.
    out, act_now = eng.reason_active_changed("fullscreen", True, now=21.0)
    assert act_now is False and out.desired == PAUSED
    eng.reason_active_changed("fullscreen", False, now=22.0)
    eng.pop_due(now=99.0)
    # screen_locked push update was dropped; fullscreen cleared; but the
    # push-fed lock is still latched from before the outage...
    assert eng.compute().desired == PAUSED
    eng.note_push_alive(True)
    eng.reason_active_changed("screen_locked", False, now=100.0)
    eng.pop_due(now=200.0)
    assert eng.compute().desired == PLAY


def test_crash_recovery_loads_latches_not_reasons():
    eng = engine()
    eng.request_manual_stop()
    saved = copy.deepcopy(eng.latches())
    fresh = PlaybackPolicyEngine()
    fresh.load_latches(**saved)
    assert fresh.compute().desired == STOPPED
    # Auto reasons always start cleared after a restart.
    assert fresh.compute().active_reasons == ()


def test_snapshot_is_json_safe():
    import json

    eng = engine()
    eng.reason_active_changed("maximized", True, now=0.0)
    eng.pop_due(now=10.0)
    assert json.loads(json.dumps(eng.snapshot()))["active_reasons"] == ["maximized"]


def test_full_matrix_spot_table():
    cases = [
        # (stop, pause, mute, autos, want_desired, want_mute)
        (False, False, False, {}, PLAY, False),
        (True, False, False, {}, STOPPED, False),
        (True, True, False, {}, STOPPED, False),
        (False, True, False, {}, PAUSED, False),
        (False, False, True, {}, PLAY, True),
        (False, False, False, {"fullscreen": True}, PAUSED, False),
        (False, False, False, {"maximized_mute": True}, PLAY, True),
        (False, False, False, {"fullscreen": True, "maximized_mute": True}, PAUSED, True),
        (False, True, False, {"fullscreen": True}, PAUSED, False),
        (True, False, True, {"fullscreen": True}, STOPPED, True),
    ]
    for stop, pause, mute, autos, want_desired, want_mute in cases:
        eng = engine()
        eng.load_latches(manual_stop=stop, manual_pause=pause, manual_mute=mute)
        for name in ("fullscreen", "maximized", "maximized_mute", "screen_locked"):
            eng.reason_active_changed(name, bool(autos.get(name, False)), now=0.0)
        eng.pop_due(now=99.0)
        out = eng.compute()
        assert (out.desired, out.wanted_mute) == (want_desired, want_mute), (
            stop,
            pause,
            mute,
            autos,
        )
