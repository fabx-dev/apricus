# Apricus 2.1.0 release notes

Release date: **2026-11-04** (confirmed by the maintainer). Status: locally prepared, not published. External publication requires separate approval.

Apricus 2.1.0 closes the M1–M3 cycle: playback controls, a server-owned policy engine, and GNOME Wayland fullscreen/maximized sensing. It does not include suspend/resume (M4) behavior.

## Highlights

- **Now Playing (M1):** live dashboard with dynamic Play/Pause, separate Stop, volume/mute, media details and up-next preview. Manual actions persist and take precedence over automation.
- **Playback Policy Engine (M2):** one server-owned engine decides every transition. Pause/mute preferences for maximized windows apply to the current state immediately; playlist selection and position stay consistent with the policy.
- **GNOME Wayland sensing (M3):** the `apricus-integration@fabx-dev` extension (revision 8) reports aggregate fullscreen/maximized state over a versioned D-Bus channel. The server validates sender and sequence, reconciles on reconnect, and freezes safely on signal loss.
- **Direct webpage interaction:** native/Snap GNOME Wayland installations can promote the same wallpaper window and WebView without reload. Use **Interact with webpage**, then **Done — Return to desktop** or the shortcut displayed on Done (prefer Super+Ctrl+Shift+F12, conflict-checked F11/F10 alternatives). Esc remains available to HTML dialogs. Returning focus to another application intentionally ends interaction. Closing only the GUI leaves Done operational. A running Web Page offers Stop/Resume, not manual GUI Pause.
- **Immediate pause:** VLC is called without the old pre-pause fade; resume retains fade-in. Correct resume and immediate GUI pause were accepted on the tested VM, not certified on every device or source.
- **Streaming information:** a small native information button beside the URL field offers the notice via tooltip or keyboard-accessible popover, without a permanent disclaimer.
- **Strict Snap compatibility:** pipe-based player readiness and a narrow TCP Fast Open fallback, without additional sandbox permissions. Streaming keeps native HTTPS; AVIO is not restored.

## Upgrade notes

- Native `.deb`/`.rpm` installs include the extension under `/usr/share/gnome-shell/extensions/`. Enable once with `gnome-extensions enable apricus-integration@fabx-dev`, then log out/in.
- Snap/Flatpak cannot install host extensions from their sandbox: build the ZIP with `gnome-extension/build-zip.sh` and run `gnome-extension/install-host.sh --zip <file.zip> --enable` on the host, then log out/in.
- After updating the extension, the new code loads only after logout/login. Confirm revision 8 with `journalctl --user -g apricus-integration`. Updating only application code does not require another logout when rev8 is already loaded.
- Back up playlists, preferences and persistent data before upgrading. The final 2.0.7-to-2.1.0 Store upgrade is not yet validated; local configuration tests are not a substitute for that test. Do not remove the application with data purging as an upgrade step.

## Package compatibility

- **DEB/RPM:** expose M1–M3 and direct webpage interaction with compatible GNOME Wayland/rev8 integration. Final native package startup is checked separately; installed visible interaction/A/V acceptance was performed with strict Snap on GNOME/Mutter 50.1 Wayland in the tested VM, not on every distribution.
- **Flatpak:** base playback/UI and webpage display are provided. M3 availability depends on positively verified window-state access at runtime. **Interact with webpage is unavailable in this 2.1.0 package.** The filtered bus does not grant the bridge name/required Shell identity visibility, and rev8's host-PID window binding is not compatible with Flatpak PID namespaces without a protocol/identity change. No generic bus permission or confinement bypass was added.
- **Snap:** bridge-carried M3 from rev8 to the server-owned bridge (no control-center dependence); direct signal subscription remains unavailable under strict confinement. Direct web interaction is supported with rev8.
- yt-dlp versions are intentionally preserved, not silently aligned: Snap 2026.10.26; DEB/RPM/Flatpak 2026.07.04. EJS is 0.8.0 and Deno 2.9.7. No universal cross-package streaming parity is asserted.

## Known limitations

- GNOME 49/51 ship per static API review but are not runtime-tested.
- Some YouTube videos may not play reliably due to streaming service restrictions or compatibility limitations. Intermittent audio/video stalls remain unresolved; the observed cause has not been established, and the information button is not a playback fix.
- Detection+decision latency ≤ 500 ms is not rigorously attested. Historical measurements included the now-removed pre-pause fade and do not certify the final sensor-to-visible-pause latency.
- Visible local/YouTube sample audio, mute, pause/resume, GUI-closed automation and direct web interaction passed the bounded x14 acceptance; x15 also passed its status-message check. This is not hardware-wide, Fedora-visual, frame-accurate or universal YouTube certification. Final packaging and local-upgrade results are recorded in the final readiness report; Store-delivered upgrade is a separate approval-gated check.
- The Settings screenshot in `res/` still shows the previous subtitle; the 2.1.0 rows explain the extension requirement instead.
- A prior validation campaign regenerated its static PNG without a byte-identical backup; that historical reservation stands. The final campaign restored its own baseline byte-identically.

## Verifying the release

- `apricus --help` (the description), the startup log or the About/Settings row must report 2.1.0. There is no `--version` option.
- Native installs: extension revision 8 loaded, `DesktopStateChanged` received, policy `extension-push`, preferences editable.
- Strict Snap: without rev8, direct interaction is unavailable. Bridge-carried M3 requires a compatible extension and a Snap whose server owns the bridge; prefer the matching rev8 shipped with 2.1.0.
- The maintainer confirmed 2026-11-04 as the release date. AppStream metadata is stable and all four formats are prepared from the same final source baseline; publication itself remains separately approval-gated.
