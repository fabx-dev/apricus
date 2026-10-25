# M3 — final validation status

## Validated scope

Native GNOME Shell **50.1 on Wayland**, Ubuntu 26.04.1: M3 was validated live
on candidate `4844213578e6dc443c816203ff2e9e0ad84c11b6`, with the reservations
below. The complete path was exercised: Shell tracker → publisher → real
D-Bus signal → backend → playback engine → native player.

The final targeted campaign passed loading/revision/owner/GetState/Ping,
fullscreen pause/resume, existing GUI preference editability, persisted
GUI → server → policy changes while fullscreen remained active, automatic
mute/removal, Play/Pause/Stop/Play, player GetAll, and desktop sanity. Earlier
live coverage of manual priorities, reconnect, sender rejection and wallpaper
readoption remains applicable; it was not repeated in full.

The subsequent local gettext correction translates four capability subtitles
in all supported catalogs (`it`, `de`, `fr`, `es`, `ja`), with extraction,
compiled-catalog and headless real-GUI-helper/domain regression coverage. It
does not change policy and has **not** been retested in the live session.

## Platform and packaging boundaries

| Platform | Status |
|---|---|
| Native GNOME Wayland 50.1 | Functionally validated live, with reservations |
| X11 | Existing behavior retained; no new live X11 validation claimed |
| Flatpak | Effective availability depends on verified permissions/runtime access; no permission presumed |
| Strict Snap on Wayland | New M3 push source not supported yet; stable Snap 2.0.7 unchanged |
| GNOME 49 / 51 | NOT TESTED live |

## Timing: observations, not a latency certification

The external observer used comparable monotonic timestamps and sampled every
100 ms. It recorded:

- **+92.9 ms** from its D-Bus signal observation to the first completed read
  showing policy `paused`.
- **+1,597 ms** from its signal observation to the first completed read showing
  player `paused`, compatible with the configured **1.5 s fade**.

These are observation times, not exact internal event timestamps. Compositor
notification, signal emission, server receipt/decision and VLC pause event were
not all timestamped directly. Sensor, communication/policy and actuation latency
cannot be rigorously separated from these samples. **The ≤500 ms detection +
decision target is NOT MEASURED / not rigorously attested**, not PASS. No exact
fade contribution is obtained by subtracting 1.5 s from an observed duration.
The local real-GLib timer regression around 250 ms is not a live latency result.

## Restoration and provenance

The final campaign backed up the complete affected configuration/cache,
static PNG, URI-referenced file and extension inventory before loading M3.
After shutdown, all affected file hashes and GNOME URIs matched that baseline.
After the user's second manual login, original **rev4** and DING were ACTIVE,
the M3 name was absent and no test processes remained. The user confirmed
desktop icon interaction, regular Overview and no orphan wallpaper windows.

**Historical reservation retained:** the preceding campaign regenerated a PNG
without a byte-identical backup. Its older bytes cannot be attested or recovered
by this later backup. The final campaign restored its own baseline, not those
lost historical bytes.

Overall verdict: **M3 functionally validated with reservations**, not latency-
certified and not a claim of Snap/Flatpak parity or GNOME 49/51 support.
