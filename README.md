<p align="center"><img src="res/apricus.svg" width="256"></p>

<p align="center">Live wallpaper for Linux. Written in Python.</p>
<p align="center">Apricus (lat.) — sunny, exposed to the sun.</p>

<p align="center">
<a href="https://github.com/fabx-dev/apricus/actions/workflows/ci.yml"><img src="https://github.com/fabx-dev/apricus/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
</p>

# Apricus

Apricus is a community fork of [Hidamari](https://github.com/jeffshee/hidamari)
by Jeff Shee (GPL-3.0, same license). It picks up where Hidamari left off
and pushes live wallpapers on Linux forward: **real Wayland support,
multi-desktop support, fast login startup, and a robust player**.

> Status: active development. The `master` branch tracks upstream v3.7;
> current work happens on feature branches (see `PLAN.md`).

## Features

- Autostart after login
- Static wallpaper from video frame, with blur effect <sup>1</sup>
- Pause on maximized window / fullscreen <sup>2</sup>
- Volume control, mute/pause in two clicks
- I'm feeling lucky (random video)
- Hardware accelerated decoding via VLC <sup>3</sup>
- Multi-monitor support, per-monitor video
- Streaming URL support via yt-dlp <sup>4</sup>
- Webpage as wallpaper <sup>5</sup>

<sup>1</sup> Currently GNOME only.
<sup>2</sup> Window-state detection is currently X11 only; Wayland support is on the roadmap.
<sup>3</sup> VLC backend; HW acceleration varies by driver (NVIDIA + Wayland is fragile).
<sup>4</sup> Tested with YouTube.
<sup>5</sup> Anything from a plain page to WebGL content.

## What changed vs upstream

See [PLAN.md](PLAN.md) for the full roadmap. So far (branch `fase-1b`):

- Startup timing instrumentation (`apricus.perf`, `[Perf]` marks)
- Stable per-monitor config keys (duplicate models get `Model#i`, with legacy migration)
- `feeling_lucky` no longer crashes on an empty library (upstream #272)
- Correct dark-mode wallpaper restore, `ffprobe` timeouts, headless-safe imports
- CI (lint + format check + pytest) and a headless-safe test suite

## Installation (from source)

Prerequisites per distro are listed in [docs/dev.md](docs/dev.md). Then:

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

Put videos in the video folder (`~/Video/Hidamari` or `~/Videos/Hidamari`
depending on distro, kept from Hidamari for compatibility) and pick them from the GUI. A Flatpak package is planned
but not published yet.

## Development

```bash
make lint     # ruff check
make format   # ruff format
uv run pytest tests/ -q
```

`PLAN.md` is the roadmap. Issues and pull requests are welcome.

## Credits

- Upstream project: [jeffshee/hidamari](https://github.com/jeffshee/hidamari)
- Icons: [Freepik](http://www.freepik.com/) from [Flaticon](https://www.flaticon.com)
- License: GPL-3.0-or-later (see `COPYING`)

## Screenshots

![](res/screenshot-1.png)
![](res/screenshot-2.png)
![](res/screenshot-3.png)
