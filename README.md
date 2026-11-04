<p align="center"><img src="res/apricus.svg" width="192" alt="Apricus logo"></p>

<h1 align="center">Apricus 2.1.0</h1>

<p align="center"><strong>Live wallpaper for the GNOME desktop on Linux — local videos, playlists, streams and web pages.</strong></p>
<p align="center"><em>Apricus (lat.) — sunny, warmed by the sun.</em></p>

<p align="center">
  <a href="https://github.com/fabx-dev/apricus/releases"><img src="https://img.shields.io/github/v/release/fabx-dev/apricus?label=release&color=blue" alt="Latest Release"></a>
  <a href="https://github.com/fabx-dev/apricus/actions/workflows/ci.yml"><img src="https://github.com/fabx-dev/apricus/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://snapcraft.io/apricus"><img alt="Snap Store" src="https://snapcraft.io/apricus/badge.svg" /></a>
  <br>
  <a href="COPYING"><img src="https://img.shields.io/badge/license-GPL--3.0--or--later-blue" alt="License: GPL-3.0-or-later"></a>
  <img src="https://img.shields.io/badge/GTK4%20%2B%20Libadwaita-GNOME-4A86CF?logo=gnome&logoColor=white" alt="GTK4 + Libadwaita">
  <img src="https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white" alt="Python 3.10+">
</p>

---

Apricus turns your GNOME desktop background into a living canvas: a local clip, a looping playlist, a live stream, or an interactive web page. It is a community fork of [Hidamari](https://github.com/jeffshee/hidamari), rebuilt around a GTK4 + Libadwaita control center, a server-owned playback policy engine, and a GNOME Shell integration for fullscreen/maximized awareness on Wayland.

Why Apricus:

- Native GTK4 control center with Library, Playlist, Now Playing, Streaming, Web Page and Settings workspaces.
- Playback Policy Engine: manual Pause and Stop persist and are never cleared by automation; playback resumes only when the current policy permits it.
- GNOME Wayland integration: fullscreen/maximized sensing through a small Shell extension, with preferences that apply immediately.
- Per-monitor wallpaper selection, static-frame login continuity, and six interface languages.

---

## What's new in 2.1.0

2.1.0 closes the M1–M3 cycle. No suspend/resume (M4) behavior is included.

**M1 — Playback controls**

- New **Now Playing** panel with media hero, status badge, transport and media details.
- Dynamic **Play/Pause** button plus a separate **Stop** button.
- Manual Pause and Stop are persisted; automatic events never clear them. Play resumes only when the current policy permits it.
- Pause reaches VLC without the previous pre-pause fade. Compatible pausable sources retain their media across pause/resume; this does not guarantee continuity for every stream.

**M2 — Playback Policy Engine**

- One server-owned engine decides every playback transition.
- Manual pause/stop latches survive restarts; automatic reasons never clear them.
- Pause/mute preferences for maximized windows, applied to the current state without restarts.
- Playlist selection, position and queue behavior kept consistent with the policy.

**M3 — GNOME Wayland integration**

- The Shell extension reports fullscreen/maximized truth over a dedicated versioned D-Bus channel.
- The server validates sender and sequence, reconciles on reconnect, and freezes safely on signal loss.
- Preferences changed in the GUI reach the running server immediately.

**Interact with webpage**

- Interact with the same running WebKit page and WebView, without reloading or opening a browser, then return with **Done — Return to desktop**, owned by GNOME Shell.
- Requires GNOME Wayland with integration revision 8 on native/Snap installations; unavailable in the 2.1.0 Flatpak package.

See [RELEASE_NOTES_2.1.0.md](RELEASE_NOTES_2.1.0.md) and [CHANGELOG.md](CHANGELOG.md) for the full notes.

---

## Features

- **Local video wallpapers** from your video folder, with asynchronous thumbnails and per-monitor selection.
- **Looping playlists** with drag-and-drop reorder, shuffle and undo.
- **Streams**: YouTube, Twitch, live and direct video links, with 10-second preview, favorites and history. Native packages bundle Deno and yt-dlp; some YouTube videos may not play reliably. The Streaming information button explains this limitation on demand.
- **Web pages**: any URL or local `.html` file as wallpaper, with favorites, recent history and an Inspiration gallery.
- **Multi-monitor**: deterministic per-monitor selection, including identical models.
- **Now Playing**: live status, transport (Play/Pause, Stop, volume/mute), media inspector and up-next preview.
- **Policy automation**: pause or mute when a window is fullscreen/maximized, configurable in Settings.
- **GNOME integration**: companion extension `apricus-integration@fabx-dev` keeps wallpaper below DING icons and application windows, and feeds window state to the policy engine.

---

## Screenshots

Screenshots live in `res/` and illustrate the GTK4 interface; they predate final release acceptance:

![Library](res/apricus-2-library.png)
![Now Playing](res/apricus-2-now-playing.png)
![Playlist](res/apricus-2-playlist.png)
![Streaming](res/apricus-2-streaming.png)
![Web page](res/apricus-2-webpage.png)
![Settings](res/apricus-2-settings.png)

> Note: the Settings screenshot still shows the previous “Requires X11” subtitle, and Streaming does not yet show the new information button. In 2.1.0 the pause/mute rows explain the extension requirement on capable GNOME Wayland. These images are illustrative, not final-package validation evidence.

---

## Installation

Packages are distributed through [GitHub Releases](https://github.com/fabx-dev/apricus/releases). **Apricus 2.1.0 is locally finalized with release date 2026-11-04; publication has not yet taken place and requires separate approval.** Filenames below match the prepared artifacts, not a claim that they are already available online. See the [final release readiness report](docs/FINAL_RELEASE_READINESS_2.1.0.md) for verification scope and publication gates.

### Debian / Ubuntu (`.deb`)

```bash
sudo apt install ./apricus_2.1.0_amd64.deb
```

Or build locally into `dist/`:

```bash
make deb
```

### Fedora / compatible distributions (`.rpm`)

The dependency and startup smoke test used **Fedora 43 with RPM Fusion free enabled**. Enable the matching free repository using the [RPM Fusion configuration instructions](https://rpmfusion.org/Configuration) before installing; stock Fedora repositories alone may not provide all VLC dependencies. This is not a visual GNOME Wayland certification on Fedora or a claim of validation on other compatible distributions.

```bash
sudo dnf install ./apricus-2.1.0-1.x86_64.rpm
```

Or build locally into `dist/`:

```bash
make rpm
```

### Flatpak

The prepared bundles require Flatpak and the **GNOME Platform 50** runtime. Configure the user Flathub remote for runtime dependencies, then install each local bundle separately:

```bash
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user flathub org.gnome.Platform//50
flatpak install --user ./apricus-2.1.0.flatpak
flatpak install --user ./apricus-locale.flatpak
```

**Flatpak publication requires a separate decision and has not been approved.** These commands describe local bundle installation, not an Apricus listing on Flathub. Webpage display is available, but **Interact with webpage is unavailable in this package**.

Or build locally from source with `flatpak-builder` installed and the user Flathub remote configured (pulls the GNOME 50 runtime/SDK and installs the resulting app):

```bash
make flatpak
```

### Snap

```bash
sudo snap install apricus
```

This installs the currently published Store version (2.0.7 rev15 during local 2.1.0 finalization), not the locally finalized, unpublished 2.1.0. For an explicitly approved local test, quit Apricus first and verify its app/helper processes have exited, then use `sudo snap install --dangerous ./apricus_2.1.0_amd64.snap`. The Snap remains strict-confined. Back up user data before replacing an installation; see the [final release readiness report](docs/FINAL_RELEASE_READINESS_2.1.0.md) for upgrade and rollback scope.

### From source

Prerequisites per distribution are listed in [docs/dev.md](docs/dev.md).

```bash
git clone --recurse-submodules https://github.com/fabx-dev/apricus.git
cd apricus
make sync      # create/refresh uv environment (uses system PyGObject)
make install   # install into ~/.local (no sudo required)
make run       # launch in debug mode
```

---

## GNOME integration

GNOME Wayland sessions require **XWayland**: Apricus currently uses the X11 backend for its wallpaper player. Fullscreen/maximized integration and direct webpage interaction additionally require the matching GNOME Shell extension; the tested visible session was GNOME Shell/Mutter 50.1 Wayland. See the compatibility table below for unverified environments.

Apricus ships a companion extension, `apricus-integration@fabx-dev` (source in `gnome-extension/`), revision 8 in release 2.1.0:

- Hosts wallpaper windows in the Shell background layer, below DING icons and app windows.
- Reports fullscreen/maximized state to the server over `org.gnome.Shell.Extensions.ApricusIntegration`.
- Carries no playback policy logic. Window-state sensing reports aggregates; the separate authenticated interaction channel binds only the current wallpaper identity and generation.

Setup:

1. Native `.deb`/`.rpm` installs place the extension under `/usr/share/gnome-shell/extensions/`. For Snap/Flatpak, which cannot write host extension directories from their sandbox, run `./gnome-extension/build-zip.sh` from the source checkout root, then `./gnome-extension/install-host.sh --zip ./dist/apricus-integration@fabx-dev.zip --enable` on the host (per-user, no sudo). You may use the prepared extension ZIP instead, passing its actual path to `--zip`.
2. For a newly installed extension that GNOME Shell does not recognize yet, log out and back in first; then enable once with `gnome-extensions enable apricus-integration@fabx-dev`.
3. After updating an already loaded extension, log out and back in so the Shell loads revision 8.
4. Confirm with `journalctl --user -g apricus-integration` (expect `enabled, code revision 8`).

Updating works the same way: overwrite the files, then log out/in. Removing Apricus never touches `~/.local/share/gnome-shell/extensions/`; disable and delete that directory manually if desired.

Package differences:

- **Native DEB/RPM**: M3 push and same-window web interaction with the matching rev8 extension on GNOME Wayland.
- **Flatpak**: webpage display is available, but **Interact with webpage is unavailable in 2.1.0**. Exact bridge-name permission alone cannot resolve the Shell-owner check and sandbox/host PID mismatch in rev8. No broad bus access or confinement bypass is granted. Window-state preferences stay disabled until the server positively verifies a healthy `extension-push` source.
- **Strict Snap on Wayland**: window push and direct interaction arrive bridge-carried from rev8 to the server-owned Snap bridge; direct signal subscription stays unavailable under strict confinement. No control-center dependence.

---

## Usage

1. Open Apricus, pick a video in **Library** and apply it to one monitor or all monitors.
2. Build a queue in **Playlist**, then apply or shuffle it.
3. Use **Now Playing** for Play/Pause, Stop, volume/mute and media details.
4. In **Settings**, choose pause and/or mute behavior for maximized/fullscreen windows. With M3 active, changes apply to the current window state immediately.
5. For streams, paste a link in **Streaming** and use Try 10s before applying; for web walls use **Web Page** or the Inspiration gallery.

In 2.1.0, **Web Page → Interact with webpage** promotes the same
running webpage without reloading it. Finish with the native Shell control
**Done — Return to desktop** or the temporary shortcut displayed on that control.
Ctrl+Alt+Tab lets you reach the Shell's accessible Done control, which you then
activate; it does not itself end interaction. Esc alone remains available to the webpage. This requires
revision 8 of the GNOME integration and a running, unpaused web wallpaper;
manual Stop/Pause and automatic pause are never overridden. GUI closure leaves
Done available. After an unconfirmed rollback the page is hidden for safety:
apply it again to recover. Flatpak direct interaction is unavailable in 2.1.0;
adding a bridge permission alone is insufficient. Bounded visible strict-Snap
acceptance passed on x14; x15 passed its status-message refinement, with protected
runtime parity verified. See the [web interaction acceptance report](docs/WEB_INTERACTION_ACCEPTANCE_2.1.0.md) for the
precise scope. Web Page offers Stop/Resume, not a manual GUI Pause button.
Focusing another application intentionally ends interaction.

The preferred exit shortcut is **Super+Ctrl+Shift+F12**, with **Super+Ctrl+Shift+F11/F10** alternatives
only if an earlier choice is already reserved. Existing bindings are never
changed. **Super+Shift+Esc** belongs to Mutter's input-capture cancellation and
is not used by Apricus.

---

## Compatibility and limitations

| Area | Status in 2.1.0 |
|---|---|
| GNOME Wayland (Shell 50.1) | Strict-Snap x14/x15 bounded acceptance in the tested VM; native package and other hardware coverage are separate |
| GNOME 49 / 51 | Shipped per static API review, not runtime-tested |
| X11 sessions | Existing behavior retained |
| Flatpak | Webpage display, no direct webpage interaction; M3 depends on verified window-state access |
| Strict Snap on Wayland | Server-owned bridge push; direct signals unavailable |
| Detection+decision latency ≤ 500 ms | NOT rigorously attested; the pre-pause fade was removed, but no final sensor-to-visible-pause bound is certified |
| YouTube streaming | Some videos exhibit intermittent audio/video stalls; unresolved known limitation, not a guaranteed service |
| Other desktops (KDE, XFCE, …) | Not claimed; GNOME is the supported target |

Other desktop environments are not validated. Icon stacking and static-wallpaper sync are GNOME-specific. KDE Plasma manages its own background surface and is not supported. Bundled yt-dlp is deliberately unchanged: Snap 2026.10.26, DEB/RPM/Flatpak 2026.07.04; this is not a claim of identical streaming service behavior.

---

## Troubleshooting

- **Wrong GUI launches (`~/.local/bin/apricus` shadows `/usr/bin/apricus`)**: an old per-user install takes precedence on `PATH`. Check with `which -a apricus` and `apricus --help` (the description reports the version), remove or update the stale `~/.local` copy, then launch the packaged binary explicitly while testing.
- **YouTube fails or stalls**: some videos may not play reliably due to streaming service restrictions or compatibility limitations. The exact cause of the observed intermittent stalls is not established. Bundled Deno/yt-dlp and the Streaming information button do not imply the issue is fixed.
- **Missing video deps**: install `vlc`, `ffmpeg`, `python3-vlc` and the GTK4/Libadwaita typelibs listed in `docs/dev.md`.
- **Extension missing or stale**: Settings explains the requirement; reinstall the 2.1.0 extension files, enable, log out/in, and check `journalctl --user -g apricus-integration`.
- **Strict Snap shows integration disabled**: use the matching rev8 extension and a Snap that owns the bridge in its server process; reload the extension via logout/login after updating host files.
- **Flatpak cannot see videos**: it has read-only `xdg-videos` access; keep media under Videos or grant access with Flatseal.

---

## Development and contributing

```bash
make lint                    # ruff check src/
make format                  # ruff format src/
uv run pytest tests/ -q      # headless test suite
```

Translation catalogs (`po/*.po`):

```bash
make pot
make update-po
```

Please keep M1–M3 behavior and reservations intact, avoid new sandbox permissions without review, and do not present unverified platforms as supported. M4 (suspend/resume) is out of scope for 2.1.0.

---

## Credits and license

- Author and maintainer: [fabx-dev](https://github.com/fabx-dev)
- Upstream project: [Hidamari](https://github.com/jeffshee/hidamari) by Jeff Shee
- License: GPL-3.0-or-later (see [COPYING](COPYING))
