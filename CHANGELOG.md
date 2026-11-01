# Changelog

## 2.1.0 — 2026-10-26

- M1: Now Playing panel with dynamic Play/Pause and separate Stop; persisted manual actions with precedence over automation.
- M2: Server-owned Playback Policy Engine with pause/mute preferences, playlist-aware behavior and immediate application to current state.
- M3: GNOME Shell extension revision 5 with `DesktopStateChanged` push channel, sender/sequence validation, reconnect reconciliation and safe freeze on loss.
- GUI pause/mute rows editable on capable GNOME Wayland with extension-requirement messaging; strict Snap Wayland remains without M3 push.
- Player D-Bus `data_source` type corrected to `a{ss}`; window-policy setters recompute current state without reloading player config.
- Now Playing Play/Pause button and action follow the effective playback state, not just the manual latch (auto-paused video offers Play, and Play resumes instead of silently adding a manual hold).
- Pause is now immediate: the pre-pause volume fade-out no longer delays VLC `pause()`, so video and audio stop at once on manual and automatic (fullscreen/maximized) pauses. Resume still fades in and restarts from the paused position without recreating media.
- Snap stream playback uses native VLC HTTP/TLS with a narrowly scoped TCP Fast Open compatibility fallback, restoring genuine PAUSE without media recreation or fresh YouTube resolution. The x7 reconnect/reseek candidate failed continuity and is superseded; installed-app acceptance of the new candidate remains pending (see `docs/notes/snap-stream-resume.md`).
- Strict Snap: player readiness uses a pipe-based flag instead of `multiprocessing.Event`, because the sandbox denies `/dev/shm` (without this, setting any video failed with `PermissionError` and no player ever spawned).
- M3 on strict Snap via bridge-carried window push (extension rev6 → Snap-owned bridge → policy engine); direct signal subscription remains unavailable under strict confinement by design.
- Localizations updated for the four capability subtitles across all supported catalogs.
- See `RELEASE_NOTES_2.1.0.md` for upgrade notes and known limitations.

## 2.0.7 — 2026-10-19

- Sidebar status footer, DING stacking guard, and dock icon alignment fixes.
