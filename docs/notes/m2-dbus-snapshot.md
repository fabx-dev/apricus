# M2 snapshot marshalling correction

The interrupted source live run at `bb048c2` exposed a transport defect:
`engine_snapshot` is declared `a{sv}`, but returned a plain Python dictionary.
pydbus `ObjectWrapper.Get` calls `GLib.Variant(type, result)`; the `v` in
each dictionary entry requires an existing `GLib.Variant`. The first entry,
`desired` (a Python string), therefore raised `Expected GLib.Variant, but got str`.
`GetAll` has the same requirement. JSON round trips and XML syntax checks do not
exercise this conversion.

The D-Bus publication now uses a transport view, forwarding methods and writable
properties to the existing server. Only its `engine_snapshot` getter wraps
values with explicit signatures: `s` for desired state, `as` for reasons
(including an empty array), `b` for flags, `t` for generation, and `u` for policy
version. The server/pure-engine snapshot remains JSON-safe and unchanged;
Snap IPC still publishes the original server. The external `a{sv}` signature
and logical values are unchanged. Future snapshot keys must add a signature.

Comparison with the M1 baseline shows `engine_snapshot` is the only M2-added
D-Bus property. Existing scalar getters retain their declared string/bool/
integer types; none needs nested variant conversion.

`tests/test_policy_dbus.py` publishes only the diagnostic property on a private
`dbus-run-session` bus and reads it through real pydbus `Properties.Get` and
`GetAll`. The server is allocated without initialization: no player, GUI,
sensors, user configuration or GNOME session access. It checks the production
XML signature and default, paused/automatic/muted, and stopped snapshots.
With the transport getter still returning the plain dictionary, this test
reproduced the exact live TypeError; with typed values it passes.

Automated verification: focused D-Bus/policy/wiring/server tests 60 passed;
complete Python suite 428 passed, 5 skipped, 28 warnings; Ruff lint and format
passed (71 files); headless GJS status-bridge and extension-rescan passed.

## Pause observation protocol for the next authorized live run

Do not infer a pause failure from the previous one-second sample. Video pause
calls `pause_fade`; its completion callback invokes VLC pause. Until that
callback, `playback_state` can legitimately remain `playing` because it queries
VLC rather than only the manual latch. The observed configuration had a
1.5-second fade.

For the next run, record the configured duration and interval, monotonic
command time, player identity and immediate engine/latch state. Take the first
post-fade actual-state sample after `fade_duration_sec + max(1.0,
2 * fade_interval)` seconds (2.5 seconds for the observed configuration).
Then repeat actual-state and player-identity observations over another two
seconds, together with visual confirmation. Report delayed/unchanged state
with timings rather than assuming convergence. Do not alter fade or pause
logic based on the old sample. No new live run is part of this correction.
