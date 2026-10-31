# Apricus 2.1.0 release notes

Apricus 2.1.0 closes the M1–M3 cycle: playback controls, a server-owned policy engine, and GNOME Wayland fullscreen/maximized sensing. It does not include suspend/resume (M4) behavior.

## Highlights

- **Now Playing (M1):** live dashboard with dynamic Play/Pause, separate Stop, volume/mute, media details and up-next preview. Manual actions persist and take precedence over automation.
- **Playback Policy Engine (M2):** one server-owned engine decides every transition. Pause/mute preferences for maximized windows apply to the current state immediately; playlist selection and position stay consistent with the policy.
- **GNOME Wayland sensing (M3):** the `apricus-integration@fabx-dev` extension (revision 5) reports aggregate fullscreen/maximized state over a versioned D-Bus channel. The server validates sender and sequence, reconciles on reconnect, and freezes safely on signal loss.

## Upgrade notes

- Native `.deb`/`.rpm` installs include the extension under `/usr/share/gnome-shell/extensions/`. Enable once with `gnome-extensions enable apricus-integration@fabx-dev`, then log out/in.
- Snap/Flatpak cannot install host extensions from their sandbox: build the ZIP with `gnome-extension/build-zip.sh` and run `gnome-extension/install-host.sh --zip <file.zip> --enable` on the host, then log out/in.
- After updating the extension, the new code loads only after logout/login. Confirm revision 5 with `journalctl --user -g apricus-integration`.

## Package compatibility

- **DEB/RPM:** full M1–M3 when the extension is installed and enabled.
- **Flatpak:** M3 availability depends on verified window-state access at runtime.
- **Snap:** bridge-carried M3 while the control center runs (rev6 extension required); direct signal subscription remains unavailable under strict confinement.

## Known limitations

- GNOME 49/51 ship per static API review but are not runtime-tested.
- Detection+decision latency ≤ 500 ms is not rigorously attested. An external observer measured the first policy-paused read ~93 ms after signal receipt; the first player-paused read came ~1.6 s later with the default 1.5 s fade. These are observation times, not exact internal timestamps.
- The Settings screenshot in `res/` still shows the previous subtitle; the 2.1.0 rows explain the extension requirement instead.
- A prior validation campaign regenerated its static PNG without a byte-identical backup; that historical reservation stands. The final campaign restored its own baseline byte-identically.

## Verifying the release

- `apricus --version` (or the About/Settings row) must report 2.1.0.
- Native installs: extension revision 5 loaded, `DesktopStateChanged` received, policy `extension-push`, preferences editable.
- Strict Snap: without the rev6 extension or with the control center closed, expect the integration rows disabled; bridge-carried M3 needs both.
