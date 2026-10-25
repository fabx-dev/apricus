# M2: restore policy on a newly activated player

## Verified startup failure

At `36353c6`, startup loads config, constructs `PlaybackPolicyEngine`, restores
manual latches, and calls `reload()` / `_setup_player()`. The latter called
`_apply_policy_output(..., fresh=True)` immediately after `Process.start()`.
That is process creation, not GTK activation or IPC readiness.

The old actuator cached `(desired, wanted_mute)` **before** obtaining a player
proxy or executing any command. An absent proxy therefore left PAUSED marked
as delivered. There was no subsequent startup reconciliation. Replacement
players also inherited the previous player's dedup pair. `fresh=True` itself
does not suppress PAUSE, but the earlier identical-output check did.

The captured `<private-server-log>` confirms this order: server start/spawn at lines
69–74, player startup at 75, source installation at 106. The restored config
contains PAUSE=true. There is no delivered player pause in that startup log.
The player previously initialized its local pause mirror to false, and its
source setup called `start_playback()`: that allowed the observed VLC autoplay.
The persisted file and engine were correct throughout.

The new delayed-activation regression reproduces the same failure without a
desktop: both restart-with-pause and fresh-wallpaper replacement received zero
pause requests before the fix. The original three cases gave 2 FAIL / 1 PASS.

## Targeted correction

- Each new process gets an internal multiprocessing readiness Event and the
  server-computed initial paused command. These are process arguments, **not**
  new D-Bus methods/properties or persisted configuration.
- Before activation, the video entrypoint sets its existing local pause veto
  from that command. It does not read/compute policy. Thus source autoplay
  cannot bypass a server-commanded PAUSE. Existing fade methods are unchanged.
- Video signals readiness after window/source activation. Web uses the same
  lifecycle and applies its existing initial freeze before signaling readiness.
- The server resets dedup for the new process. A 50 ms readiness check waits
  for the Event, using the current engine decision when ready. It does not
  sleep or infer readiness from elapsed time. It stops after successful
  actuation, process death/replacement/STOP, or a 30-second budget with a warning.
  An unavailable/failed IPC actuation is not falsely cached as successful.
- The initial playing path does not reissue Play/fades when the player already
  auto-started. If Play was requested while bootstrap was holding PAUSE, it
  explicitly starts once activation is ready. A successful pause is retained
  if just the mute step fails, so retrying mute does not repeat the pause fade.
- In-place VLC-family switches retain the existing local pause veto. A fresh
  process after a source/mode change follows the new readiness path and cannot
  inherit another process's delivery cache.

STOP still returns before player creation. STOP/RESUME commands, persisted
latches, playlist code, fade implementation, GUI and D-Bus schema are unchanged.
Mute is a separate actuator step and is covered during startup and retry.

## Automated validation and limits

`tests/test_policy_startup.py` covers 14 cases: real server initialization with
temporary ConfigUtil storage; previous player destruction; delayed activation;
real VideoPlayer pause/start and PlayerWindow fade completion against a VLC
stub; one delivery per new player; fresh wallpaper replacement; initial PLAY;
STOP without spawn; explicit Play after restart/during bootstrap; timeout and
stale callback cancellation; failed IPC and partial mute failure; activation
ordering and video/web entrypoint bootstrap. Tests control time/completion;
they do not sleep or launch GTK windows/VLC processes.

The existing wiring tests now explicitly distinguish a replacement actuator
from its predecessor and signal readiness before checking its commands.
This corrects their old assumption that an already-available fake proxy
represented the just-created player.

Focused startup/policy/persistence/STOP/playlist/D-Bus tests: 90 passed.
Complete suite: 459 passed, 5 skipped, 30 warnings. Ruff lint/format and the
headless GJS status-bridge and extension-rescan harnesses pass.

No new live test was run. Native VLC/compositor convergence still requires a
separately authorized live check. Startup/IPC that never becomes ready within
the finite budget is warned about, not silently reported as applied or retried
forever. Tests do not claim real renderer or stalled-IPC timing guarantees.

## Home/DING: existing evidence only

The captured desktop observation reports Home absent during wallpaper playback,
`show-home=true`, and a live DING process. The post-recovery report records the
user's confirmation that Home was visible again. Logs say the Shell integration
is active and owns stacking; the player's legacy stacking guard is consequently
not used. That report is not independent proof of actual compositor ordering.

Occlusion/stacking is compatible with the symptom, but there is no captured
actor tree, window-order snapshot or screen image establishing it. Nor is there
evidence proving an icon refresh failure or that the issue is independent of
M2. The user did not establish initial icon visibility. The logged Wayland
Introspect AccessDenied concerns automatic window-state sensing, not proof of
a DING stacking cause. **Cause: NON DETERMINATA.**

No host GNOME/DING settings or extension code were changed or queried afresh
for this investigation; only existing evidence was examined. All 34 recorded
live-evidence checksums match.
