# M2 explicit Play persistence correction

## Evidence and cause

The second live run at `7b087fe` recorded `playback_state=playing`,
`engine_snapshot.manual_pause=false`, and disk `policy_manual_pause=true` in
the private run's `resume-observation.json` (not distributed with this
repository). The preserved evidence checksums were verified without changing
any evidence files.

The complete GUI path is:

1. `gtk4app.py:on_play_pause` writes `server.is_paused_by_user = not prev`,
   then calls `pause_playback()` or `start_playback()`. From STOPPED it uses
   the separate M1 `resume_playback()` verb.
2. Real D-Bus forwards the property Set/method call through `_DBusServer`
   to the existing server; Snap IPC dispatches Set/call through
   `_invoke_on_mainloop`. Neither transport changes latch values.
3. The server's property setter and `start_playback()` call
   `PlaybackPolicyEngine.request_manual_resume()`, setting the engine latch
   to false immediately. The engine does not write configuration itself.
4. Before this fix, both called `_sync_policy_persist()` without explicit
   pause intent. Its default `pause_to="keep"` copied the false latch into
   `self.config`, but passed `manual_pause=None` to `_save_config()` and
   `ConfigUtil.save()`.
5. Under its file lock, `ConfigUtil._save_locked()` deliberately preserves
   disk STOP/PAUSE when no explicit intent is supplied. It therefore replaced
   `self.config["policy_manual_pause"]` with the old true disk value. Engine:
   false; in-memory config after save: true; disk after save: true. No later
   writer or race is needed to cause the defect.
6. The same ownership rule means a standalone property write to pause could
   also lose its true intent against an old false disk value. The GUI's
   subsequent explicit `pause_playback()` previously hid this half of the bug.

## Minimal correction

Only two server persistence calls change:

- `start_playback()` explicitly supplies `pause_to=False`.
- The `is_paused_by_user` setter explicitly supplies its requested bool.

The config writer is unchanged: it locks, merges owned latch intent, serializes
to a temporary JSON file, flushes/fsyncs and atomically replaces the main file.
Ordinary GUI, playlist and server saves still preserve disk STOP/PAUSE rather
than reintroducing values from stale selections. The tests exercise later
stale saves in both directions and a later ordinary server save after Play.

STOP/RESUME already supplies explicit STOP intent and the current pause value;
its behavior is unchanged. Mute continues to use the compatibility `is_mute`
key via `_sync_policy_persist`; setting and clearing it, preserving it through
pause/play and STOP/RESUME, and restoring it on simulated restart are tested.
No new multi-writer guarantees for the legacy mute key are introduced here.
Fade, player code, GUI, policy priorities and both transport contracts remain
unchanged.

## Automated proof

`tests/test_policy_persistence.py` uses temporary config/legacy paths and real
server `_load_config`/`_save_config` plus real ConfigUtil disk operations.
It reconstructs a fresh server and engine from loaded configuration without
running server initialization, sensors, GUI, players or a host-session bus.

- Before the fix: six regression cases failed (method, property, GUI command
  sequence, each with/without an active automatic pause); nine initial latch
  and STOP/mute cases passed. Method/GUI Play left disk true despite engine
  false; the standalone property also failed to persist initial PAUSE.
- After the fix: all 17 cases pass, including two additional mute-preservation
  cases. Restart after explicit Play does not restore manual PAUSE. An active
  automatic pause still prevents playback in the current engine after Play;
  automatic sensor state is not persisted by these simulated restart tests.
- Targeted persistence/policy/D-Bus/STOP/config/playlist tests: 107 passed.
- Complete Python suite: 445 passed, 5 skipped, 28 warnings.
- Ruff lint and format: passed (72 files).
- Headless GJS status-bridge and extension-rescan: passed.

No new live test was run. End-to-end desktop restart behavior remains for a
separately authorized live validation; these tests prove the persistence path,
not compositor/player startup behavior.
