# M3 Snap strict end-to-end validation protocol (rev6 extension, server-owned bridge)

Status: PROTOCOL ONLY — not executed. Requires a separate GNOME Wayland
session where logout/login, extension replacement and Snap install are
explicitly authorized. Do NOT run any of this in the daily session.

## Prerequisites (authorized separately)

1. A test machine/VM with Ubuntu 26.04.1, GNOME Shell 50.1, Wayland session.
2. Snap 2.1.0 artifact from the 2.1.0 GitHub Release (or CI artifact).
3. Extension ZIP built from the 2.1.0 sources (`gnome-extension/build-zip.sh`),
   containing `windowPush.js`, `CODE_REVISION = 6`, `metadata.json` v2.
4. Baseline: Snap stable 2.0.7 installed, extension rev4 ACTIVE, DING ACTIVE,
   full backup (config/cache/static PNG/URIs/extension inventory) with a
   verified restore script.

## Procedure

1. Install Snap 2.1.0 (`snap install --dangerous <file>` or from the
   candidate channel once published there): expect install success and no
   AppArmor denials for the declared `gnome-status` name.
2. Replace ONLY `extension.js` + add `state.js` + `windowPush.js` (hashes
   against the 2.1.0 tree), keep `metadata.json` handling per installer;
   manual logout/login. Expect rev6 loaded, Apricus + DING ACTIVE, bridge
   name owned by the Snap on the session bus.
3. Start the Snap control center from the release: expect preferences
   editable (no “only X11”/Snap lockout) once the first window frame lands.
4. Play a local video (Hawaii or equivalent), keep it animating.
5. Fullscreen a window (F11): expect automatic pause with reason
   `fullscreen`, player `playback_state=paused`, generation advancing.
6. Exit fullscreen: expect resume when allowed; then maximize/unmaximize:
   expect pause/resume with reason `maximized`.
7. Manual PAUSE, STOP, explicit Play during fullscreen, mute independence:
   expect manual latches to win over automatic reasons in every case.
8. Close the control center completely: expect the last window state to
   keep working (server-owned bridge, no GUI dependence); reopen and
   expect the integration row to re-sync with adopted counts.
9. Disable/re-enable the extension (or restart the server process):
   expect sequence rebaseline, no stale frames, no auto-resume on loss.
10. Journal check: no `JS ERROR`, no AppArmor denials for new names.
11. Desktop sanity: DING/Home open+drag, Overview clean, no orphan
    wallpaper windows, no duplicate player processes.
12. Upgrade check: pre-seed a 2.0.7 config + playlist, install 2.1.0 over
    2.0.7, confirm settings/playlist survive and the new preferences appear.

## Pass criteria

- All 12 steps PASS with observed (not inferred) state at each step.
- Latency is reported as observed brackets only (signal→snapshot poll at
  100 ms); the ≤500 ms target stays NOT MEASURED until traced probes exist.
- Any AppArmor denial, freeze, or lost latch = FAIL with logs attached.

## Rollback (mandatory whatever the outcome)

Restore the rev4 extension files, remove `state.js`/`windowPush.js` if
absent in baseline, reinstall/restore Snap 2.0.7 stable, restore
config/cache/PNG/URIs, manual logout/login, then verify rev4 loaded,
DING ACTIVE, icons working, no orphan windows, no test processes left.
