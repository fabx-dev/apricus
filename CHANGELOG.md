# Changelog

## 2.1.0 — unreleased

- Web Page: local candidate adds same-window interaction with Shell-owned Done and a temporary exit shortcut displayed on that control (prefer Super+Ctrl+Shift+F12, with conflict-checked F11/F10 alternatives); no browser or WebView replacement. Final installed strict-Snap acceptance is still pending.
- GNOME integration revision 8 avoids Mutter's reserved Super+Shift+Esc (cancel-input-capture) without changing system/user keybindings.
- GNOME integration revision 7: authenticated asynchronous interaction channel, generation-scoped rollback, minimized-window notifications and narrowly scoped focus protection. No new Snap permissions.

- M1: Now Playing panel with dynamic Play/Pause and separate Stop; persisted manual actions with precedence over automation.
- M2: Server-owned Playback Policy Engine with pause/mute preferences, playlist-aware behavior and immediate application to current state.
- M3: GNOME Shell extension revision 6 with `DesktopStateChanged` push channel, sender/sequence validation, reconnect reconciliation and safe freeze on loss.
- GUI pause/mute rows editable on capable GNOME Wayland with extension-requirement messaging; strict Snap uses the server-owned bridge for M3 push.
- Player D-Bus `data_source` type corrected to `a{ss}`; window-policy setters recompute current state without reloading player config.
- Now Playing Play/Pause button and action follow the effective playback state, not just the manual latch (auto-paused video offers Play, and Play resumes instead of silently adding a manual hold).
- Pause invokes VLC immediately, without the former pre-pause fade delay; resume retains fade-in. Immediate GUI pause and correct YouTube resume were accepted on the tested VM with x9/x8 respectively, not certified across all streams or hardware.
- Snap stream playback uses native VLC HTTP/TLS with a narrowly scoped TCP Fast Open compatibility fallback. Restart/reseek recovery remains available for non-pausable inputs; native pausable inputs retain their media across pause/resume.
- Streaming page: discreet information button with a shared tooltip/popover notice, keyboard navigation and native light/dark styling.
- Known limitation: some YouTube videos may stall or lose reliable audio/video playback. This remains unresolved; no universal streaming reliability or confirmed upstream root cause is claimed.
- Strict Snap: player readiness uses a pipe-based flag instead of `multiprocessing.Event`, because the sandbox denies `/dev/shm` (without this, setting any video failed with `PermissionError` and no player ever spawned).
- M3 on strict Snap via bridge-carried window push (extension rev6 → Snap-owned bridge → policy engine); direct signal subscription remains unavailable under strict confinement by design.
- Localizations updated for the four capability subtitles across all supported catalogs.
- See `RELEASE_NOTES_2.1.0.md` for upgrade notes and known limitations.

## 2.0.7 — 2026-10-19

- Sidebar status footer, DING stacking guard, and dock icon alignment fixes.
