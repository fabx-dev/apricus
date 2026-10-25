# M2 consolidated playlist corrections

Baseline: `041d1909e1a62b149ad1d2e684db898e6d3455ff`.
Live evidence: `<private-evidence-dir>`.
All 72 recorded checksums were verified; no live evidence was changed.

## Verified causes

1. **Play after paused playlist restart:** `_apply_source` initializes the
   desired queue and restored index, but `_play_queue_item` returned on the
   persisted pause latch *before* installing the media. The new VLC player
   therefore had no media. Explicit Play correctly cleared PAUSE but attempted
   playback on an empty player. Source preparation and policy were conflated.
2. **Select playlist while paused:** the same early return prevented replacing
   the previous video. Here the player was not empty: it retained Hawaii, so
   Play resumed Hawaii instead of the desired playlist. This shares the loading
   defect with case 1, but has a distinct observed wrong-media outcome.
3. **Position loss:** the player writes a newer index while the server retains
   an older full config snapshot. PAUSE saves that snapshot. The file lock and
   atomic replacement serialized writes but preserved only STOP/PAUSE latches,
   not playlist positions. Consequently a valid new position was replaced by
   `{}` (or another old map). This is independent of media loading.

## Selection, media, position and policy are separate

- Desired queues remain in the existing `playlist` config map. A source switch
  prepares the current item with `stop`/`set_media`/crop, including while paused.
  A monitor without a queue is cleared rather than retaining old video.
- The existing index restoration/clamping selects the item; no transport command
  resets all queues or indices. New queues still use existing numeric-position
  restoration/clamping, and shuffle retains its existing restart semantics.
- Initial source setup does not directly play each item. Once all monitor media
  is installed, the existing `start_playback` fade path honors the server's local
  pause mirror. The policy engine alone determines that mirror. There is no new
  policy evaluation or persisted-pause decision in the player.
- Natural EndReached advancement checks that the current mode is playlist,
  that no source switch is pending, and that the local playback veto permits it.
  Late ends while paused cannot change the queue index or start another item.
- Deferred source changes are generation-scoped: obsolete callbacks are dropped.
  Play arriving before source setup does not resume the previous media; setup
  applies the latest local veto after preparing the latest selection.

No IPC method/property, policy priority, fade implementation, GUI control,
readiness protocol, bridge or GNOME extension changes are involved. Compatible
STOP/RESUME still avoids spawning under STOP and starts one player on resume.

## Position ownership and atomic update

Ordinary `ConfigUtil.save` preserves the on-disk position map under the existing
lock, including when its caller has stale positions. Invalid persisted entries
are sanitized by the same nonnegative-integer rule as config loading.

`save_playlist_position(key, index, expected_queue, model=...)` is the explicit
playlist operation. Under that same lock it reads the latest complete file,
checks that playlist mode/selection are still current and STOP is not set, and
updates **only** that monitor's index via the existing atomic writer. It does
not write the player's stale preferences or overwrite other monitor positions.
It resolves stable key / legacy model / Default just like the player's queue
lookup. Superseded selections and invalid indices are rejected without changing
the config file. `_save_queue_index` uses this operation instead of load/modify/
save of a whole stale snapshot.

PAUSE, STOP, MUTE, source-independent GUI saves and stale later saves do not
change positions. Preparing an item or advancing a playlist remains an explicit
position operation; a changed queue may legitimately require index clamping.

## Automated regression proof

Before changing production code, the three regressions in
`tests/test_playlist_policy.py` failed respectively with: desired PLAY but
actual paused on an empty new player; previous Hawaii still loaded; newer disk
index overwritten by PAUSE. After correction all three pass.

The new file contains 23 cases using real server startup/transport methods,
real VideoPlayer source/queue/fade methods, and actual temporary ConfigUtil
files. Only windows/VLC, process creation, environment sensors and scheduling
are simulated. Coverage includes both video and web, paused/stopped selection,
paused/stopped restart, no duplicate process/fade starts, last-selection wins,
early Play ordering, local automatic veto/manual priority, multiple monitors,
stale writes, rejected old queue updates, and concurrent monitor position writes.

Existing direct-source and position-writer unit fixtures were updated for the
new contract: loading a paused source is allowed; autoplay is not. The real
disk regressions, not the mocks, verify preservation and atomic position writes.

Results: 209 focused tests passed; complete suite 482 passed, 5 skipped,
30 warnings. Ruff lint and formatting passed (74 files); both headless GJS
status-bridge and extension-rescan harnesses passed. No live run occurred.

## One minimal concluding live validation (not executed)

With one verified backup and one final restore:

1. Use a two-item short playlist. Reach item 2, pause and verify actual stopped
   motion plus engine/file PAUSE and position 1. Restart the test server while
   paused: no autoplay. Play must resume item 2; engine/file clear PAUSE and
   preserve position. Repeat STOP/restart/resume only as this case's prerequisite.
2. Pause Hawaii; select that playlist. Verify no autoplay, then Play must show
   the selected item, not Hawaii. Check the saved index and real VLC state.
3. Where multiple monitors exist, check independent queues/positions once.
   Check GUI, Home and Overview briefly; restore and compare hashes/manifests.

Previously accepted single-video, D-Bus and mute scenarios need not all be
repeated. Real automatic fullscreen/maximized testing remains NOT TESTED on
the current Wayland environment unless a supported backend is available; do
not force a backend or treat headless policy tests as live evidence.

Residual limits: native VLC/WebKit/compositor timing still needs that final
live run. This change does not implement per-frame resume positions, define new
shuffle ordering semantics, change legacy full-config ownership for unrelated
preferences, or prove that the historical Home anomaly is resolved.
