# Apricus 2.1.0 release notes

Status: unpublished local candidate. The release date and publication require separate approval.

Apricus 2.1.0 closes the M1–M3 cycle: playback controls, a server-owned policy engine, and GNOME Wayland fullscreen/maximized sensing. It does not include suspend/resume (M4) behavior.

## Highlights

- **Now Playing (M1):** live dashboard with dynamic Play/Pause, separate Stop, volume/mute, media details and up-next preview. Manual actions persist and take precedence over automation.
- **Playback Policy Engine (M2):** one server-owned engine decides every transition. Pause/mute preferences for maximized windows apply to the current state immediately; playlist selection and position stay consistent with the policy.
- **GNOME Wayland sensing (M3):** the `apricus-integration@fabx-dev` extension (revision 6) reports aggregate fullscreen/maximized state over a versioned D-Bus channel. The server validates sender and sequence, reconciles on reconnect, and freezes safely on signal loss.
- **Immediate pause:** VLC is called without the old pre-pause fade; resume retains fade-in. Correct resume and immediate GUI pause were accepted on the tested VM, not certified on every device or source.
- **Streaming information:** a small native information button beside the URL field offers the notice via tooltip or keyboard-accessible popover, without a permanent disclaimer.
- **Strict Snap compatibility:** pipe-based player readiness and a narrow TCP Fast Open fallback, without additional sandbox permissions. Streaming keeps native HTTPS; AVIO is not restored.

## Upgrade notes

- Native `.deb`/`.rpm` installs include the extension under `/usr/share/gnome-shell/extensions/`. Enable once with `gnome-extensions enable apricus-integration@fabx-dev`, then log out/in.
- Snap/Flatpak cannot install host extensions from their sandbox: build the ZIP with `gnome-extension/build-zip.sh` and run `gnome-extension/install-host.sh --zip <file.zip> --enable` on the host, then log out/in.
- After updating the extension, the new code loads only after logout/login. Confirm revision 6 with `journalctl --user -g apricus-integration`.
- Back up playlists, preferences and persistent data before upgrading. The final 2.0.7-to-2.1.0 Store upgrade is not yet validated; local configuration tests are not a substitute for that test. Do not remove the application with data purging as an upgrade step.

## Package compatibility

- **DEB/RPM:** full M1–M3 when the extension is installed and enabled.
- **Flatpak:** M3 availability depends on verified window-state access at runtime.
- **Snap:** bridge-carried M3 from the rev6 extension to the server-owned bridge (no control-center dependence); direct signal subscription remains unavailable under strict confinement.

## Known limitations

- GNOME 49/51 ship per static API review but are not runtime-tested.
- Some YouTube videos may not play reliably due to streaming service restrictions or compatibility limitations. Intermittent audio/video stalls remain unresolved; the observed cause has not been established, and the information button is not a playback fix.
- Detection+decision latency ≤ 500 ms is not rigorously attested. Historical measurements included the now-removed pre-pause fade and do not certify the final sensor-to-visible-pause latency.
- Final candidate audible audio/mute, GNOME automation, package parity and Store-upgrade acceptance must be completed before publication. Earlier candidate results are not final-package certification.
- The Settings screenshot in `res/` still shows the previous subtitle; the 2.1.0 rows explain the extension requirement instead.
- A prior validation campaign regenerated its static PNG without a byte-identical backup; that historical reservation stands. The final campaign restored its own baseline byte-identically.

## Verifying the release

- `apricus --help` (the description), the startup log or the About/Settings row must report 2.1.0. There is no `--version` option.
- Native installs: extension revision 6 loaded, `DesktopStateChanged` received, policy `extension-push`, preferences editable.
- Strict Snap: without the rev6 extension, expect the integration rows disabled; bridge-carried M3 needs the rev6 extension and a Snap whose server owns the bridge.
