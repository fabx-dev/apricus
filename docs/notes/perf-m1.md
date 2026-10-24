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

## Real GNOME validation — phase 1 inventory ([validation date omitted])

**Read-only inventory completed. Phases 2–4 have NOT started.** The user has
requested an explicit authorization checkpoint before session changes or
installation. No application was launched/stopped, no dependency was installed,
and no extension, GSettings, autostart, package or service was changed in this
phase. Only this report is updated in the M1 worktree; Agent-A repositories and
the roadmap worktree are not used.

Source preflight: `feat/gnome-policy-stop`, clean at
`84bd123d19ecfd547be5f713bc7895a185c07ea9` before this documentary update.

| Inventory item | Result | Evidence / limits |
|---|---|---|
| OS/session | PASS | Ubuntu 26.04.1 LTS; GNOME Shell 50.1; Wayland |
| Installed Snap | PASS (inventory) | `apricus` 2.0.7 revision **x3**, local/untracked channel; `apricus-channel-poc` 0.1 x1 also installed |
| Active Apricus | PASS (inventory) | Snap server PID **[process ID omitted]**, forkserver **[process ID omitted]**, player **[process ID omitted]**; AppArmor `snap.apricus.apricus (enforce)`, cgroup and mapped `/snap/apricus/x3` libraries confirm strict Snap identity |
| Current Snap source | PASS (sampled identity) | Mounted `server.py`, `player/video_player.py`, `snap_ipc.py` match Git `9cb6ea5`, not M1; this is not a full archive identity assertion |
| Native local installation | PASS (inventory) | `~/.local/bin/apricus` and `~/.local/share/apricus` exist, launcher says 2.0.7; installed server has no M1 stop/resume methods. PATH prefers this launcher, so plain `apricus` must not be used for the M1 test |
| Native package / Flatpak | PASS (inventory) | No installed Apricus dpkg package or Flatpak application found; prior `~/.var/app/io.github.fabx_dev.Apricus` data still exist and must be retained |
| Apricus user service/autostart | PASS (inventory) | No matching systemd user unit or Apricus autostart entry found; no changes made |
| Host extension | PASS (inventory only) | UUID `apricus-integration@fabx-dev`, ACTIVE, metadata version 1, `CODE_REVISION=4`, user installation; all three installed files match the M1 worktree's unchanged extension files |
| DING | PASS (enabled-list only) | `ding@rastersoft.com` enabled; click/drag/stacking behavior is **NOT TESTED** |
| Bridge runtime | NOT TESTED | No Apricus GUI/bridge well-known name observed during inventory; do not infer a failure or launch a GUI just to populate status |
| Independent native Python runtime | NOT TESTED (dependency blocker) | System Python finds `gi`, `vlc`, `pydbus`, `setproctitle`, `requests`, but **not `yt_dlp`**. No borrowing of Agent-A's environment or installation was attempted |

Process IDs are observations at inventory time, not future kill targets. Recheck
identity, confinement and descendants immediately before any authorized shutdown.
The absence of SNAP variables in `/proc/.../environ` is not evidence of native
execution: process-title rewriting can obscure environment bytes; AppArmor,
cgroup and mapped runtime paths were used instead.

### Existing backups: availability and limits

Evidence directory (read only): `~/apricus-bridge-validation-20261024/`.

- **PASS:** `extension-original/` matches the recorded
  `final-original-backup-inventory.json` in file hashes, ownership, modes and
  mtime. It contains the **historical** extension, NOT the current host revision.
  Its existing `restore-extension.py` was only read, not executed. It must not
  be used blindly to roll back this test: it restores an older extension and
  can remove the bridge helper. No extension replacement is planned for M1.
- **AVAILABLE, historical:** `extension-before-final-snap-bridge/`,
  `extension-before-resuming-tests/`, `snap-config-before-video/` (valid V5 JSON,
  MODE_NULL), `flatpak-data-before-regression/` (valid V5 configs). These are not
  a substitute for a fresh snapshot of the current session/profile.
- **PASS, readable JSON:** native `~/.config/apricus/config.json` is V5/MODE_NULL;
  its `.bak` is **V4**, not a current-profile rollback. Current Snap x3 config
  and `.bak` are V5/MODE_VIDEO. None has the M1 `is_stopped` key; none was loaded
  through Apricus or migrated during inventory.
- **AVAILABLE, not restored/extracted:** `snap saved` lists retained automatic
  snapshot sets **1–8** for Apricus. No `snap restore`, extraction or sudo action
  was attempted; current revision/profile still needs a fresh backup before
  testing.
- **PASS, retained artifact checksums:**
  - `artifacts/apricus_2.0.7_static-9cb6ea5_amd64.snap`, 123,387,904 bytes:
    `<artifact-sha256>`.
  - `artifacts/apricus_2.0.7_bridge-97e3684_amd64.snap`, 123,387,904 bytes:
    `<artifact-sha256>`.
  These permit later recovery preparation, not authorization to replace x3.

### Proposed reversible procedure — NOT EXECUTED

1. **Obtain approval first** for an independent M1 Python environment (only in
   the dedicated worktree, no global installation/sudo), temporary shutdown of
   the current Snap wallpaper, and a source-run M1 test on the real session.
   Alternatively the user can supply a ready interpreter independent of Agent A.
   The system's missing `yt_dlp` currently prevents a clean source launch.
2. After approval, create a timestamped <private-evidence-file> directory **inside the
   M1 worktree** (untracked and excluded from commits). Record hashes/metadata of
   the current host extension, native/Snap configs and currently referenced
   static images; save exact light/dark background and screensaver GSettings
   values and the enabled extension list. Preserve the current extension, not
   just the historical backup. Do not change autostart or enabled extensions.
3. Ask the user to quit the current Snap using **Quit Apricus** (not merely
   closing its GUI), or obtain explicit approval to issue its existing IPC quit
   command. Verify that only the identified Snap instance and its descendants
   stop. Never kill by name or overlap two wallpaper instances in the host
   session. Abort if new/unknown Apricus processes appear.
4. Prepare a separate M1 config/cache profile copied from the approved test
   selection, without editing the native or Snap profiles. Use the dedicated
   source path and interpreter explicitly, never the ambiguous PATH launcher.
   Before launch, audit the effective write paths. Keep access to the host
   extension's existing data directory for detection; do not blindly override
   XDG_DATA_HOME and accidentally hide the installed extension. Disable neither
   extension nor screensaver. Live background GSettings writes inherent in
   wallpaper playback require the session-test authorization in step 1.
5. Run the user's phase-2 matrix with the **unchanged current host extension**:
   video, STOP/static cache/fallback, RESUME/new adoption, playlist, controlled
   stream/web, stopped selection changes, duplicate commands, server restart and
   persistent STOP. DING click/drag, visible first frame, Overview and workspace
   transitions require user observation/confirmation (or separately authorized
   capture). A running process/playing flag is not proof of visual success.
   Extension disable/enable is a separate optional authorization, not implicit
   in this procedure. No logout/login is initially expected since installed
   extension files already match; if one becomes necessary, stop and ask.
6. Measure in the real session: warm up for at least 10 seconds, then observe
   PLAY/PAUSE/STOP/RESUME over **60-second CPU windows**, report RSS/PSS and
   process/thread counts, player/server separately and Shell as context. Repeat
   transition latency samples and check the owned process tree for VLC/FFmpeg
   leftovers. Visible-first-frame latency is reported only if directly
   observable with timestamps; otherwise mark NOT MEASURED, not the playing
   property timestamp. Keep GNOME observations separate from Xvfb results.
7. Close **only the M1 instance** and verify its descendants have exited.
   Restore the exact saved background/screensaver values after pending M1 jobs
   are gone; preserve all existing installations, extension bytes and original
   profiles. With authorization, relaunch the original Snap explicitly with
   `/snap/bin/apricus -b` and verify the previous wallpaper/state. Compare hashes
   and enabled-state inventory. Retain evidence; do not delete or replace any
   existing installation or historical backup.
8. On a reproducible **M1-only** bug: record FAIL, add a regression test and
   make a local fix on this branch. Do not change the bridge, installed
   extension, Agent-A repositories or PR #1 to make a test pass.

### Current real-session acceptance status

| Test group | Status |
|---|---|
| Phase 1 inventory / source / host extension / historical backup verification | PASS, with runtime-dependency blocker above |
| Video STOP/RESUME, static fallback and new GNOME adoption | NOT TESTED — awaiting authorization |
| DING click/drag, Overview, workspaces | NOT TESTED — awaiting authorization and visual confirmation |
| Playlist, remote stream, web in GNOME | NOT TESTED — awaiting authorization |
| Duplicate operations, stopped selection, persistent STOP after restart in GNOME | NOT TESTED — awaiting authorization |
| Bridge non-regression on the real strict Snap | NOT TESTED — existing x3 is the integration base, not M1 |
| Real-session CPU/memory/latency/orphan measurements | NOT TESTED — Xvfb figures above are not substituted |
| Extension disable/re-enable | NOT TESTED — not authorized or currently necessary |
| M1 Snap strict / Flatpak packages | NOT TESTED — no experimental installation authorized |

### Subsequent packaging gate

Do not reuse the PR #1 artifact as evidence of M1: the retained x3 package is
based on `9cb6ea5`, whereas M1 is local commit `84bd123`. A remote test workflow
cannot build an unpublished local commit; with push forbidden, no workflow was
dispatched or modified. Later options require separate approval: a local/VM
test-only build, or explicit authorization to make the M1 ref available and run
an artifact-only workflow pinned to that SHA (no Store credentials/publication).
Snap/Flatpak test artifacts must have separate names/evidence and must not
replace the user's existing installation or retained Flatpak data without
explicit approval. Any sudo, package install/removal or logout remains a user
intervention checkpoint.

**Checkpoint: stopped after phase 1. No real-session M1 result or distribution
validation is claimed.**

## Authorized phase 2 — isolation preflight stopped ([validation date omitted], [event time omitted])

The user authorized an independent venv/dependencies, fresh backups, controlled
Snap shutdown, source-run M1, normal temporary wallpaper changes, measurements
and restoration. This attempt stopped **before Snap shutdown** under the user's
explicit condition: if full isolation is not supported, describe the problem
before proceeding.

### Reproduced preflight blocker (not a demonstrated M1 playback bug)

The proposed standard source launch isolates Apricus using `XDG_CONFIG_HOME`
(`commons.py`), but GNOME's GSettings/dconf client also uses that directory
(`GnomeWallpaperBackend._settings()` uses normal `Gio.Settings`). Two read-only
commands on the same real session produced different background values:

| Read-only probe | Observation |
|---|---|
| Normal environment: `gsettings get org.gnome.desktop.background picture-uri` | Current Snap x3 static image, basename `static-9720803a88bb9e9c.png` |
| Private `XDG_CONFIG_HOME`: same `gsettings get` | Default Ubuntu image `file:///usr/share/backgrounds/warty-final-ubuntu.png` |

Consequently a standard XDG-isolated native launch does not have the same
desktop-settings view as GNOME Shell. Its cached/static background validation
would be unreliable. No write probe was attempted: the actual dconf writer's
behavior must not be assumed from these read results. Isolation cannot be
declared complete by simply changing XDG variables and proceeding.

No symlink to the host dconf database, private-bus substitution, production-code
patch or path-override bootstrap was introduced to work around the blocker.
The existing native legacy-stacking fallback also contains a DING disable/enable
workaround; it must not be allowed to run if integration detection is lost,
because changes to other extensions are forbidden for this validation.

### State and disposition

| Item | Result |
|---|---|
| Worktree preflight | PASS: clean `feat/gnome-policy-stop`, HEAD `182150d`; implementation remains `84bd123` |
| Required OS/session | PASS: Ubuntu 26.04.1 / GNOME 50.1 / Wayland |
| Standard XDG profile and real desktop-settings coherence | FAIL (preflight): read-only mismatch above |
| Independent venv / dependency installation | NOT TESTED: not created/installed after the isolation blocker |
| Fresh full backup and executable restoration procedure | NOT TESTED: preparation did not proceed beyond the read-only isolation check; historical backups remain as inventoried above |
| Snap shutdown / M1 source startup | NOT TESTED: current Snap never stopped, M1 never launched |
| Video/playlist/stream/web, static/fallback, adoption and STOP persistence | NOT TESTED on the real session |
| Visible frame, DING click/drag, Overview/workspaces | NOT TESTED; no desktop-control tool is available in this conversation, so manual visual confirmation would also be needed |
| CPU windows of at least 60 seconds, memory and real transition latency | NOT TESTED; no Xvfb result substituted |
| New M1 orphan processes | None created: no M1 runtime was started; not a real-session orphan regression test |
| Restoration | No restoration/restart necessary: the existing Snap/session were left running, no settings or packages changed |

Observed baseline Snap processes: server [process ID omitted], forkserver [process ID omitted], player
[process ID omitted], resource tracker [process ID omitted]; a pre-existing zombie child [process ID omitted] was already
present before any M1 launch and was not modified or attributed to M1.
The only new local directory is the ignored <private-evidence-file> directory
`<private-evidence-dir>/`; no source, installed extension, Snap profile,
AppArmor rule, PoC Snap or other extension was changed. Agent-A repositories
and the roadmap worktree were not accessed.

### Proposed next decision (not executed)

Approve a narrowly scoped **test launcher**, not a playback implementation
change, that redirects only Apricus config/autostart/legacy-import paths in
every server, forkserver/spawn child and GUI while keeping normal host GNOME
settings access. Cache/data remain private. Its effective paths and unchanged
host settings must be verified before Snap shutdown. No linking/copying the
host dconf database into the private profile. If this needs a production path
override rather than test instrumentation, request that separate scope first.

Only after that method is agreed can fresh backup/restoration preparation,
isolated dependency setup and the already-authorized runtime test sequence
resume. No bug fix, package validation, push, PR, merge, tag or publication was
performed in this attempt.

## Test-only isolation adapter — new preflight ([validation date omitted])

**PASS for preparation/preflight only. No live M1 test has started.** All
production files under `src/`, installed extension files, packaging, bridge and
integration-branch code remain unchanged. The playback implementation is still
`84bd123`; the adapters and evidence described here are separate test tooling.

### Method and safety gates

New opt-in files: `tests/manual/m1_isolation/{launcher.py,sitecustomize.py,
m1_bootstrap.py,m1_probe.py,landlock_guard.py}`; fail-closed unit tests in
`tests/test_m1_isolation.py`. See the adjacent README for usage and boundaries.

An independent `.venv` was created in this worktree with system GI access and
the locked Python runtime/test dependencies (`uv sync --frozen`); downloads/cache
are under `<private-evidence-dir>/`. Nothing was installed globally and no
Agent-A environment was used. No sudo or package-manager operation was used.

The parent launcher does not alter its host environment. Only probe subprocesses
receive private XDG_CACHE_HOME, XDG_DATA_HOME, temp, shader and Deno cache roots.
**HOME, XDG_CONFIG_HOME, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS and display
information are preserved**, including whether a variable was originally unset.
The real dconf/GSettings backend/profile is retained; no copy, symlink, private
bus or memory backend is substituted to obtain matching values.

`sitecustomize` patches Apricus's **in-memory application path constants** before
dependent modules import aliases. The hook runs in fresh Python interpreters;
forked workers inherit the patched state. All Python probes use this worktree's
venv and this worktree's source, not a PATH launcher or installed Apricus.
Bootstrap failures exit with code 78 instead of continuing unisolated.

On this machine, Landlock ABI **8** is available. An unprivileged process-local
write boundary allows only the private profile/evidence roots, plus limited
device I/O (`/dev/null`, existing `/dev/dri` and `/dev/snd`). It does not allow
persistent writes to host HOME/config/cache/runtime, global `/tmp` or `/dev/shm`.
Fork/exec children inherit it. AppArmor, user/system services and the parent
process's policy are not changed. Unexpected native filesystem writes fail
rather than silently escaping to stable profiles.

In this phase all Python GSettings mutations, extension toggles (including the
legacy DING workaround), systemd operations, external application launches and
native file chooser actions are rejected. A harmless local autostart file was
created and removed **inside the test profile** to exercise the real path; it is
not in the host autostart search directory. No portal/systemd launcher was
installed. All Apricus server/player/GUI constructors are blocked; the launcher
has **no live-runtime subcommand**. There is no hidden call to `main()`.

### Audited path inventory

Let `R` be the fresh `build/m1p-*` evidence directory and `P=R/p`.

| Resource / production users | Test path / handling |
|---|---|
| `commons` / `utils` config, backups, lock, atomic temp | `P/config/apricus/config.json`, `.bak`, `.lock`, `.tmp` |
| Legacy Hidamari import | `P/legacy/hidamari/config.json`; real legacy profile is not imported |
| Static frames and stopped fallback (`video_player`, `web_player`, `stop_support`, GUI hero) | `P/config/apricus/static-*.png`, unique temporary PNGs, `stopped-background.png`; aliases verified after role-specific imports |
| FFprobe metadata (`mediacache`) | `P/config/apricus/video-info.json` and its atomic temp |
| Playlist, queue position/shuffle, volume/mute, stream/web history and favorites | Same private config.json; no separate host database |
| Default library / preseeded media | `P/media`; no automatic use of the user's Hidamari library |
| Native GnomeDesktop thumbnail factory | `P/cache/thumbnails/...`; actual native save+lookup performed |
| GUI stream thumbnail images/index and favicons | `P/cache/apricus/stream-thumbs/`, `P/cache/apricus/favicons/`; index write and path checks performed |
| App yt-dlp metadata | `P/cache/apricus/ytdlp-meta-cache.json`; synthetic metadata write, no network extraction |
| yt-dlp internal cache | `P/cache/yt-dlp`; effective cache root read from a real YoutubeDL object without extraction |
| Deno, Mesa/NVIDIA shader caches, SQLite temp | `P/cache/deno`, `P/cache/mesa`, `P/cache/nvidia`, `P/t` via child-only environment |
| Python/tempfile/GLib and native helper temp | `P/t`; actual temporary-file and multiprocessing socket paths exercised |
| VLC user configuration | `P/config/vlc/vlcrc`, via test-only application option-builder adapter adding public `--config`; actual CLI help verifies support; real vlc.Instance class unchanged |
| WebKit local storage/cookies/cache | Explicit WebsiteDataManager: `P/data/webkit`, `P/cache/webkit`; actual manager getters checked; no WebView/page created |
| XDG desktop autostart | `P/autostart/io.github.fabx_dev.Apricus.desktop`; isolated create/remove verified |
| systemd unit path | `P/systemd/user`; setup and `systemctl` calls blocked, no registration |
| Host install ZIP/script and verification receipt | `P/exports`; helper sources read from this worktree; explicit outside destinations denied by filesystem policy; no installer executed |
| Application Unix IPC fallback | `P/ipc`; XDG_RUNTIME_DIR itself stays real; no Snap socket or native service was published |
| Python bytecode, diagnostic logs and evidence | Bytecode disabled in probes; logs/JSON under `R/evidence`; regular inherited write FDs outside private roots are rejected |
| External folder/browser/desktop launch and chooser recent-state | Blocked test actions; no global desktop launcher or chooser state installed |

Paths that remain real are intentional: session bus and display sockets, GNOME
settings and schema sources, the loaded host extension (read only), system
libraries/devices and explicitly selected media (read only). No persistent
Apricus file is intentionally shared with native/Snap/Flatpak stable profiles.

### Recorded preflight results

Final evidence root: **`build/m1p-ayadto2e/`** (mode 0700, local/ignored).
`marker.json` records the source HEAD used (`de1bd8e`) and adapter file hashes.
`host-before.json`, `host-after.json`, `report.json`, `<private-evidence-file>`,
per-role JSON and per-interpreter bootstrap records are retained there.

| Check | Result |
|---|---|
| GNOME background and all 11 meaningful keys | PASS: host CLI and real Gio.Settings agree in every probe, including light/dark URI, colors/options, screensaver, enabled extensions and color scheme; all keys available |
| Host environment and display/session context | PASS: all 13 protected variables identical in launcher/probe/children; GLib config directory remains the real host directory |
| Same isolated application paths | PASS: imported aliases in server, GTK3 video/web and GTK4 GUI contexts agree |
| Host extension discovery | PASS: actual `integration_status()` returns ACTIVE in every context despite private XDG_DATA_HOME; no extension toggle |
| Actual child propagation | PASS: 4 roles under **spawn**, 4 under **forkserver**, controller, thread worker, exec worker = **11 contexts**; 8 fresh-interpreter bootstrap logs include tracker/forkserver; fork children report their inherited bootstrap origin |
| Effective native/cache writes | PASS: ConfigUtil, isolated desktop autostart, fallback PNG, yt-dlp cache, stream index, native thumbnail save/lookup; WebKit manager directories and public VLC config option verified |
| Alternative-path writes | PASS: controlled outside-root sentinel cannot be overwritten through Python, native C open/truncate, a native shell exec, symlink or cross-boundary hardlink; **55 denial checks** across 11 contexts, sentinel intact |
| Stable installations/profiles/extension | PASS: hashes plus mode/owner/mtime of **27 protected paths** unchanged within the measured preflight window |
| Production sources | PASS: all **25 Python source files** byte-identical before/after; no source edits |
| Snap and application bus ownership | PASS within final probe window: same strict Snap processes/start identities and same relevant well-known name ownership; no M1 service registered |
| Global GNOME changes | PASS: before/after significant values identical; mutating Python APIs/commands rejected before execution |
| Preflight helper cleanup | PASS: multiprocessing children joined, thread/exec worker finished, tracker/forkserver/bootstrap PIDs gone |
| Adapter unit/lint/format tests | PASS: **5 fail-closed tests**; Ruff lint and format checks over `src`/`tests` passed |
| Live wallpaper, GUI behavior, decoding, DING click/drag, performance | **NOT TESTED** in this preparation |

Probe-only method-name/import-order errors were fixed in test tooling; failed
run/evidence directories were kept and host-state checks remained unchanged.
The VLC adapter was reviewed to keep the actual binding class intact and adapt
only option construction. No production bug was diagnosed or fixed here.

Process inventory note: the initial Snap server/player were [process IDs omitted];
the final preflight observed server/player **[process IDs omitted]** and related strict
Snap children [process IDs omitted]. No command to stop/start Snap was issued by
this preparation. The cause of the intervening inventory difference is not
asserted. The PASS statement is limited to the final controlled before/after
window, not a claim that the entire session was frozen throughout preparation.
Re-inventory is mandatory before any subsequent approved shutdown.

### Residual limits and next reversible phase

- This is **not a full security sandbox**: reads/network/session IPC remain
  available. Landlock does not mediate all metadata operations or pre-existing
  write FDs; inherited regular write FDs are checked, Python metadata mutations
  outside private roots are rejected, and stable hashes/metadata are compared.
  Arbitrary malicious native code or writes requested from another service are
  not covered by a blanket guarantee. The reviewed preflight constructs no
  applications and uses read-only GNOME operations.
- Known persistent Apricus paths are isolated. If an untested native helper
  later requires a different write path, it will fail closed: stop and report
  the exact path, rather than adding a broad HOME/runtime/tmp exception.
- WebKit browsing subprocesses, GPU/audio access, native decoder allocation and
  normal Gtk3/XWayland window mapping remain untested. Landlock/no_new_privs may
  affect helper sandbox startup; current manager/config checks are **not** a
  claim of rendering compatibility. No sandbox is disabled to make them pass.
- Real GNOME settings/IPC are deliberately not redirected. C/portal-generated
  preferences are not generically intercepted by Python method guards; file
  choosers and external-launch actions are excluded. A later live mode needs
  separately reviewed background-only write permissions and restoration, not
  weaker isolation or a fake dconf database.
- Private XDG_DATA_HOME also isolates user desktop associations/recent-state;
  it is not equivalent to the installed application's data view. External app
  launches/choosers are excluded rather than installing global desktop files.
  Account for this when reviewing later GUI/Overview observations. Host extension
  discovery stays real via HOME and the session bus, as verified above.
- This preflight does not replace real-session or confined-package acceptance,
  nor does it provide new CPU/latency measurements. Instrumentation overhead
  must be identified if it is retained for future measurements.

On new authorization: recheck current Snap revision/PIDs, preserve current
configs/selection/volume/mute/static/autostart and exact GNOME settings, prepare
an independently usable restore script **before any shutdown**, review/enable
the adapter's live-mode entrypoint and only the intended background writes,
then quit the identified Snap via its existing control API. Verify all its
children are gone before starting M1. Keep private profiles and the unchanged
host extension; preseed approved media instead of using global file choosers.
After testing, stop/reap only M1, restore exact host background/settings and
unchanged original profile, then explicitly relaunch the original Snap and
verify DING/bridge with the user. No package replacement, extension toggle,
logout/login or privilege escalation is implicit in that authorization.

**Stopped after successful preflight. Awaiting authorization for real validation;
the Snap was not stopped by this tooling and M1 was not started.**

## Authorized real-session attempt — interrupted and restored ([validation date omitted])

The user clarified that the earlier Snap PID change resulted from their manual
reset, **not a spontaneous restart**. No investigation or autostart/service
modification is needed. They authorized the real-session phase with fresh
backup, isolated data and final Snap restoration.

### Preparation and observations

- Repeated preflight: PASS, `build/m1p-rfvzu880/`. Fresh independently verified
  backup: `backup/manifest.json`, 13 files covering Snap configuration/cache,
  native configuration and the installed extension. Full selection/playlist,
  volume/mute/static/autostart inventory and 11 GNOME values were captured.
- `tests/manual/m1_isolation/live_session.py` provides explicit preparation,
  supervision, watchdog and standalone system-Python restoration. The copied
  `build/m1p-rfvzu880/restore.py` and all backup file hashes were verified before
  shutdown. No production source changed. Private red/blue video and HTML
  fixtures were prepared but not used for runtime matrix tests.
- The existing Snap server [process ID omitted] was closed using its authenticated Unix IPC
  peer. The supervisor checked prior live descendants, strict Snap processes,
  Apricus process names and server bus ownership before starting M1. No two
  wallpaper-controlling instances were intentionally run together.
- M1 server [process ID omitted]/player [process ID omitted] started from this worktree with private data,
  real bus/display/GSettings, and the existing GTK3 X11 backend selection.
  Native extension adoption was logged and VLC reported playing. **Neither
  observation proves a visible decoded frame.**
- Native dconf reported `unable to create file '/run/user/1000/dconf/user':
  Permission denied; dconf will not work properly`. The write guard intentionally
  did not permit that runtime path. The read-only preflight did not exercise
  this native initialization/write requirement: its PASS must not be read as
  complete runtime-isolation validation.
- Additional log evidence includes H.264 `get_buffer() failed`,
  `thread_get_buffer() failed`, `no frame!`, and denied Shell Introspect GetWindows.
  Root causes of decoding failure are **not established**; no claim of an M1
  regression or a visible wallpaper is justified.
- On observing the errors, validation was interrupted. No filesystem allowance
  was expanded; no production fix, extension change, autostart change or package
  operation was performed. The supervisor/watchdog completed and exited.

### Results

| Check | Result |
|---|---|
| Fresh backup and independent restoration readiness | PASS |
| Prior Snap shutdown / no concurrent Apricus before M1 start | PASS |
| Native live initialization with current isolation | **FAIL**: dconf runtime access denied; decoder errors also present |
| VLC playing-state observation | PASS as a state observation only, not frame visibility |
| Visible first frame and resume-to-visible latency | NOT TESTED |
| Functional STOP/RESUME/PAUSE/QUIT, persistence and 50-cycle matrix on real GNOME | NOT TESTED; validation interrupted before the matrix |
| CPU windows >=60s, RSS/PSS, process/thread and latency measurements | NOT TESTED; no new valid performance result |
| DING click/drag, Overview/workspaces, GUI survival | NOT TESTED visually |
| M1 shutdown and cleanup | PASS: all five recorded M1 identities absent after restoration |
| Exact GNOME settings and original configuration restored | PASS: all 11 GNOME values match backup; Snap config semantically identical |
| Original Snap restart | PASS: authenticated runtime PID [process ID omitted], MODE_VIDEO, playing=true, volume=50, mute=false, static=true |
| Installed extension/native protected files and backup integrity | PASS: hashes unchanged, all 13 backup files verified; DING/integration remain ACTIVE |
| Bridge ownership | Same baseline: separate bridge bus name unowned before and after; not a new bridge functionality PASS |
| Snap/Flatpak packaging acceptance | NOT TESTED |

Recovery completed with no recorded M1 PID left. Snap **2.0.7 x3** is restored;
no need to execute the already-completed idempotent restoration again. The
backup/evidence remain local in `build/m1p-rfvzu880/`, including `state.json`,
`ready.json`, `<private-evidence-file>` and `snap-restored.log`.

**Blocked and stopped.** A revised, narrowly reviewed handling of native dconf
runtime access is required before another attempt; no copying/linking dconf,
global environment replacement or broad runtime write exception is assumed.
The decoder evidence also requires diagnosis before a valid visual/performance
claim. Ask for approval of the revised isolation approach before retrying.
No push, PR #1 change, merge, tag, release or publication occurred.

## Restricted adapter diagnosis — no new GNOME runtime ([validation date omitted])

Scope: user authorized diagnosis/minimal test-tool correction only, explicitly
requiring a stop before granting real dconf write rights. The running Snap was
not stopped, M1 was not launched on the desktop, and neither production code,
installed extension, AppArmor, services nor any other worktree was changed.

### Exact native dconf operation and proposed minimum

New reproducible tool: `tests/manual/m1_isolation/diagnose_dconf.py`.
Evidence: `<private-evidence-dir>/{host.trace,guarded.trace,report.json}`.
`strace -f -yy` on system Python/Gio, using identical protected environment and
only a GSettings read/change subscription, establishes:

```text
host: openat(AT_FDCWD, "/run/user/1000/dconf/user", O_RDWR|O_CREAT, 0600) = fd
host: pwrite64(fd, "\0", 1, 1) = 1
host: mmap(NULL, 1, PROT_READ, MAP_SHARED, fd, 0) = address
guarded: openat(...same path..., O_RDWR|O_CREAT, 0600) = -1 EACCES
```

The only intentional control difference is applying the unchanged Landlock
guard. This reproduces the denial without GTK, VLC, wallpaper or M1 startup.
It is **not** a denied read-only mmap: the writable open is denied first.
Even a read API needs a native write: dconf writes NUL to the second byte of its
runtime shared-memory marker to ensure allocation before mapping the first byte
for change/invalidation tracking. No ftruncate or writable mapping of that
marker was observed in this reader initialization. The already existing marker
is 2 bytes; the actual configuration database is a different, 10460-byte file
at `<private-path>`, opened O_RDONLY|O_CLOEXEC and mapped
PROT_READ|MAP_PRIVATE. The guarded reader can still return cached/current values
while emitting fatal-quality diagnostics about broken native initialization.

The resolved paths equal the paths above; marker/database and their immediate
parents are not symlinks. The report records inode/device/size for both. The
fallback lookup `/run/dconf/user/1000` returns ENOENT; it is not an alias to the
runtime marker. No database copy, replacement or symlink was created. The normal
host control's dconf library itself performs the demonstrated marker pwrite;
no application setting was changed and protected state before/after matches.
Installed libdconf1 is **0.49.0-4**. The upstream implementation corroborates
the observed allocation sequence and explains avoiding SIGBUS:
<https://raw.githubusercontent.com/GNOME/dconf/master/shm/dconf-shm.c> (source
reference only; syscall evidence comes from the installed host library).

**No exception applied, no new write permission granted.** The narrow candidate
for later approval is `LANDLOCK_ACCESS_FS_WRITE_FILE` (**bit 1 only**) on the
already-existing, verified regular file `/run/user/1000/dconf/user`, through a
file-specific PATH_BENEATH rule. Read rights are already unhandled/allowed by
the existing policy. The trace does not justify granting TRUNCATE, REMOVE_FILE,
MAKE_REG, directory-wide writes, config-tree writes or permission to create a
missing marker. O_CREAT on this existing file does not itself demonstrate a
need for directory creation rights. Missing/replaced marker/inode must fail
closed and be rechecked, not broadened to its parent.

This rule is a **proposal, not verified sufficient under the actual guard**:
testing it would grant the write permission the user explicitly reserved for
approval. Landlock cannot constrain WRITE_FILE to offset 1 or a NUL payload;
the file-specific exception would permit other content writes to that inode.
If dconf replaces/unlinks the marker during a later session write, the original
inode rule may no longer cover it. No promise of replacement safety or future
writer behavior is made from these reader traces.

### Corrected preflight acceptance

Minimal test-adapter correction: `launcher.py` now rejects `dconf-CRITICAL` or
`dconf will not work properly` in captured native probe logs. Three new
regression cases prevent returning PASS solely because GSettings values match.
No Landlock policy was changed; there is deliberately no dconf allowlist entry.

Final preflight evidence: **`build/m1p-kshjhzay/`**, exit 1, **FAIL** for
`native_dconf_initialization_clean`. All **12 existing checks PASS**: 11 contexts
complete under spawn/forkserver/thread/exec, all 11 host values and 13 protected
variables agree, application path/cache aliases stay private, all 55 escape
write probes are denied, stable profiles/source hashes/Snap identities/bus
owners remain unchanged, and probe helpers exit. This corrects the earlier
overly permissive preflight verdict; it is not a successful runtime preflight.
HOME, XDG_CONFIG_HOME, XDG_RUNTIME_DIR and DCONF_PROFILE were not changed.

### Separate H.264 findings

Original complete available log remains at
`build/m1p-rfvzu880/evidence/live-server.log` (337 lines). It contains the FFmpeg
H.264 buffer errors but **does not contain verbose VLC module-selection or vout
allocation messages**. Missing original verbose detail cannot be acquired
retroactively without another reproduction; this phase did not restart M1.
The original effective decoder plugin/vout therefore cannot be conclusively
named from that log alone. `[h264 @ ...]` identifies the FFmpeg codec context,
not the complete selected renderer/hardware path.

Read-only ffprobe of the original Hawaii file reports H.264 High, level 4.0,
1920x1080, yuv420p, 30000/1001 fps. The native VLC avcodec plugin exists and its
`ldd` output has no missing dependency. Native packages: VLC/libvlc **3.0.23-1**,
libavcodec62 **8.0.1-3ubuntu2**. The running Snap remains server [process ID omitted] / player
[process ID omitted]. Read-only `/proc/<pid>/maps` shows its mounted avcodec plugin and
libavcodec **60.31.102**, plus its own LibVLC and GL/X11-related plugins.
Mapped libraries prove availability/loading, **not which vout is actively used**.
The two environments do not use the same FFmpeg ABI/build. Supporting inventory:
`<private-evidence-dir>/{media.json,native-avcodec-ldd.txt,snap-mapped-plugins.json}`.

New `diagnose_codec.py` acquired **complete `-vvv` messages and syscall traces**
for two short native VLC-only probes with isolated config/cache/data/temp,
dummy interface and dummy video output, no Apricus and no wallpaper window:
`<private-evidence-dir>/{host,guarded}/vlc.stderr` and the matching `.trace` files.
Both logs explicitly select video decoder **avcodec**, **Lavc62.11.100**, software
pixel format **yuv420p** after hardware-format probing, and output **vdummy**;
both report **Received first picture**, exit 0, no `get_buffer() failed`, no
`thread_get_buffer() failed` or `no frame!`, and no EACCES in the traced syscalls.
Normal hardware capability/context probing can contact the existing display;
these are not embedded-renderer or live-desktop acceptance tests.

The original Apricus config was renderer `auto`, audio volume 50, mute false,
with `--no-disable-screensaver` and pulse output. The test adapter only appended
a private public `--config` argument to the unchanged production option builder.
The diagnostic dummy/no-audio/3-second settings are deliberately different and
are **not production fixes**. Decoder availability and basic decoding under the
guard are established; a generic missing-H.264-plugin hypothesis is unsupported.
The original embedded allocation/render/hardware failure is **not reproduced or
resolved**. Neither dconf denial nor a particular renderer is declared its cause.
No LibVLC binding/library/renderer options or production code were modified.

### Disposition

Both diagnostic reports show host settings, stable file metadata/hashes, Snap
process identities, bus owners and production sources unchanged. The final
preflight independently confirms the same. Adapter tests: **8 passed**; Ruff
lint/format checks pass. Snap remains running throughout; no recovery/restart
was needed in this phase. No GNOME extension or autostart/service modification,
sudo, push, PR #1 change, merge, tag, release or publication.

**Stopped before granting real dconf writes, as instructed.** Another positive
preflight cannot honestly be reported under the present rights. Further work
requires separate approval for the precisely scoped runtime-marker write rule;
another real GNOME M1 run remains unauthorized.

## Approved file-only dconf exception — positive preflight ([validation date omitted])

The user authorized testing WRITE_FILE on the **already-existing** runtime marker
only, without another desktop M1 run or Snap shutdown. This section supersedes
the previous blocked preflight disposition, not the earlier failed live result.

### Rule and resource validation

Only test tooling changed. `landlock_guard.restrict_writes(..., dconf=identity)`
adds a file-specific PATH_BENEATH rule with allowed mask **2**,
`LANDLOCK_ACCESS_FS_WRITE_FILE`. The existing read policy is unchanged.
**No TRUNCATE, MAKE_REG, REMOVE_FILE, dconf-directory or generic runtime rights**
are added. AppArmor and the dconf implementation/profile are unchanged.

Before the rule is applied, the adapter verifies the canonical path
`/run/user/1000/dconf/user`, rejects symlinks including ancestors, requires a
regular existing file, UID/GID 1000, mode **0600**, and link count 1. It opens an
O_PATH|O_NOFOLLOW|O_CLOEXEC descriptor, checks its device/inode and adds the rule
to that FD's resource, not its parent. Expected identity is inherited by every
new interpreter and rechecked in forked roles. Missing or unsafe resources are
not created or repaired. The launcher checks identity/permissions every 50 ms
while the private probe process group runs and before/after it; replacement
causes interruption and re-evaluation, not automatic rebinding. This polling is
not an atomic guarantee against a transient malicious filesystem replacement.

Observed stable resource: device **89**, inode **1142**, UID/GID **1000/1000**,
mode **0600**, nlink **1**. No replacement/identity change occurred. WRITE_FILE
does **not** limit writes to a particular byte or payload: any content write to
this inode is possible; only the normal dconf client's demonstrated allocation
pwrite is needed here. It is a runtime notification marker, **not** the actual
`~/.config/dconf/user` settings database. The latter's hash and metadata are now
also explicitly included in the protected-file comparison.

### Syscall evidence and preflight

`<private-evidence-dir>/{host.trace,guarded.trace,report.json}` retains the repeat
probe, with the explicit `--allow-marker-write` switch. Under the approved guard:

```text
openat(..., "/run/user/1000/dconf/user", O_RDWR|O_CREAT, 0600) = fd
pwrite64(fd, "\0", 1, 1) = 1
mmap(NULL, 1, PROT_READ, MAP_SHARED, fd, 0) = address
```

No native dconf critical error remains. Existing-file O_CREAT succeeds without
granting creation rights on its parent. GSettings values, protected files,
Snap identities and session variables match the host control.

Final preflight: **PASS**, all **15 checks** true, evidence root
**`build/m1p-01ja42ur/`**. `marker.json` fingerprints the exact adapter used;
per-role JSON and logs are retained under `evidence/`. No server/player/GUI
instance was constructed; imports and safe fixture/config/cache writes only.

| Check | Result |
|---|---|
| Existing-marker identity and safe owner/mode/path | PASS, same in all 11 contexts |
| Native dconf initialization | PASS, no dconf critical diagnostics |
| 11 real GNOME values / 13 protected environment variables | PASS, host and child contexts agree |
| Host settings remain unchanged | PASS, values plus actual dconf DB hash/metadata unchanged |
| Private config/legacy/static/cache/thumbnail/stream/WebKit/VLC/temp paths | PASS, original alias checks retained |
| spawn/forkserver server/video/web/GUI imports; thread/exec workers | PASS, 11 contexts, helpers exit |
| Kernel write boundary | PASS, **99 denial checks**, 9 per context |
| Actual dconf marker ftruncate | DENIED in every context; requested existing size, so no destructive truncation even if a policy bug existed |
| Creation in dconf parent and outside profile | DENIED in every context |
| Actual stable Snap config write-open | DENIED in every context; no content modification attempted |
| Existing sentinel Python/native/shell/symlink/hardlink escapes | DENIED; original sentinel content preserved |
| Snap processes, app bus owners, installed extension/stable files | PASS, unchanged within the whole probe window |
| Production files | PASS, unchanged |

The rule never grants REMOVE_FILE; no attempt was made to unlink the real marker
as a destructive test. Same-size ftruncate checks the independently mediated
TRUNCATE permission without erasing the marker. Normal dconf initialization
writes its second byte and may update runtime-marker timestamps; that expected
activity is not presented as a byte-for-byte frozen runtime file.

Tests: **11 passed**, including identity-change and unexpected-runtime rejection,
native-error preflight rejection, and logging-helper lifetime. Ruff lint/format
checks pass. Python 3.14 emits an existing ctypes packed-structure deprecation
warning; the actual Linux ABI structure/rule succeeded in the syscall preflight.

### H.264 preparation, deliberately separate

No new decoding run was needed, no VLC option was changed, and the earlier
dummy-output diagnosis is not promoted to an embedded-renderer PASS. New dormant
`tests/manual/m1_isolation/vlc_diagnostics.py` prepares public LibVLC file logging
with explicit FILE* lifetime, and timestamped process-map/status/media-statistics
snapshots. It has **no automatic hook, VLC import, playback or option changes**.
Its lifecycle is tested with a fake Instance; actual LibVLC logging integration
is still pending a separately authorized test.

For that future adapter integration, attach immediately after the real Instance
is created and before media/player setup. Keep complete native logs and stderr:
the public logging API cannot recover Instance-construction messages, and FFmpeg
may emit directly to stderr. Collect mapped plugin versions, module-selection
messages and media statistics before the first error/teardown, and correlate
with a full subprocess-following syscall trace covering allocation/shared-memory
and file denials. Never substitute dummy output or disable hardware in the real
test to make it pass. Tracing/logging overhead must be separated from latency
measurements. No cause for the historical H.264 failure is asserted here.

### Final disposition

Snap remained running with unchanged recorded identities (server **[process ID omitted]**,
player **[process ID omitted]**, helpers **[process IDs omitted]**); no restart or restoration was
needed. Real session settings, original extension and stable Apricus profiles
remain intact. No sudo, service/autostart changes, production edits, other
worktree access, PR #1 change, push, merge, tag, release or publication.

**Stopped after the positive preflight. No new GNOME M1 runtime started.**
Any future marker replacement or new native denial remains a reason to stop,
not to grant directory permissions. A fresh inventory/backup and explicit
authorization are still required before a real-session validation retry.

## Authorized GNOME retry — smoke interrupted, Snap restored ([validation date omitted])

User authorized real-session validation from `ee723a1`, gated on a successful
smoke test before functional/performance tests. The approved Landlock adapter
remains test-only; production code and packaging dependencies are unchanged.

### Fresh preparation and controlled transition

New process inventory identified Snap **2.0.7 revision x3**, server **[process ID omitted]**,
tracker/forkserver **[process IDs omitted]**, player **[process ID omitted]**. Integration extension
and DING were ACTIVE. Existing `build/m1p-rfvzu880/` backup (13 files and its
standalone script) was verified, not overwritten. New private profile/evidence:
**`build/m1p-48pantki/`**. The new preflight passed all 15 checks before shutdown.

Fresh backup `backup/manifest.json` records the full configuration/playlist,
GNOME settings, exact Snap revision, process identities and autostart state.
Selected video: `Hawaii-Ocean-2K-AVC.mov`; two queued videos; volume **50**, mute
**false**, static **true**, video mode. All 13 backup files were independently
hashed and the copied `restore.py` verified with `/usr/bin/python3` before
shutdown. Native config/extension copies are protected evidence, never blindly
restored. Original autostart/services were not changed.

The explicit supervisor sent the existing Snap's authenticated normal IPC quit,
then checked original live descendants, strict Snap processes, Apricus process
names and server bus ownership before source launch. No competing wallpaper
instance was present when M1 started. It launched a private strace process
**[process ID omitted]**, source server **[process ID omitted]**, player **[process ID omitted]**, with the same private
config/cache/data/temp/IPC roots verified by preflight. A standalone watchdog
was armed before shutdown. GSettings stayed real; the approved marker inode
exception was checked before shutdown and throughout the attempt.

Test-only wiring enabled the prepared public LibVLC file logger after Instance
creation/before media playback, retained stderr and traced the full process
tree's allocation/file syscalls. It did not change renderer/hardware/audio
choices or replace `vlc.Instance`. The only existing option adaptation remained
the private public `--config` path. Instrumentation is not release performance.

### Immediate stop condition and VLC evidence

`failure.json` records **dconf marker identity/permissions changed**. Before the
attempt the marker was device **89**, inode **1142**. After restoration it is
device **89**, inode **1154**, same owner/group/mode. The runtime monitoring
detected a change during startup and immediately entered restoration. It did
not grant rights to the new inode or its parent. Dconf marker replacement is
consistent with notification invalidation after real GSettings writes, but the
external dconf writer's unlink/replacement is not captured by the child-tree
trace, so its precise initiating write is not claimed from these logs alone.

The original-inode reader initialization succeeded: `openat O_RDWR|O_CREAT`,
`pwrite64` at offset 1 and read-only shared mmap. No EACCES/EPERM appears in the
captured syscall trace. The current file-only rule is therefore sufficient for
initial reads, **not a live session that requires surviving marker replacement**.
No broader/dynamic exception was attempted. The earlier positive preflight
remains limited to its no-settings-mutation window.

Detailed native log:
`<private-evidence-file>` (241 lines), plus
`<private-evidence-file>`, `<private-evidence-file>`, and the before-media map/status
snapshot. Native log lines 108–113 identify **avcodec**, **Lavc62.11.100**, H.264.
Lines 138–176 show VAAPI probing, **gl** vout selection, `vaDeriveImage: operation
failed`, `video output creation failed`, `failed to create video output`.
Later lines show VDPAU probing/fallback, gl selection again and **Received first
picture** (line 231). These errors must not be hidden, but subsequent fallback
means they do not alone prove final decoding failure. The older
`get_buffer()/thread_get_buffer()/no frame!` sequence was not reproduced here.
There is no evidence of a missing avcodec plugin, and no permission-denial
syscall identified as the cause of the VAAPI output failure. Renderer/hardware
fallback diagnosis remains incomplete; no speculative VLC fix was made.

Server property reached `playing`; `ready.json` records that state, explicitly
not visibility. The player logged `first frame ... 2.05s after start`, triggered
by its first-time-changed event. **This is neither user-confirmed on-screen
visibility nor a validated visible-frame latency measurement.** The supervisor
interrupted before requesting a visual M1 confirmation. GNOME journal recorded
adoption of the `__main__.py` window into the background layer, followed by
adoption of the restored Snap's `apricus` window. The adoption observation is
not substituted for a visible video or interactive DING check.

### PASS / FAIL / NOT TESTED

| Phase / test | Result and scope |
|---|---|
| Fresh inventory, independent backup and old backup verification | PASS |
| Repeated isolated-path/real-session preflight | PASS, 15 checks before shutdown |
| Normal Snap quit / no competing instance before M1 | PASS |
| Source video player startup and VLC playing property | PASS as an internal-state observation only |
| Smoke acceptance overall | **FAIL / interrupted**: marker identity changed; native vout probe failures also captured |
| First M1 frame actually visible | **NOT TESTED**, no user/desktop visual evidence |
| STOP and player resource release attributable to STOP | NOT TESTED |
| M1 static wallpaper/fallback after STOP | NOT TESTED |
| RESUME and visible video return | NOT TESTED |
| New source window adopted by GNOME integration | PASS for initial adoption only; RESUME adoption NOT TESTED |
| DING clicks/drags during M1 / Overview | NOT TESTED |
| Playlist playback/advance and STOP during playlist | NOT TESTED |
| Wallpaper changes during STOP / repeated STOP and RESUME | NOT TESTED |
| Persistent STOP across M1 restart | NOT TESTED |
| Static enabled/disabled / missing cache | NOT TESTED |
| Controlled HTTP stream / web wallpaper | NOT TESTED |
| >=60s CPU windows, RSS/PSS/process/thread measurements | NOT TESTED: benchmarks were not started |
| STOP latency / resume-to-playing / visible-first-frame latency | NOT TESTED; no valid measurements from this interrupted run |
| M1 termination, including tracer and recorded helpers | PASS after restoration; all recorded identities absent |
| Restored Snap configuration and all 11 GNOME values | PASS, configuration semantically equal to fresh backup and values exact |
| Original Snap restarted and wallpaper visible | PASS, runtime playing=true and **user confirmed Hawaii visible and animated** |
| Restored desktop DING | PASS, **user confirmed icons, clicks and dragging work** |
| Installed extension / DING enabled/ACTIVE | PASS, protected extension hashes unchanged |
| Separate GNOME bridge service telemetry | NOT TESTED: bridge bus name unowned both before and after; extension adoption is separately observed |
| Backup integrity | PASS, all 13 fresh backup files/script verified; old backup retained intact |
| Snap/Flatpak M1 package validation | NOT TESTED; not started |

No measurement here is imported from Xvfb or presented as a GNOME performance
result. No restart/second runtime attempt was performed after the error.

### Cleanup instrumentation correction, not a production bug

During restoration, a **test-diagnostics bug** raised
`Media.get_stats() missing required positional argument: p_stats`. It interrupted
the normal player quit handler; identity-scoped supervisor termination then
removed the remaining processes. The independent restoration completed, and
all six recorded source/tracer/helper identities were verified absent.

After restoring the Snap, corrected the adapter helper to allocate a real
`vlc.MediaStats` and pass it to `get_stats`. Diagnostic snapshot/logging failures
can no longer prevent invoking original player cleanup. Added two regression
tests with fake media/player objects, requiring no playback. Also corrected the
supervisor READY text to report the server PID instead of the tracer PID.
No production M1 bug was established or changed. **13 adapter tests pass**;
Ruff lint/format pass (existing Python 3.14 packed-ctypes deprecation warning).
These fixes were not used for a second live run; their scope is unit-validated.
Test-tool wiring and regression fixes are committed locally as **`787f1e2`**;
the production M1 implementation remains unchanged.

### Complete restoration status and remaining blocker

Automatic supervisor restoration and watchdog both exited. Fresh backup/profile
and exact original GNOME values restored; authenticated Snap runtime server
**[process ID omitted]**, player **[process ID omitted]**, helpers **[process IDs omitted]**, video mode,
playing=true, volume=50, mute=false, static=true. No recorded M1 identity remained.
`restoration-verification.json` retains the technical results and user's explicit
positive visual confirmations for Hawaii and DING. The separate bridge name's
absence matches baseline, but is not represented as a successful bridge-state
query. No manual recovery is required. Standalone recovery remains available
at `/usr/bin/python3 .../build/m1p-48pantki/restore.py`, already completed.

**Stopped.** The file-only dconf exception cannot silently follow replacement
inodes; that constraint requires a separately reviewed approach before another
live retry. Detailed VLC evidence is retained without altering renderer or
hardware settings. No production code, installed extension, AppArmor, services,
autostart, Agent-A worktree or PR #1 changed. No sudo, logout/login, package
replacement, push, PR, merge, tag, release or publication occurred.

## Strategy change — prepare temporary Snap removal ([validation date omitted])

The user abandoned further Landlock adaptation/another VM and authorized
**preparation only**, requiring their intervention before sudo or actual
removal. Future M1 runs must use normal native config/cache and the real GNOME
session, with no Landlock/sitecustomize and no make install. **No Snap was
stopped/removed and no M1 runtime was launched in this preparation.** The old
automatic-Snap-reinstall supervisor is explicitly **not applicable** to this
strategy. At the end of future source tests, restore native settings/data and
ask whether to reinstall the old Snap or keep source M1 available; no automatic
Snap installation is authorized.

### New inventory, backup and package evidence

Current branch `feat/gnome-policy-stop`, preparation base `9b73d7c`.
Snap **apricus 2.0.7 x3** remains running, server **[process ID omitted]**, player **[process ID omitted]**,
tracker/forkserver **[process IDs omitted]**. `apricus-channel-poc 0.1 x1` is separately
installed and must remain untouched. Both host integration and DING are ACTIVE.
Authenticated read-only Snap IPC confirms playing=true, video mode, volume
**50**, mute **false**, static **true**. Selected wallpaper is the Hawaii AVC
video, with Kelp-Dark and LA-Downtown-Night in the saved two-item playlist.

New private recovery directory (0700):
**`<private-evidence-dir>`**. New standalone backup-only tooling lives in
`tests/manual/m1_native/`; no old adapter or production source was changed.

- `user-data.tar.gz`: full `~/snap/apricus` tree, including common/revision
  data/config/cache; native Apricus and legacy Hidamari configs, existing
  relevant caches/VLC data, and evidence-only host extension copy. All **131
  regular files** can be read to EOF from the archive. Original absent-path
  state, autostart/service absence and environment are recorded in manifest.
- `system-data-readable.tar.gz`: readable `/var/snap/apricus` state, **24 files**;
  no sudo needed. Its hash is recorded in `additional-evidence.json` and the
  supplemental integrity index.
- `manifest.json`, `runtime.json`, full relevant GSettings schema outputs and
  `dconf-dump.txt`: full config/playlist/state plus settings/connection/snapshot
  inventories. The full dconf dump is evidence only, not a blanket restore plan.
- `verify.py`: standalone system-Python verification of retained-file hashes
  and archive readability. `preparation.sha256` covers supplemental evidence.
  `procedure.md` contains an independent, GUI-free removal/recovery/reinstall
  procedure, with explicit user gates and **no automatic reinstall**.
- The two older runtime backup sets (`m1p-rfvzu880` and `m1p-48pantki`) remain
  untouched; both 13-file sets and copied restore-script hashes were verified.
  Their old automatic-restart behavior must not be used for the new strategy.

Retained candidate package copied from the previously preserved artifact:
`apricus_2.0.7_previous.snap`, **123387904 bytes**, SHA256:

```text
<artifact-sha256>
```

It was extracted **only into this new evidence directory**, not installed.
Content comparison against mounted `/snap/apricus/x3`: **8708 regular files**,
**6243 symlink targets**, **693 directories**, no mismatch. No symlink content
was followed as if it were an archive regular file. Results are recorded in
`package-content-comparison.json`.

**Exact installed compressed-blob checksum remains PENDING**: the installed
`/var/lib/snapd/snaps/apricus_x3.snap` is root-owned mode 0600. Its size and mounted
contents match the retained candidate, but this is not represented as a verified
byte-for-byte blob hash. No sudo or block-device permission bypass was attempted.
Before removal, ask the user to run `sudo sha256sum` on that installed file and
compare with the hash above. A mismatch is a blocker requiring a separately
approved copy of the actual installed blob. Same payload can be reinstalled
using the retained package; snapd may assign a new local xN revision, so the
procedure does not falsely guarantee reinstalling the local label `x3`.

### Native environment readiness, without starting M1

Existing independent `.venv` has all six required modules discoverable.
HOME remains `<private-path>`; XDG_CONFIG_HOME is unset (normal `~/.config`),
XDG_RUNTIME_DIR `<runtime-dir>`, session bus `<runtime-dir>/bus`, DISPLAY `<display>`,
WAYLAND_DISPLAY `<wayland-display>`, DCONF_PROFILE unset. Source config resolves to
**`~/.config/apricus/config.json`**, legacy to `~/.config/hidamari/config.json`.
These ordinary native paths are backed up, not redirected to an experimental
profile. User media/repositories are not copied or removed.

A normal Ubuntu Python startup loads `/usr/lib/python3.14/sitecustomize.py`
(Apport), not the old test hook. To comply with the literal no-sitecustomize
constraint, `inspect_environment.py` was executed with the venv Python **-S**,
adding only source, venv site-packages and system GI import paths. It confirmed
no sitecustomize/Landlock module, all dependency specs present and unchanged
real GNOME paths. Output retained as `native-environment.json`. This is an
import-only inspection, **not an application/window launch**. Actual source
launcher/child propagation will be prepared after the privileged checkpoint;
there is no claim that normal-environment runtime already passed. Existing
production GTK3/XWayland selection is not rebranded as native Wayland rendering.

### Planned reversible sequence and matrix

After privileged hash confirmation and explicit removal approval: recheck live
state and prepare a new final checkpoint if anything changed, quit the identified
Snap normally and verify all descendants/other Apricus instances gone. Request
user execution of a fresh `snap save apricus`/check-snapshot if adopted, record
its ID, then remove **only `apricus`**, **without --purge**. Never touch the PoC,
extension, DING, services, personal files or another worktree. No removal command
has been executed in this phase.

Future source tests must stop on smoke errors before benchmarks. After source
shutdown, preserve generated files, restore only reviewed application settings
and changed native files from staged backup (never blindly extract extension
or shared cache contents), compare original GNOME values and backup integrity,
then ask about reinstall. The recovery guide calls out old revision-relative
wallpaper URIs that may be unavailable until a user-approved reinstall; an
alternative preserved image must not be silently called exact URI restoration.
Optional reinstall uses the verified local payload with `snap install
--dangerous`, restores only apricus via the fresh verified snapshot, checks new
revision/path mapping and connections, and starts `/snap/bin/apricus -b` only
after source M1 is gone. Every privileged action remains user-gated.

| Current stage / requirement | Result |
|---|---|
| Exact installed version/revision and running-process inventory | PASS, 2.0.7 x3 |
| Candidate package retained/readable, matching mounted contents | PASS |
| Installed root-only blob's exact SHA256 | **PENDING USER INTERVENTION** |
| New user/system-data backup readability and configuration capture | PASS |
| Old backups retained/integrity verified | PASS |
| GUI-independent recovery/reinstall procedure | PREPARED, not executed/tested as a reinstall |
| Native dependency/path inspection without sitecustomize/Landlock | PASS, inspection only |
| Snap normal quit/removal | NOT TESTED / NOT PERFORMED |
| Native M1 first visible frame, STOP/resources, RESUME | NOT TESTED |
| Native M1 DING clicks/dragging and Overview | NOT TESTED |
| Native M1 playlist/stream/web/persistence/static matrix | NOT TESTED |
| Native M1 CPU >=60s / RSS/PSS/process/thread/latencies | NOT TESTED, no measurements |
| End-of-test native restoration / reinstall choice | NOT APPLICABLE YET; tests not started |
| Current Snap/session unchanged | PASS; no IPC mutation, removal, reinstall or M1 launch |

**Stopped before sudo and removal; awaiting the user's privileged checksum and
explicit confirmation.** No production code, Landlock adapter, extension,
AppArmor, DING, master/integration branch or PR #1 changed. No push, PR, merge,
tag, release or publication. No new M1 Snap/Flatpak package validation started.

### User-confirmed checksum and authorized normal shutdown ([validation date omitted])

The user supplied the privileged SHA256 of the installed x3 blob: **exact match**
to retained `<artifact-sha256>`.
Installed-payload identity is now PASS, no longer pending. They then explicitly
confirmed proceeding with normal shutdown and temporary removal of apricus only.

Before shutdown, all retained hashes, exact configuration and recorded GNOME
values were rechecked and matched the prepared backup; no refreshed data copy
was needed. Server PID/start identity, strict Snap label and socket peer UID
were verified. Normal IPC `quit` returned `ok=true`. All four original identities
([process IDs omitted]) exited; no Apricus-named or strict Snap apricus
process remained. Configuration after quit still matches the backup. Evidence:
`<private-evidence-dir>`.

**Shutdown PASS. Actual removal is covered in the next section.**

### Native Wayland smoke attempt after temporary Snap removal ([validation date omitted])

The user executed the privileged removal themselves:

```text
sudo snap remove apricus
apricus rimosso (istantanea dei dati snap salvata)
```

Verification after removal: `snap list apricus` absent, automatic snapshot **ID 9**
recorded for `2.0.7 x3`, `snap/apricus` and `/snap/apricus` absent,
`apricus-channel-poc 0.1 x1` still installed, host integration and DING ACTIVE,
no Apricus-named processes left. Removal evidence:
`<private-evidence-dir>`.

No `--purge` was used. No extension, DING, service, repository or other Snap
was changed. No automatic snapshot restore/reinstall was performed.

M1 was then started from `<private-path>` with the
independent `.venv/bin/python -S`, adding only source, venv site-packages and
system GI import paths. No Landlock, no `sitecustomize` adapter, no `make
install`, no ambiguous PATH launcher. Parent/spawn/forkserver probes used the
real HOME/config/runtime/session bus/GSettings and the same source/config paths.
The real native config was preseeded only with the approved Hawaii selection
(`is_stopped=false`); personal media were read-only inputs. This was not private
profile isolation. No production code was changed for this run.

### Results

| Check | Result |
|---|---|
| Snap absent before M1, no competing wallpaper instance | PASS |
| Source server startup and VLC `playing` property | PASS as internal-state observation only |
| First Hawaii frame actually visible | PASS: user confirmed animated video |
| Extension adoption of source window | PASS for initial window only; journal records background-layer adoption |
| STOP command accepted, player PID gone, server alive | PASS |
| STOP persisted (`is_stopped=true`) | PASS |
| STOP latency measurement | NOT TESTED as a valid benchmark; one observed gdbus round trip was 106.53 ms including process launch, not a latency claim |
| Static wallpaper/fallback visibly correct | FAIL/UNCONFIRMED: user reported blue background instead of expected Hawaii still |
| H.264 decoder errors | FAIL/blocker: `get_buffer() failed`, `thread_get_buffer() failed`, `no frame!` logged twice during player teardown after STOP |
| RESUME and visible video return | NOT TESTED: supervisor aborted before RESUME |
| DING clicks/dragging during M1 | NOT TESTED |
| DING after M1 stopped | PASS: user confirmed icons, clicks and dragging work |
| Overview workspaces during M1 | NOT TESTED |
| Playlist/stream/web/persistence/static matrix | NOT TESTED |
| CPU >=60s, RSS/PSS/process/thread, STOP/RESUME latencies | NOT TESTED: benchmarks not started |
| Source termination and child cleanup | PASS: no recorded source PIDs remain; supervisor/watchdog exited |
| Native config/cache restoration | PASS: original native files byte-identical to backup |
| GNOME background/settings restoration | PASS for values; original `~/snap/apricus/x3/...` URI target remains missing after Snap removal, as the user accepted |
| Non-Snap original files vs initial archive | PASS: 13 files compared, no remaining changes |
| Snap reinstall | NOT PERFORMED: explicitly left for a later user decision |
| Bridge service telemetry | NOT TESTED as a service check; separate bridge name remained unowned |

The retained M1 static PNG (`after-test-config/static-d47832c882a05d8a.png`)
contains a correct Hawaii sunset frame, so the generated fallback asset itself is
not blank. The user-visible blue screen may overlap the automatic URI restoration
after STOP, so it is not conclusively attributed to a static-generation bug. The
H.264 errors occurred after the STOP request during teardown, not during the
initial visible playback, but they still abort the smoke gate before benchmarks.
No speculative VLC/renderer fix was applied.

Recovery completed with standalone system-Python `recover.py`; the same file is
byte-identical to the reviewed `tests/manual/m1_native/session.py`. Native
recovery tests were added (`tests/test_m1_native_session.py`: preserve generated
data, restore originals, keep absent paths absent, never reinstall Snap).
**2 recovery tests pass.** Ruff lint/format pass.

### Final disposition

M1 source is fully stopped; native data and original URI values are restored;
Snap remains removed; PoC, extension and DING are untouched. No restart/reinstall
was performed automatically. No production code, integration branch, PR #1,
AppArmor, login/session or package change occurred. No push, PR, merge, tag,
release or publication.

**Stopped after an interrupted smoke test. Do not start Snap/Flatpak package
validation.** The section below supersedes it with the authorized second run.

### Second native attempt: full smoke PASS ([validation date omitted])

Fresh recovery set `<private-evidence-dir>` preserved the MODE_NULL
original native config/cache and the missing-file URI settings chosen by the
user. The supervisor was corrected (test tooling only) to record H.264 teardown
lines without auto-aborting on them or on blue-background observations; it still
aborts on Python tracebacks, unexpected exit or uncontrolled processes. No
production renderer/VLC option was changed.

STOP timeline tooling `tests/manual/m1_native/stop_timeline.py` records GNOME
state before STOP, the expected static asset, player death and every later URI
change over 5s. Timeline `<private-evidence-file>` (52 rows) establishes:

- Before STOP, GNOME already pointed to the native static asset
  `<private-file-uri>`.
- The expected PNG existed throughout: 1920x1080, 8-bit/color RGB,
  1251009 bytes, valid PNG signature.
- After the D-Bus STOP reply (122.72 ms round trip including gdbus launch, not
  a latency claim), the URI was unchanged.
- Player PIDs reached zero and no later GSettings rewrite to blue occurred.
- Final state `is_stopped=true`, server alive.

The blue origin is therefore resolved: the live desktop's blue screen is GNOME's
`primary-color #023c88` fallback when `picture-uri` points to a missing file
(observed after Snap removal restores the old `~/snap/apricus/x3/...` URI). It
is not the STOP fallback path, which leaves the existing valid native static
URI untouched. No fallback-order, PNG-generation or PNG-application defect was
reproduced in this run. The 16-bit Mutter rejection path was not triggered; the
generated cache is valid 8-bit RGB.

H.264 diagnosis (separate, same run): 34 teardown lines
(`get_buffer() failed`, `thread_get_buffer() failed`, `no frame!`, two decoder
contexts) appear in `<private-evidence-file>` after the STOP request during player
teardown. They do not precede STOP, do not crash the server, do not prevent
player release (old PID gone, server alive), and leave no orphans. RESUME then
created player [process ID omitted] and reached `playing`. No normal-quit-without-STOP
comparison was run, no VLC option was changed, and no decoder/renderer cause is
claimed. The errors remain non-fatal teardown noise for this configuration.

| Check | Result |
|---|---|
| Snap absent before M1, no competing wallpaper instance | PASS |
| Source server startup and VLC `playing` property | PASS as internal-state observation only |
| First Hawaii frame actually visible | PASS: user confirmed animated video |
| Extension adoption of source window | PASS: background-layer adoption logged for initial and resumed windows |
| STOP command, player termination, server survival | PASS |
| STOP persisted (`is_stopped=true`) | PASS |
| STOP/RESUME latency | NOT TESTED as benchmarks; STOP gdbus round trip 122.72 ms and RESUME round trip 23.40 ms include process launch, not latency claims |
| Static wallpaper visibly correct | PASS: user confirmed still Hawaii photo; URI/PNG timeline corroborates |
| RESUME new player creation and `playing` | PASS: new PID [process ID omitted], no overlap with STOP PID set |
| First frame after RESUME visibly animated | PASS: user confirmed |
| New window adopted after RESUME | PASS: second adoption event logged |
| DING clicks/dragging during resumed M1 | PASS: user confirmed |
| Overview during resumed M1 | PASS: user confirmed |
| Playlist/stream/web/persistence/static matrix | NOT TESTED in this authorized smoke scope |
| CPU >=60s, RSS/PSS/process/thread measurements | NOT TESTED: benchmarks not started |
| Source termination and child cleanup | PASS: 10 recorded identities absent, supervisor/watchdog exited cleanly |
| Native config/cache restoration | PASS: byte-identical to fresh backup |
| Original URI values restored | PASS for values; missing-file target remains as the user accepted |
| Snap reinstall | NOT PERFORMED: left uninstalled per current user decision |
| Bridge service telemetry | NOT TESTED as a service check; separate bridge name remains unowned |
| Production bug fix | NONE: no reproducible M1 bug established; no production file changed |

No measurement here is imported from Xvfb or presented as a GNOME performance
result.

### Final disposition (second native attempt)

M1 source is fully stopped; native data and original URI values are restored;
Snap remains removed; PoC, extension and DING are untouched. No restart/reinstall
was performed automatically. No production code, integration branch, PR #1,
AppArmor, login/session or package change occurred. No push, PR, merge, tag,
release or publication.

**Stopped after a successful smoke test. Do not start Snap/Flatpak package
validation.** The matrix run below supersedes this checkpoint.

### Full functional matrix + benchmarks ([validation date omitted])

Same unconfined source setup, Snap still removed, no Landlock, no production
changes. Live root `<private-evidence-dir>`, driver
`tests/manual/m1_native/matrix.py`, sampler `tests/manual/m1_native/bench.py`
(test tooling only). STOP latency samples include gdbus CLI launch overhead and
are reported as observed ranges, not calibrated claims.

| # | Test | Result |
|---|---|---|
| 1 | STOP persistence across server reload (same reload() path used at startup) | PASS: reload while stopped spawns no player, state stays stopped, config true |
| 2 | Wallpaper change while STOP active | PASS: selection updated, stopped background re-applied, no player spawned |
| 3 | Double STOP / double RESUME idempotency | PASS: single player after double resume, state consistent |
| 4 | Playlist STOP/RESUME/advance (2s+3s clips) | PASS: 5 auto-advances while playing, zero advances during 8s STOP, advance resumes with fresh player |
| 5 | Controlled HTTP stream (loopback) | NOT APPLICABLE as playback: app SSRF guard rejects local addresses by design with a clean warning, no crash; STOP/RESUME lifecycle around it works |
| 5b | Public HTTP stream (1 MB test file) | PASS: RESUME playing in 3.2s, first-frame log 4.8s, stream thumbnail static cached (415 KB PNG) |
| 6 | Local web wallpaper lifecycle | PASS: user confirmed rendered page; WebKit Network+Web processes present; STOP kills player; snapshot-to-static fails gracefully (cairo converter DEBUG, falls back to neutral PNG, no crash) |
| 7 | Static disabled | PASS: STOP leaves background untouched (URI byte-identical before/after) |
| 7b | Static re-enabled while stopped | PASS: Hawaii cache re-applied without player |
| 8 | Missing static fallback | PASS: unknown video while stopped applies valid 69-byte `stopped-background.png` |
| 9 | Repeated STOP/RESUME cycles (5x) | PASS: all recorded player PIDs gone, only server remains |
| 10 | Orphan VLC/FFmpeg/WebKit processes | PASS: none; HTTP helper killed after tests |
| 11 | Extension adoption of new windows | PASS: adoption events for initial and resumed windows |
| 12 | DING clicks/dragging + Overview after cycles | PASS: user confirmed both with M1 playing |

H.264 teardown lines recur after STOP during player teardown (non-fatal, no
crash, no blocked release, RESUME unaffected). Web snapshot cairo-converter
failure is a graceful DEBUG fallback, not an error. No production fix
introduced.

### Benchmarks (GNOME Wayland, 15 CPUs, video Hawaii 1080p)

CPU% is machine-normalized (100% = all 15 cores); single-core equivalent ≈15x.
RSS/PSS are whole-tree sums (server + tracker + forkserver + player).

| State | CPU mean / max | RSS mean | PSS mean | Procs | Threads |
|---|---|---|---|---|---|
| PLAYING 60s | 2.61% / 2.95% (≈39% one core) | 420 MB | 330 MB | 4 | 28 |
| STOPPED 60s | 0.00% / 0.00% | 90 MB | 51 MB | 3 | 7 |
| RESUMED 60s | 2.56% / 2.97% (≈38% one core) | 420 MB | 330 MB | 4 | 28 |

STOP latency observed (8 samples, gdbus-inclusive): 36–141 ms.
RESUME to `playing` observed: 570 ms (playlist), 768–866 ms (video),
1342 ms (web), 3189 ms (public stream incl. extraction). First-frame log after
RESUME: ~1.0–4.8 s depending on mode. Visible-frame user confirmations were
obtained for video and web; no calibrated visible-latency claim is made.
No RESUME CPU spike beyond the steady playing level was observed in the 60s
resumed window. Xvfb numbers are not compared here.

### Final disposition (matrix run)

M1 and all children fully stopped (no Apricus/FFmpeg/WebKit processes, no
failure record); native config/cache byte-identical to the fresh backup;
original URI values restored (missing-file target accepted by the user); Snap
still not installed; PoC, host extension and DING ACTIVE and verified. No
restart/reinstall performed automatically. No production code, integration
branch, PR #1, AppArmor, login/session or package change. No push, PR, merge,
tag, release or publication. Recovery tooling unchanged in behavior; matrix and
sampler helpers added as test-only files with 2 pre-existing recovery tests
still passing.

**Stopped after a complete functional matrix and benchmarks. Do not start
Snap/Flatpak package validation.**
