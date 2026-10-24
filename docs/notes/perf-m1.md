# M1 — real Stop / Resume: implementation and measurements

Date: [validation date omitted]. Base: `9cb6ea5672d10ca971666ffa0c170996e7b5dd94`.
Branch/worktree: `feat/gnome-policy-stop`, `<private-path>`.
Local implementation only: no push, PR, merge, installation or publication.
GNOME-session and real confined-package acceptance remain **pending**.

## Implementation decisions

This deliberately changes the early plan's handle-reuse suggestion: STOP destroys
the **player process**, not merely its media reference. The existing server and
Control Center survive. RESUME creates only the player with fresh VLC/WebKit
handles. No unsupported VLC methods, renderer selector or policy-engine stub is
introduced. `--no-disable-screensaver`, Wnck and the GNOME bridge architecture are
unchanged.

| Operation | Resources/lifecycle | Persistence |
|---|---|---|
| PAUSE | Existing VLC fade/pause; player and decoder allocations retained | Existing user-pause behavior; not redesigned in M1 |
| STOP | Dedicated player session terminated and child reaped; bounded TERM/KILL fallback cleans its process group | `is_stopped=true` in config; server/GUI stay alive |
| RESUME | Fresh player/decoder/WebKit process, not a restart of Apricus | Explicitly clears `is_stopped`; reloads current selection and preferences |
| QUIT | Existing application shutdown (server, GUI, player) | Does not clear the STOP latch |

Public API is additive on the **existing server interface**: `stop_playback()`,
`resume_playback()` and read-only `playback_state` (`playing`, `paused`, `stopped`,
or `unavailable`). Both commands are idempotent. No toggle method, new bus name,
permission, player control interface or `PolicyChanged` signal is added.
The same commit includes the two methods in `snap_ipc.ServiceProxy._METHODS`.
That set is a client dispatch hint, **not** a server-side authorization boundary.

The server owns manual STOP. Startup, reload, forced respawn, existing automatic
start callbacks and wallpaper/playlist selection cannot clear it. Only explicit
`resume_playback()` clears it. Ordinary config saves preserve the on-disk latch
under an `fcntl` file lock; otherwise a late GUI or playlist writer could overwrite
STOP (or reintroduce it after RESUME) using an older config snapshot. Existing
atomic rename, backup and file permissions are retained.

Player trampolines call `setsid()` before importing the renderer. STOP signals
only that owned multiprocessing child/session, never processes by name. SIGTERM
is handled on the player GLib loop for normal VLC cleanup; an unresponsive
resolver is bounded by the server's shutdown waits (0.75 + 2 + 2 seconds, not a
D-Bus method timeout). Thus the normal <300 ms target is **not** a worst-case
guarantee. Killing the whole renderer is why this release does not rely on the
unproven claim that `media.release()` frees a decoder.

### Media and static wallpaper behavior

| Mode | STOP | RESUME / limitations |
|---|---|---|
| Video | Player exits; no active decoder | Same selected file and per-monitor assignments; starts from the beginning |
| Playlist | Pending EndReached/automatic-play callbacks check STOP; no advance or stale index save | Saved queue index is restored for non-shuffled queues; shuffles retain existing reshuffle behavior; no frame/time guarantee |
| Stream | Network/decoder resources owned by the player are destroyed | URL is resolved again; authentication/network failure uses existing error reporting; no frame/time guarantee |
| Web | WebKit renderer and child processes are stopped, not merely hidden | Page reloads with current selection and mute; DOM/session progress is not retained; WebKit volume remains the existing unsupported feature |

Existing WebKit PAUSE remains unsupported; Now Playing does not offer a working
pause button for a running web wallpaper. Web STOP/RESUME is supported.

- `is_static_wallpaper=true`: apply the selected source's existing cache; video
  and stream use the existing SHA1 naming, web uses its existing SHA256 naming,
  playlist uses its existing first-queued-item static convention. On cache miss,
  use a valid neutral 1x1 PNG (scaled by the desktop), never another selection's
  frame. **No decoder, network fetch or FFmpeg is started while stopped.** A
  fresh frame is prepared on normal playback/resume. This is intentional: a
  cold-cache STOP does not promise a freshly extracted video frame.
- `is_static_wallpaper=false`: leave the desktop background untouched, including
  on RESUME; do not force a generated image or change this preference. It may
  therefore remain a previous/user-selected desktop background, as the option
  requests. STOP does not prune images, which could otherwise delete a currently
  referenced desktop background.
- Changing wallpaper or mode while stopped persists the selection and updates
  only the cached/fallback background when enabled. It never starts a player.
- FFmpeg/FFprobe static jobs are tracked, cancelled and waited on during player
  shutdown. Cache commit and apply run on the main loop, checking the request
  epoch, closing state, current persisted selection, static option and STOP
  latch. Unique temporary images are discarded rather than overwriting a newer
  cache. This also covers the gap between a server config save and the player's
  queued reload callback. Late WebKit snapshots check current persisted state
  and source too.
- The desktop's single static background remains the existing GNOME backend's
  responsibility; independent static composites per monitor are not added in M1.
- The bridge's loaded/operational status is not playback status. A stopped
  wallpaper can correctly have zero adopted actors; M1 does not change the
  bridge's lease, authentication or control surface.

GUI: Now Playing Stop/Resume button, Playing/Paused/Stopped status (derived from
actual VLC playback where available), stopped sidebar status, explicit Stop and
Resume menu entries. GUI commands run off its UI thread, show failures rather
than silently toggling a label, and read back the server state. New strings are
translated in all five shipped languages (`it`, `de`, `fr`, `es`, `ja`).

## Isolation and methodology

`tests/perf_m1.py` is opt-in and refuses to run without
`APRICUS_M1_ISOLATED=1` and `XDG_CURRENT_DESKTOP=M1_TEST`.
The runs used a private Xvfb display and `dbus-run-session` bus, private HOME/XDG
directories, memory-only GSettings, nonexistent system-bus and PulseAudio
addresses, and software rendering. No extension, active GNOME settings, live
application, host service or Agent-A process was changed. Existing dependencies
were used; nothing was installed and no sudo was requested.

Environment: x86_64 Linux 7.0.0-38-generic, Python 3.14.4, LibVLC 3.0.23,
FFmpeg 8.0.1. Clip: generated silent 1280x720 MPEG4, 30 fps, 8 seconds.
25 video + 25 playlist stop/resume cycles per complete run. Every cycle repeats
STOP and RESUME, checks the old player PID no longer exists, verifies a new PID
on resume, and confirms legacy automatic start cannot clear STOP.

Metrics are totals over the server process subtree (server, multiprocessing
forkserver/resource tracker, and active player), **not** GNOME Shell or GPU
measurements. GUI was launched separately after the measured cycles. CPU uses
`/proc/<pid>/stat` tick deltas over approximately 300 ms, normalized to one core;
RSS/VSZ use `/proc/<pid>/status`, PSS uses `smaps_rollup`. RSS double-counts shared
pages, PSS apportions them, VSZ is virtual address space, not physical allocation.
0% in a short sample means no counted tick, not a guarantee of literally zero CPU.
RESUME latency ends at VLC's playing state, **not a verified first visible frame**.

The PAUSE sample is a single short post-fade sample, not a steady-state benchmark.
No GPU, energy, 24-hour leak/soak, hardware-decoder or Wayland-compositor savings
are claimed from these software-rendered Xvfb results.

## Measurements

Final complete run: `<private-evidence-dir>`, exit 0.
Full raw resource samples, private server log and driver log remain in that
directory; rerunning the checked-in harness regenerates them. Earlier complete
50-cycle runs also passed; the following numbers refer **only to this run**.

| Metric (subtree total) | Before STOP, mean (50) | STOP, mean (50) | After RESUME, mean (50) | PAUSE, one sample |
|---|---:|---:|---:|---:|
| CPU, % of one core | 83.84 | 0.00 | 105.79 | 37.58 |
| RSS, KiB | 389,994 | 151,329 | 388,729 | 393,492 |
| PSS, KiB | 241,416 | 84,466 | 240,179 | 230,234 |
| VSZ, KiB | 4,285,948 | 506,733 | 4,283,996 | 4,279,532 |
| Processes | 4 | 3 | 4 | 4 |
| Threads | 49.96 | 6 | 50 | 47 |

Ranges across the 50 cycles:

- Before STOP: RSS 383,452–429,004 KiB; PSS 221,587–283,493 KiB;
  VSZ 4,279,684–4,383,576 KiB; CPU 69.88–102.67%; 4 processes, 48–50 threads.
- STOP: RSS 147,456–151,896 KiB; PSS 68,006–87,072 KiB;
  VSZ 503,456–506,940 KiB; 0 counted CPU ticks; exactly 3 processes/6 threads
  every cycle (server + forkserver + resource tracker). No player PID remains.
- After RESUME: RSS 383,200–398,520 KiB; PSS 219,191–253,036 KiB;
  VSZ 4,280,984–4,350,004 KiB; CPU 96.45–113.18%; exactly 4 processes/50 threads.
- STOP latency: mean 32.94 ms, min 22.99, max 60.10.
- RESUME to VLC-playing latency: mean 232.41 ms, min 209.45, max 255.46.

These data demonstrate actual reclamation of the renderer's process/address
space and decoding work in this environment. They do **not** isolate a benefit
of `media.release()` (not the implementation), prove hardware-GPU savings, or
establish a steady-state CPU/energy comparison against PAUSE. The short RESUME
samples include decoder startup and are not steady-state playback comparisons.

Additional private-display checks passed: web STOP removed its three recorded
renderer/WebKit PIDs; HTTP-stream STOP removed its recorded player PID; the
GTK4 GUI kept the same PID over STOP; restarting the server preserved STOP and
did not create/register a player. The video/playlist checks verified a fresh
player PID after **every** RESUME.

| Cycle | Mode | STOP ms | RESUME to playing ms |
|---:|---|---:|---:|
| 1 | Video | 29.2 | 247.9 |
| 2 | Video | 26.2 | 249.0 |
| 3 | Video | 27.3 | 239.8 |
| 4 | Video | 27.7 | 242.6 |
| 5 | Video | 27.9 | 255.5 |
| 6 | Video | 24.6 | 244.7 |
| 7 | Video | 32.7 | 244.8 |
| 8 | Video | 42.2 | 224.1 |
| 9 | Video | 25.5 | 224.4 |
| 10 | Video | 24.3 | 232.0 |
| 11 | Video | 37.7 | 224.5 |
| 12 | Video | 24.5 | 253.6 |
| 13 | Video | 60.1 | 225.6 |
| 14 | Video | 24.8 | 231.0 |
| 15 | Video | 27.4 | 226.9 |
| 16 | Video | 25.5 | 228.5 |
| 17 | Video | 26.3 | 248.8 |
| 18 | Video | 24.3 | 229.9 |
| 19 | Video | 24.8 | 237.9 |
| 20 | Video | 23.9 | 229.8 |
| 21 | Video | 41.8 | 227.5 |
| 22 | Video | 38.3 | 253.5 |
| 23 | Video | 32.4 | 229.8 |
| 24 | Video | 24.1 | 232.9 |
| 25 | Video | 32.3 | 224.4 |
| 26 | Playlist | 41.4 | 209.5 |
| 27 | Playlist | 26.9 | 244.1 |
| 28 | Playlist | 23.0 | 223.3 |
| 29 | Playlist | 45.3 | 215.6 |
| 30 | Playlist | 35.7 | 226.9 |
| 31 | Playlist | 29.5 | 233.2 |
| 32 | Playlist | 46.8 | 239.0 |
| 33 | Playlist | 38.5 | 220.0 |
| 34 | Playlist | 47.2 | 224.9 |
| 35 | Playlist | 38.4 | 212.0 |
| 36 | Playlist | 28.3 | 209.9 |
| 37 | Playlist | 24.6 | 247.7 |
| 38 | Playlist | 36.4 | 216.4 |
| 39 | Playlist | 24.9 | 223.9 |
| 40 | Playlist | 47.5 | 226.7 |
| 41 | Playlist | 24.2 | 232.8 |
| 42 | Playlist | 43.5 | 237.4 |
| 43 | Playlist | 34.3 | 231.6 |
| 44 | Playlist | 44.4 | 232.5 |
| 45 | Playlist | 23.5 | 234.5 |
| 46 | Playlist | 25.0 | 233.9 |
| 47 | Playlist | 47.3 | 248.6 |
| 48 | Playlist | 48.4 | 224.0 |
| 49 | Playlist | 23.6 | 229.2 |
| 50 | Playlist | 42.3 | 233.7 |

## Reproduction (isolated only)

Use an existing Python environment containing the project's test/runtime
dependencies. Do not install anything into a user's session to run this command.
Example from the M1 worktree:

```bash
PYTHON=<source-root>/.venv/bin/python
root=$(mktemp -d "${TMPDIR:-/tmp}/apricus-perf.XXXXXX")
mkdir -m 700 "$root/runtime" "$root/home"
env -u WAYLAND_DISPLAY -u DBUS_SESSION_BUS_ADDRESS -u SNAP -u FLATPAK_ID \
  HOME="$root/home" XDG_CONFIG_HOME="$root/config" XDG_CACHE_HOME="$root/cache" \
  XDG_DATA_HOME="$root/data" XDG_RUNTIME_DIR="$root/runtime" \
  DBUS_SYSTEM_BUS_ADDRESS="unix:path=$root/no-system-bus" \
  XDG_CURRENT_DESKTOP=M1_TEST GSETTINGS_BACKEND=memory APRICUS_M1_ISOLATED=1 \
  PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src GDK_BACKEND=x11 \
  LIBGL_ALWAYS_SOFTWARE=1 PULSE_SERVER="unix:$root/no-pulse" TMPDIR="${TMPDIR:-/tmp}" \
  NO_AT_BRIDGE=1 GTK_A11Y=none GIO_USE_VFS=local \
  xvfb-run -a -s '-screen 0 1280x720x24' dbus-run-session -- \
  "$PYTHON" tests/perf_m1.py > "$root/driver.log" 2>&1
# Results: $root/results.json (all per-cycle resource samples), server.log, driver.log.
```

The HTTP stream fixture supplies a deterministic test resolver and serves the
clip on a private loopback port; VLC and HTTP I/O are real. It does **not** validate
production yt-dlp extraction, remote authentication or arbitrary network streams,
and does not relax any production URL/sandbox checks. The web fixture is local
HTML and does not disable the WebKit sandbox. A real GTK4 Control Center is also
opened on Xvfb and its PID is checked to survive STOP; server restart is checked
to retain STOP without registering a player bus name.

## Automated validation

- Complete suite on private Xvfb + D-Bus: **377 passed**, no skips, 22.67 s.
  15 warnings are GI/asyncio deprecations; no failing test is hidden.
- Ruff lint and formatting checks over `src` and `tests`: passed.
- `msgfmt --check` for every shipped PO catalog: passed.
- `gjs -m tests/gjs/status-bridge.js`: PASS (no Shell or real session bus).
  The full pytest suite also exercises the bridge authentication/recovery
  round trip using a separately spawned private D-Bus session.
- `git diff --check`: passed. No GJS/extension/bridge production code changed.

## Acceptance / remaining gates

- Automated coverage includes all four media modes, duplicate commands, stopped
  selection/reload/forced-respawn/startup, actual owned-process reaping, STOP
  during a real FFmpeg job, late local/stream frame commits, playlist EndReached
  suppression, fresh handles on resume, late config writers, volume/mute/selection
  preservation, GUI command wiring, and a real Unix-socket `snap_ipc` round trip.
- Native D-Bus commands and property are exercised on the private session bus;
  no new bus permission is assumed. Existing bridge authentication/recovery and
  GJS tests remain regression gates; extension/adoption code is not changed.
- Pending separate authorization/environment: **actual confined Snap and
  Flatpak**, real GNOME 49/50/51 + DING stacking after new player adoption,
  bridge status while stopping/resuming in strict Snap, real remote streams,
  hardware GPU/audio, multi-monitor hotplug and suspend/lock/unlock in GNOME.
  Mock/socket/private-bus successes must not be presented as sandbox or Shell
  compatibility certification.
- Also pending: visual desktop fallback/cache validation in GNOME, long soak,
  and startup/cancellation latency under an intentionally unresponsive resolver.
  The bounded fallback and request invalidation have automated tests, but no
  claim is made that every forced shutdown finishes below 300 ms.
- No changes to the roadmap worktree, integration branch, PR #1, GNOME bridge,
  extension, Wnck backend, packaging, workflows, release versions or rulesets.

Only M1 was implemented. M2–M10 remain outside this change.
