# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Central playback policy engine (M2). Pure logic: stdlib only.

No GTK, VLC, D-Bus, GSettings, files or subprocesses are touched here. The
server owns the single engine instance; players, GUI and menu only express
intent through server verbs and read back snapshots. All side effects
(config persistence, player IPC, fades) stay in the caller, driven by the
returned output — which keeps every transition unit-testable headless.

Priority: MANUAL_STOP > MANUAL_PAUSE > auto-STOP (reserved) > auto-PAUSE.
STOP dominates PAUSE. Manual latches are never cleared by automatic
events. Auto reasons never touch the persisted manual keys.

Mute is orthogonal: wanted_mute = manual_mute OR any(auto_mute_active).
"""

import time
from dataclasses import dataclass

# Desired states mirror the D-Bus playback_state vocabulary so snapshots
# and diagnostics compare directly with reported player state.
PLAY = "playing"
PAUSED = "paused"
STOPPED = "stopped"

# Automatic reasons wired in M2. Future producers (M3 extension push, M4
# power listeners) reuse the same entry point with explicitly registered
# effects; unknown reasons are rejected so a typo can never silently
# pause (or unpause) the desktop.
_AUTO_EFFECTS = {
    # reason: (pauses_playback, forces_mute)
    "fullscreen": (True, False),
    "maximized": (True, False),
    "maximized_mute": (False, True),
    "screen_locked": (True, False),
}

# Reserved for a future automatic STOP producer. Declared (and tested as
# inert) so the priority slot exists without any behavior attached.
_RESERVED_AUTO_STOP = ("auto_stop",)

POLICY_VERSION = 1

#: Debounce applied ONLY to automatic reason flaps (flicker). Manual
#: Play/Pause/Stop commands always act immediately.
DEBOUNCE_S = 0.25


@dataclass(frozen=True)
class EngineOutput:
    """Result of one engine computation. Value object: comparable, hashable."""

    desired: str
    wanted_mute: bool
    active_reasons: tuple = ()
    generation: int = 0


@dataclass
class _AutoReason:
    active: bool = False


class PlaybackPolicyEngine:
    """Server-side playback decider. Single instance per server process."""

    def __init__(self, *, clock=None, debounce_s=DEBOUNCE_S, push_sources=()):
        self._clock = clock or time.monotonic
        self._debounce_s = debounce_s
        #: Reasons fed by the (optional) GNOME push channel. While the
        #: channel is down their updates are dropped; local reasons
        #: (Wnck/poll/lock) keep working so X11 is never degraded by a
        #: GNOME-integration outage. Empty in M2; M3 registers push reasons.
        self._push_sources = frozenset(push_sources)
        self._clock = clock or time.monotonic
        self._debounce_s = debounce_s
        self._manual_stop = False
        self._manual_pause = False
        self._manual_mute = False
        self._auto = {name: _AutoReason() for name in _AUTO_EFFECTS}
        self._push_alive = True
        self._generation = 0
        self._pending = None  # (generation, EngineOutput, due_at) or None

    # -- persisted latch state -------------------------------------------
    def load_latches(self, *, manual_stop=False, manual_pause=False, manual_mute=False):
        """Restore manual intent after a server restart. No output emitted;
        the caller applies the initial output explicitly."""
        self._manual_stop = bool(manual_stop)
        self._manual_pause = bool(manual_pause)
        self._manual_mute = bool(manual_mute)
        self._pending = None

    def latches(self):
        return {
            "manual_stop": self._manual_stop,
            "manual_pause": self._manual_pause,
            "manual_mute": self._manual_mute,
        }

    # -- manual intent (always immediate, cancel pending auto output) -----
    def request_manual_pause(self):
        self._manual_pause = True
        return self._mutate(immediate=True)

    def request_manual_resume(self):
        """Clear a manual pause. Resumes only if the policy allows it;
        a latched STOP is untouched (use request_manual_start instead)."""
        self._manual_pause = False
        return self._mutate(immediate=True)

    def request_manual_stop(self):
        self._manual_stop = True
        return self._mutate(immediate=True)

    def request_manual_start(self):
        """Play from STOPPED: clear STOP *and* PAUSE so no stale manual
        pause survives the fresh start (matches M1 observable behavior,
        where the pause flag died with the old player)."""
        self._manual_stop = False
        self._manual_pause = False
        return self._mutate(immediate=True)

    def toggle_manual_mute(self):
        self._manual_mute = not self._manual_mute
        return self._mutate(immediate=True)

    def set_manual_mute(self, muted):
        self._manual_mute = bool(muted)
        return self._mutate(immediate=True)

    # -- automatic reasons (debounced; never touch latches) --------------
    def reason_active_changed(self, reason, active, *, now=None):
        """Feed an automatic reason. Returns (output, act_now). Unknown
        reasons raise ValueError: producers must register effects first.
        Updates for push-fed reasons are dropped while the push channel is
        down (conservative freeze); local reasons always apply."""
        return self.reasons_active_changed({reason: active}, now=now)

    def reasons_active_changed(self, updates, *, settled=False, now=None):
        """Apply one sensor snapshot atomically. Only already-debounced push
        snapshots use settled=True; local/X11 reasons retain engine debounce."""
        for reason in updates:
            if reason in _RESERVED_AUTO_STOP:
                raise ValueError(f"reserved auto-STOP reason not implemented: {reason}")
            if reason not in self._auto:
                raise ValueError(f"unknown automatic reason: {reason!r}")
        accepted = False
        for reason, active in updates.items():
            if reason in self._push_sources and not self._push_alive:
                continue
            self._auto[reason].active = bool(active)
            accepted = True
        if not accepted:
            return self.compute(), False
        return self._mutate(immediate=settled, now=now)

    def note_push_alive(self, alive):
        """Integration channel lost/restored. While down, updates for
        push-fed reasons are dropped (conservative freeze); local reasons
        keep working so X11 is never degraded. Returns the snapshot."""
        self._push_alive = bool(alive)
        return self.snapshot()

    def pop_due(self, now=None):
        """Collect a debounced auto output whose time has come. Returns
        None when nothing is due (idempotent: at most one delivery)."""
        if self._pending is None:
            return None
        if (now if now is not None else self._clock()) < self._pending[2]:
            return None
        _, output, _ = self._pending
        self._pending = None
        return output

    def pending_delay(self):
        """Seconds until the pending auto output is due, or None."""
        if self._pending is None:
            return None
        return max(0.0, self._pending[2] - self._clock())

    # -- read-only views ---------------------------------------------------
    def compute(self):
        """Current output for the present latch/reason state."""
        if self._manual_stop:
            desired = STOPPED
        elif self._manual_pause or self._any_auto_pause():
            desired = PAUSED
        else:
            desired = PLAY
        mute = self._manual_mute or self._any_auto_mute()
        active = tuple(sorted(name for name, r in self._auto.items() if r.active))
        return EngineOutput(desired, mute, active, self._generation)

    def snapshot(self):
        """JSON-safe view for GUI/diagnostics. Additive; never a control."""
        out = self.compute()
        return {
            "desired": out.desired,
            "wanted_mute": out.wanted_mute,
            "manual_stop": self._manual_stop,
            "manual_pause": self._manual_pause,
            "manual_mute": self._manual_mute,
            "active_reasons": list(out.active_reasons),
            "push_alive": self._push_alive,
            "generation": out.generation,
            "policy_version": POLICY_VERSION,
            "debounced": self._pending is not None,
        }

    # -- internals ---------------------------------------------------------
    def _any_auto_pause(self):
        return any(r.active and _AUTO_EFFECTS[name][0] for name, r in self._auto.items())

    def _any_auto_mute(self):
        return any(r.active and _AUTO_EFFECTS[name][1] for name, r in self._auto.items())

    def _mutate(self, *, immediate, now=None):
        self._generation += 1
        output = self.compute()
        if immediate:
            self._pending = None
            return output, True
        at = (now if now is not None else self._clock()) + self._debounce_s
        self._pending = (self._generation, output, at)
        return output, False
