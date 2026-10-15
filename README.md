<p align="center"><img src="res/apricus.svg" width="256"></p>

<p align="center">Live wallpaper for Linux. Written in Python.</p>
<p align="center">Apricus (lat.) — sunny, exposed to the sun.</p>

<p align="center">
<a href="https://github.com/fabx-dev/apricus/actions/workflows/ci.yml"><img src="https://github.com/fabx-dev/apricus/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

# Apricus

Apricus is a community fork of [Hidamari](https://github.com/jeffshee/hidamari)
by Jeff Shee (GPL-3.0, same license). It picks up where Hidamari left off:
**fast login startup, a robust player, per-monitor correctness, and a cleaner
path to Wayland and multi-desktop support**.

> Status: **2.0.0 RC3** — release candidate on branch `gtk4-migration`.
> This is a test version: please report issues before the stable 2.0.0.
> It upgrades Apricus 1.0 directly (same app ID, config and data preserved).

## Features

- Autostart after login (`.desktop` file or user systemd unit)
- Instant wallpaper at login: last frame applied immediately, refreshed in background
- Static wallpaper from the current video frame <sup>1</sup>
- Pause on maximized window / fullscreen <sup>2</sup>
- Volume control, mute/pause in two clicks <sup>3</sup>
- Muted looping playlist with shuffle, per-monitor queues <sup>4</sup>
- I'm feeling lucky (random video)
- Hardware accelerated decoding via VLC <sup>5</sup>
- Multi-monitor support, per-monitor video <sup>6</sup>
- Video library folder of your choice, shown in the panel <sup>7</sup>
- Streaming URL support via yt-dlp <sup>8</sup>
- Streaming rows with cached thumbnails and LIVE badge <sup>11</sup>
- Webpage as wallpaper <sup>9</sup>
- New GTK4 panel: Library, Playlist, Now Playing, Streaming, Web Page, Settings <sup>12</sup>
- Interface in English, Italian, German, French, Spanish and Japanese <sup>10</sup>

<sup>1</sup> Currently GNOME only.
<sup>2</sup> Window-state detection is currently X11 only. On Wayland the video
still plays (rendered via XWayland); auto-pause there needs a Shell extension
companion, which is on the roadmap.
<sup>3</sup> Volume and mute have no effect in playlist mode (muted by design,
for PipeWire stability) and their controls are greyed out there.
<sup>4</sup> A queue left over from single-video mode stays saved but paused —
resumable anytime, never wiped silently.
<sup>5</sup> VLC backend; HW acceleration varies by driver (NVIDIA + Wayland is fragile).
<sup>6</sup> Duplicate monitor models get stable `Model#i` config keys, so two
identical screens no longer overwrite each other.
<sup>7</sup> Flat scan: each folder shows its own videos. Nothing is assumed:
on first run you choose the folder, unless a Hidamari library already exists
(reused automatically).
<sup>8</sup> Tested with YouTube (needs Deno ≥ 2.3 on the system; included in the Flatpak).
<sup>9</sup> Anything from a plain page to WebGL content. JavaScript stays on
(pages need it); local-file access from remote pages is blocked.
<sup>10</sup> Selected automatically from the system locale. The classic panel is
fully translated (it/de/fr/es; ja partial). The new GTK4 panel strings are
marked for translation: Italian is complete, German/French/Spanish/Japanese
are in progress and fall back to English where untranslated.
<sup>11</sup> Thumbnails are cached under `~/.cache/apricus/stream-thumbs/`,
loaded lazily (max 2 concurrent downloads), with a monogram fallback when
no image is available. The LIVE badge appears only for confirmed live
streams.
<sup>12</sup> The GTK4 + Libadwaita control panel is the default GUI
(`APRICUS_GTK4_TAB` selects the starting section).

## What changed vs upstream

**Playlist (new)**
- Muted looping queue with its own panel tab (add/remove/reorder/shuffle/
  apply), per-monitor queues, D-Bus API, config v5 with migration
- A queue interrupted by a single video stays saved and paused — resumable,
  never wiped

**Startup & performance**
- Login-to-first-frame from ~1.7s to ~1.4s (1080p): shared process clock with
  `[Perf]` marks, persistent static-frame cache applied instantly and refreshed
  in background, `ffprobe` sidecar cache, no redundant seeks on the startup path
- `--fast` mode and a user systemd unit (`apricus --systemd-enable`) for
  low-contention login startup

**Player**
- Persistent player: video switches happen in place (no kill/respawn gap),
  with the new frame painted over the old one until real playback starts
- Thumbnail loading marshalled to the GTK main thread (fixes intermittent
  native crashes of the control panel)
- VLC widget cleanup, single-shot monitor sync, logging that works in forked
  children, `ffprobe` timeouts, headless-safe imports

**Correctness**
- Stable per-monitor config keys with legacy migration
- Correct dark-mode wallpaper restore; `feeling_lucky` no longer crashes on an
  empty library (upstream #272)
- Old configs are migrated, never wiped (video folder default, blur key
  removal, Hidamari import on first run)
- Server no longer persists stale in-memory configs over newer on-disk ones;
  D-Bus callers are logged, foreign UIDs rejected

**Security hardening**
- Timeouts on ffmpeg/requests/yt-dlp/systemctl; atomic config writes with
  backup and recovery; numeric validation at load; bounded thumbnail pool;
  capped playlists; validated video paths; hardened WebKit defaults

**Desktop integration**
- Desktop-environment backends in one place (`apricus.backends`): GNOME vs
  generic wallpaper handling, X11 vs Wayland window-state selection
- Full rebrand: `io.github.fabx_dev.Apricus` app ID, D-Bus names, original icon
- Flatpak builds and plays video (`make flatpak`; Flathub publishing planned)

**Removed vs upstream**
- Static-wallpaper blur: it only added startup cost for a wallpaper that sits
  underneath the video — frames are now saved sharp and Pillow is gone

## New in 2.0.0 RC3

- GTK4 + Libadwaita control panel enabled by default (legacy GTK3 launch path removed)
- Updated application icon (hicolor PNGs and scalable SVG)
- CI-green test suite on the tagged commit (portable test stubs)

## New in 2.0.0 RC2

- Streaming over HTTPS plays in the Flatpak (bundled VLC tolerates denied
  TCP Fast Open and falls back to a normal connection; verified live)
- Now Playing hero follows the playlist queue head, and play/pause works
  in playlist mode; Up-next rows no longer flash on refresh
- Player failures are recorded (`playback_error()`) instead of staying
  silently stuck on a dead stream (not yet shown in the panel UI)
- Stream and web hosts on local networks are refused before any contact

## New in 2.0.0 RC1

- GTK4 panel (`APRICUS_GTK4=1`): sidebar with Library, Playlist, Now Playing,
  Streaming, Web Page and Settings; visual playlist rows with drag-and-drop
  and undo; monitor chooser on Library/Streaming/Web Page apply
- Streaming thumbnails: cached, lazy, deduplicated, max 2 concurrent
  downloads; LIVE badge only from confirmed stream metadata
- Shared yt-dlp options (`yt_utils.base_options()`); Deno JavaScript runtime
  bundled in the Flatpak (`pkgs/flatpak/deno.json`), optional on classic
  installs (YouTube formats degrade gracefully without it)
- Static wallpaper stays pinned under streams (downloaded thumbnail or
  extracted frame) instead of falling back to blue
- Real stream titles persisted in history instead of bare hostnames

## Upgrade from 1.0

Same application ID (`io.github.fabx_dev.Apricus`), same config
(`~/.config/apricus/config.json`) and cache directories. 1.0 configs are
read and migrated, never wiped. The 1.0 → 2.0 update path has been reviewed
statically but not yet executed end-to-end; back up
`~/.config/apricus/` before testing the RC over a 1.0 install.

Note for scripters: the D-Bus methods `stream`, `webpage` and `video` take
`(url, monitor)` — pass `"all"` to apply on every monitor, as both panels do.

Rollback: 2.0 keeps the config at version 5 and only adds keys, which older
versions ignore and preserve (verified against the 1.0 loader). To revert,
quit Apricus and restore `~/.config/apricus/config.json.bak` over
`config.json` — or just keep using the file, 1.0 reads it as-is.

## Known limitations of this RC

- GTK4 panel: Italian complete; German/French/Spanish/Japanese strings still
  incomplete (English fallback where untranslated).
- `make flatpak` installs over the current Flatpak; to keep 1.0 installed,
  build to a separate directory and do not install the RC yet.
- End-to-end 1.0 → 2.0 update not yet executed; procedure documented above.
- YouTube playback without a JS runtime may 403 on protected videos;
  install Deno (classic) or use the Flatpak (bundled).
- Streaming or web pages hosted on local-network addresses (intranet,
  `localhost`) are refused: pasted URLs are resolved and probed
  automatically, so the app does not fetch non-public hosts.
- Flatpak sandbox: video folders outside `xdg-videos` are invisible to the
  app, and it cannot create folders there (read-only `xdg-videos:ro`
  permission, changed from 1.0). Use the classic install for custom
  library locations outside Videos.

## Installation (from source)

Prerequisites per distro are listed in [docs/dev.md](docs/dev.md) — note that
the native `vlc` and `ffmpeg` packages are required (the Flatpak bundles
them). Then:

```bash
git clone --recurse-submodules https://github.com/fabx-dev/apricus.git
cd apricus
make sync      # dev environment (uses system PyGObject)
make install   # installs into ~/.local (no sudo)
```

Run it (via the dev env, in debug mode):

```bash
make run
```

Pick videos from the GUI. On first run Apricus asks you to choose a video
library folder — nothing is assumed or auto-created. (An existing Hidamari
library is reused automatically for compatibility.)

A Flatpak (`io.github.fabx_dev.Apricus`) builds locally with `make flatpak`;
ready-made bundles are attached to each
[GitHub release](https://github.com/fabx-dev/apricus/releases) — install both
(the second one carries the translations). Bundle file names follow the
release version (e.g. `apricus-1.0.flatpak` for the 1.0 release):

```bash
flatpak install --user apricus-1.0.flatpak apricus-locale.flatpak
```

Flathub publishing is planned once the project has an established userbase
(Flathub currently rejects duplicate fork submissions without one).

Optional login autostart via systemd (instead of the `.desktop` file):

```bash
apricus --systemd-enable    # disable with --systemd-disable
```

## Development

```bash
make lint     # ruff check
make format   # ruff format
uv run pytest tests/ -q
```

Translatable strings live in `po/` (`make pot` / `make update-po`;
`po/gen_lang.py` regenerates the catalogs). Issues and pull requests are
welcome.

## Credits

- Upstream project: [jeffshee/hidamari](https://github.com/jeffshee/hidamari)
- App icon: original to Apricus
- License: GPL-3.0-or-later (see `COPYING`)

## Screenshots

![Video library](res/apricus-library.png)
![Playlist](res/apricus-playlist.png)

Legacy Hidamari screenshots (pre-rebrand UI):

![](res/screenshot-1.png)
![](res/screenshot-2.png)
![](res/screenshot-3.png)
