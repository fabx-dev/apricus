# M3 local closure and reduced validation

Historical implementation/measurement plan. The reduced campaign was subsequently
completed: see [final validation status](m3-validation-status.md) for the live
results, NOT MEASURED latency verdict and exact restoration reservation.

Local-only follow-up to the third campaign (`6fc5f3f`), GNOME 50.1 / native
Wayland. No installed extension, session settings, manifest or permissions
are changed by this work. No live latency PASS is claimed.

## Corrections

- GJS API verified on the installed GI binding: `GLib.timeout_add(priority,
  interval_ms, callback)` and `GLib.source_remove(tag)`. Convert 0.25 seconds
  to 250 ms, reschedule on every edge (true trailing debounce), keep summary
  deduplication and cancel on stop. Synchronize window subscriptions before
  aggregate deduplication: an ordinary new window still needs notify handlers.
- Settled push snapshots update window reasons atomically, without adding the
  engine's 250 ms delay. Source startup/reconnect buffers GetState until the
  subscription succeeds and the source is marked alive. Local/X11 reasons
  keep their existing debounce. A fullscreen→maximized reason handoff must
  never briefly resume playback.
- Existing GTK4 switches (no duplicate controls) are editable on native
  GNOME Wayland even during temporary extension loss; their subtitle states
  the extension requirement. X11 behavior is unchanged. Strict Snap Wayland
  remains disabled: its inbound M3 channel does not exist. Flatpak is disabled
  until the running server reports healthy `extension-push`; positive evidence
  is retained for the panel lifetime across temporary disconnects, never
  inferred from a manifest. A new panel must verify again. No sandbox access
  is added or presumed. Other Wayland desktops remain unsupported.
- GTK `state-set` uses the requested `state` argument, not the previous active
  value. Existing pause-over-mute precedence is retained. Server setters now
  recompute the most recently received window state, including clearing old
  reasons when policy changes. Source loss does not erase preferences or
  replay stale cached state as a new sample.
  These server-owned preferences no longer reload player configuration:
  a reload would replace the effective automatic-mute mirror with the persisted
  manual mute, and the player no longer consumes window-policy preferences.
- VideoPlayer GetAll failed because `data_source` returned a per-monitor dict
  while XML declared `s`. Correct that one property to `a{ss}`. The actual
  getter/setter already use maps; no media or VLC behavior changes. This is
  a diagnostic/export defect, not evidence of playback failure. This corrects
  the D-Bus property type; clients introspecting that property see a map.
  Base/Web player contracts are not changed.

## Non-invasive regressions

Run pytest and all three existing GJS harnesses. The state harness uses a real
GLib main loop without owning a bus name: edges at 0/80/160 ms yield one
publication about 250 ms after the final edge; a pending callback is cancelled
on stop, with an additional observation period. Timing assertions run outside
GLib callbacks so a logged callback exception cannot masquerade as success.
The 240–500 ms bound is a timer regression tolerance, not a live latency SLA.
The fake notify dispatcher now preserves `notify::` names rather than splitting
them at the second colon. Horizontal-only and vertical-only both stay false;
only full maximization activates the aggregate.

`tests/dbus/m3-local.py` runs only under `dbus-run-session` with displays unset.
It exercises actual pydbus subscribe/delivery, authentic-owner duplicates and
obsolete sequences, the real GTK4 callback in a separate interpreter, actual
server property setters and engine output, and GetAll with production player
XML/getters. Player actuation and disk config are test doubles: it does not
instantiate VLC or a desktop GUI. Repeated reconnect tests check one active
subscription and conservative loss behavior. GUI tests cover native, X11,
Snap, verified/unverified Flatpak and transient loss without erasing values.

Before/after checks use separate copies, never stash or alter the host:
old seconds timer, old non-trailing scheduling, old GUI callback, old server
setters and old player export must each fail their corresponding regression.

Consolidated final result: **537 pytest passed** (30 dependency deprecation
warnings), all three GJS harnesses passed, Ruff lint/format and diff-check
passed, all five PO catalogs passed `msgfmt --check`; gettext extraction also
passed. The real GLib timer measured **250.5 ms** after the last edge in the
final run. All five old-code counterchecks exited nonzero as expected. The
full suite used a private session bus; service-activation/portal warnings are
retained in its log and are not treated as GNOME live validation.

Evidence: `<private-path>`
(`final/` is authoritative; the first pass preceded the additional player-mute
reload regression). Host extension file inventories before/after are identical.

## Reliable latency method and limits

Use a single monotonic clock domain (`GLib.get_monotonic_time()` in Shell,
Python `time.monotonic_ns()` normalized to microseconds on the same host).
Record clock provenance; do not subtract realtime dbus-monitor timestamps
from monotonic timestamps. Do not put window identities in trace records.

For a short, separately authorized traced run, collect these points:

1. **T0**: entry into the Shell notify callback for the changed fullscreen
   state. This measures notification receipt, not the exact compositor commit
   or keyboard input. The latter are not directly exposed by this channel.
2. **T1**: immediately before `emit_signal` for the matching sequence. T1−T0
   includes trailing debounce and Shell scheduling. In a burst use the last
   edge contributing to that sequence, and report burst duration separately.
3. **T2**: backend callback entry for that owner/sequence, before validation.
4. **T3**: settled engine output and actuation dispatch after the atomic update.
   T3−T1 includes bus delivery, validation and decision; T2−T1 isolates delivery.
5. **T4**: player command receipt. **T5**: VLC Paused event / confirmed paused
   renderer state (all active renderers if applicable). T5−T3 measures actual
   actuation, not a server desired state or the user-pause mirror.

Correlate by owner epoch + sequence and by actuation generation. These exact
trace points are **not all present in current production logging**; a reduced
timing run needs separately approved diagnostic probes. External observers
alone cannot measure T0 or exact T5. A monotonic 50 ms poll gives intervals
(and overhead), not exact timestamps; old samples do not establish latency.

The configured 1.5 s fade intentionally delays effective pause. Report T3→T5
with fade enabled, including the fade contribution. A separately authorized
zero-fade run can isolate transport/control latency, then restore the setting.
Never subtract 1.5 s and call the remainder an exact measured latency. Compare
the full T0→T5 against 500 ms explicitly: it may exceed it by design with fade.
Also report T0→T3 separately. Do not silently redefine the target.

## Historical reduced final live proposal (subsequently executed)

Reuse the verified extension backup/rollback, but take a **complete** fresh
config/cache/static-image inventory before touching anything. Keep the prior
PNG reservation explicit: the third campaign regenerated its PNG without a
byte-identical backup, so that restoration was not integrally byte-identical.

1. Short load gate: candidate file hashes, ACTIVE/revision, DING, owner,
   Ping/GetState; one fullscreen signal reaches the backend.
2. On native GNOME Wayland, change the existing switches in the GUI while
   maximized: pause→mute→neither must immediately change policy/player state.
   No manual D-Bus preference override. Restore original choices.
3. One traced fullscreen enter/exit with the approved probes above. Repeat
   only with zero fade if needed and explicitly authorized. Check no late
   callback/duplicate publication on one disable/re-enable cycle.
4. Quick desktop sanity and mandatory restoration + user login verification.

Do not rerun the full PAUSE/STOP/mute/reconnect/workspace campaign already
passed on the previous candidate. Separate-axis logic and stale/duplicate
frames are now covered automatically. Flatpak/Snap parity is NOT validated
by this native run and is not a reason to install either package here.
