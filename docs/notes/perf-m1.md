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
