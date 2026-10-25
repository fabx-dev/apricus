# M1 package validation — preparation plan (Snap strict + Flatpak user)

Base: `9940bf3`, branch `feat/gnome-policy-stop`. No install performed.
No merge/push/PR/tag/release/publication. Release workflow untouched.

## 1. Automated tests (preliminary gate)

Full suite with short `--basetemp`: **392 passed**.
Note: 2 `snap_ipc` tests fail only when pytest's `--basetemp` path makes the
AF_UNIX socket path exceed 108 chars (long build-dir names); they pass with a
short temp dir. Environment/path artifact, not an M1 regression.

## 2. Packaging identity vs validated integration branch

Read-only diff `feat/gnome-wayland-integration..HEAD` over `snap/ pkgs/
.github/`: **empty**. No packaging commits exist on the M1 branch; M1 inherits
the already-reviewed manifests byte-identically (base core24, strict
confinement, same plugs incl. gsettings/wayland/x11/opengl, same
gnome-status slot, Flatpak runtime org.gnome.Platform 50, same pinned
yt-dlp 2026.7.4 / Deno 2.9.7 stack). No sandbox permission change, no SSRF
guard change, no VLC renderer change is proposed.

Submodule `pkgs/flatpak/shared-modules` (flathub, pinned `b8236c7`) was
initialized locally to run the build; no branch content changed.

## 3. Build modes (artifact-only, no publication)

### Snap (NOT BUILT YET — requires sudo, gated below)
Reference workflow `.github/workflows/snap-test-build.yml`: manual/PR only,
no credentials, no login/upload/release steps, uploads only a `.snap`
artifact. It was NOT triggered (no release workflow invoked, no tag created).
Local equivalent command (needs explicit user approval, requires sudo):
`sudo snapcraft pack --destructive-mode` in the worktree, then
`mv apricus_*.snap dist/` + `sha256sum`. Expected artifact:
`dist/apricus_2.0.7_amd64.snap` (name/version from snapcraft.yaml +
meson.build). Installed x3 blob is gone from `/var/lib/snapd/snaps`
(removal cleaned it); retained verified candidate stays untouched in
`<private-evidence-dir>`.

### Flatpak (BUILD RUNNING locally, no install, no remote)
`flatpak-builder --force-clean --repo=build/flatpak-repo
--state-dir=build/flatpak-state build/flatpak-build
pkgs/flatpak/io.github.fabx_dev.Apricus.json`, then
`flatpak build-bundle build/flatpak-repo dist/apricus-m1-test.flatpak
io.github.fabx_dev.Apricus`. User GNOME Platform/Sdk 50 already installed.
No `--install`, no remote add, no flathub submission. Log:
`build/flatpak-build.log`. VLC is compiled from source inside the sandbox;
expect 15–40 min.

## 4. Packages currently on the machine

- `apricus`: NOT installed (removed [validation date omitted], snapshot ID <snapshot-id> kept by snapd).
- `apricus-channel-poc 0.1 x1`: installed, must stay untouched.
- Retained x3 payload: `<private-evidence-dir>`
  (sha256 `b94fac7c…f76581`, verified against the removed blob before removal).
- Prior native user data: `~/.var/app/io.github.fabx_dev.Apricus/` exists from
  before — back it up before any Flatpak test install (a test install would
  reuse it).

## 5. Planned install commands (NOT EXECUTED — awaiting approval)

- Snap: `sudo snap install --dangerous dist/apricus_2.0.7_amd64.snap`
  (requires sudo; no store, no `--classic`, strict confinement kept).
- Flatpak: `flatpak install --user --reinstall dist/apricus-m1-test.flatpak`
  (no sudo; user installation only).

## 6. Data-protection strategy

- Snap test install creates a fresh `~/snap/apricus/` (absent now); PoC data
  (`~/snap/apricus-channel-poc/`) untouched (different snap name).
- Flatpak test install would reuse `~/.var/app/io.github.fabx_dev.Apricus/`:
  archive it to a new timestamped dir first, restore afterwards.
- Native `~/.config/apricus` (currently MODE_NULL original) archived before
  each packaged run; packaged apps use their own sandboxed config paths, but
  both write real GSettings wallpaper keys — record exact URIs before/after.
- Never install both packaged variants simultaneously; never run a packaged
  instance together with a source instance (same D-Bus names).

## 7. Rollback

- Snap: `sudo snap remove apricus` (+ optional `sudo snap restore 9` only if
  user later wants the old x3 data back — NOT automatic).
- Flatpak: `flatpak uninstall --user io.github.fabx_dev.Apricus`, then restore
  the archived `~/.var/app/...` dir and the recorded GSettings URIs.
- Verify: no apricus processes, original URIs restored, extensions ACTIVE.

## 8. Validation matrix — Flatpak results ([validation date omitted], authorized --user install)

Bundle: `<test-flatpak-bundle>` (52,625,152 bytes),
sha256 `<artifact-sha256>`,
built from worktree HEAD `26138c3` (M1 sources identical to validated
`9940bf3`; only docs/test-tooling added after). App-ID
`io.github.fabx_dev.Apricus`, runtime org.gnome.Platform 50, Sdk 50.
Manifest finish-args untouched: no `--own-name`, no new permission.
Install used `--no-deps -y` (no runtime changes, no remotes added, no sudo).
Throwaway `apricusN-origin` remotes vanished with uninstall; only pre-existing
flathub remains.

Prior `~/.var/app/io.github.fabx_dev.Apricus` (51 files) backed up with hashes
before install, quarantined after test, restored byte-identical (51/51 hash
match). GNOME URIs (incl. pre-existing screensaver `/tmp/frame.png`) restored
exactly. M1 + children fully terminated, no orphans. Snap still absent. PoC,
extensions ACTIVE.

| Check | Result |
|---|---|
| GUI/server start, Hawaii first frame | PASS (user-confirmed animation) |
| STOP: player gone, server alive, static applied | PASS (user-confirmed Hawaii still) |
| RESUME: new player + adoption + video | PASS (reply 32 ms, playing 1124 ms; user-confirmed) |
| Double STOP/RESUME | PASS (idempotent, 1 player) |
| STOP persistence across reload | PASS (no player, stays stopped) |
| Wallpaper change while STOPPED (Kelp: cache miss) | PASS (fallback applied, no player) |
| Playlist advance/freeze/resume (2s+3s clips) | PASS (7 advances, 0 while stopped, resumes) |
| Public HTTP stream | PASS (playing; thumbnail static cached) |
| Loopback HTTP stream | NOT APPLICABLE: SSRF guard refuses local addresses by design (clean warning, lifecycle works) |
| Local web page (file in xdg-videos) | PASS (user-confirmed render; WebKit procs present; snapshot works in bundled WebKit, no cairo failure) |
| Static OFF | PASS (background byte-identical before/after STOP) |
| Missing-cache fallback | PASS (69-byte neutral PNG) |
| 5x STOP/RESUME + orphan check | PASS (0 leftovers) |
| Extension adoption of new windows | PASS (12 events) |
| DING/Overview with video | PASS pre-reload; FAIL (covered Home icon) after an external extension reload — see below |
| Bridge bus telemetry | NOT TESTED as service: app cannot own `io.github.fabxdev.Apricus.GnomeBridge` without `--own-name` (expected sandbox limitation, no permission added to work around it) |

Observed sandbox differences vs native: first STOP took ~3.9 s (single sample;
native ~40–140 ms) — reported, not root-caused; bundled WebKit snapshot works
where system WebKit hit the cairo converter issue.

### Extension-reload cover-up (real finding, no fix applied)

At [event time omitted] a session event (Nautilus/FileManager1 activation) reloaded the
integration extension and relaunched DING — not caused by this test (extensions
and Nautilus untouched). `disable()` returns adopted actors to the normal
stack; the reload scan saw 1 window with 0 role matches, so the live video
window was never re-adopted and stayed above DING's fresh icons (Home hidden;
user-confirmed, and confirmed visible again after STOP). After RESUME the new
window was adopted ([event time omitted]) and video + icon coexist (user-confirmed).
Mechanism and both directions are evidenced in the journal; native
applicability NOT TESTED. No extension/player fix applied (extension changes
require separate approval; no speculative renderer/SSRF/config change made).

### Flatpak benchmarks (15 CPUs, Hawaii 1080p, machine-normalized %)

| State | CPU mean / max | RSS mean | PSS mean | Procs | Threads |
|---|---|---|---|---|---|
| PLAYING 60s | 2.87% / 3.23% | 398 MB | 352 MB | 4 | 26 |
| STOPPED 60s | 0.00% / 0.00% | 76 MB | 60 MB | 3 | 6 |
| RESUMED 60s | 2.26% / 2.87% | 399 MB | 350 MB | 4 | 26 |

Versus native source: same profile shape (playing ≈2.6%, stopped 0%, same
process counts); Flatpak PSS moderately higher (+~20 MB), consistent with
sandbox overhead. No RESUME spike beyond playing level. Xvfb numbers not
compared.

## 9. Follow-up analysis (no installs, no desktop tests)

### 10a. STOP-latency sample (Flatpak first STOP ≈ 3.9 s) — not a confirmed regression

What the single sample measured: monotonic T0 before the `stop_playback`
D-Bus call, T1 when `/proc` no longer showed the player (50 ms poll). The
call is synchronous, so T1−T0 lumps IPC round trip + all server-side work.
Server-side bounds in M1 code: `terminate()` + `join(0.75)` +
`finish_process` (TERM + `join(2)` + KILL + `join(2)`) + config save/load +
static apply. ≈3.9 s is consistent with both `join` timeouts being consumed,
i.e. the first sandboxed player did not exit promptly on SIGTERM. Later
STOPs completed inside bounds (no `Wallpaper player did not stop` exception
anywhere in the run log) and felt normal; they were not individually timed.
The run logs carry no wall-clock timestamps, so phase attribution from
existing logs is impossible. n=1, first-STOP-only, no breakdown → reported,
not root-caused, not declared a regression.

Proposed phase-separating method for the next authorized run (external
observation only, no production change):
`T_call` (before D-Bus) → `T_reply` (method returned = IPC + server work
incl. joins + static apply) → `T_death` (player PID gone; termination lag =
`T_death − T_call`) → `T_uri` (picture-uri flips to the static asset) →
`T_visible` (user confirmation). Additionally: record per-sample evidence
under unique filenames (current `matrix.py` overwrites `matrix-stop.json`
per action; the ranges in §8/`perf-m1.md` come from the run transcript) and
collect ≥5 STOP samples per backend before any comparison with native
(~40–140 ms observed there).

### 10b. Build/report reproducibility from the declared M1 commit

- Worktree clean; M1 code paths (`src/`, `gnome-extension/`, `snap/`,
  `pkgs/`) byte-identical to build-time commit `26138c3`
  (`git diff --name-only` empty) — only docs/test-tooling added after.
- Manifest app module is `type: dir, path: ../..` (the worktree itself);
  the built tree `build/flatpak-build/files/share/apricus/apricus/server.py`
  contains the M1 `_save_config(..., manual_stop=None)` marker.
- Artifact hash re-verified: full value in §8 (a one-char transcription typo
  in the first draft of §8 was found by re-check and corrected — the
  `.sha256` sidecar file was always correct).
- Byte-reproducibility of a rebuild is NOT claimed (build-ids/timestamps);
  the exact rebuild command is recorded in §3.
- Flatpak test status: matrix complete, restore verified, package
  uninstalled. Snap build/install: NOT STARTED, still gated.

## 10. Current status (Flatpak done, Snap gated)

- Tests: PASS (392). Packaging diff: none. Flatpak matrix complete with one
  documented integration finding; restore verified (data 51/51, URIs exact,
  no processes, Snap absent).
- Snap build/install: NOT STARTED — verified plan in §11 below.
- No merge/push/PR/tag/release/publication. No production/sandbox/permission
  changes.

## 11. Snap core24 build plan — verified, NOT executed

Context: Agent A validated the post-reload extension fix (local commit
`31f0c36`, live GNOME Wayland PASS) in their repository. That fix is
**validated but not installed**: the host extension is the pre-fix version
(byte-identical to this worktree's `gnome-extension/`), M1 must not
incorporate it, and the Snap test will run against the pre-fix host
extension. Extension-reload re-adoption scenarios are therefore excluded
from the Snap matrix (known limitation, same as observed); all other matrix
items apply.

### 11a. Isolation options actually available on this machine

- LXD/multipass: ABSENT (no `lxd`/`multipass` snaps or binaries). Enabling
  LXD needs `sudo snap install lxd` + `lxd init` (one-time privileged host
  change) — not done, needs approval.
- Destructive host build: `sudo snapcraft pack --destructive-mode` installs
  build-packages into the HOST via apt and needs sudo every build — avoided
  unless its necessity is demonstrated and separately approved.
- GitHub `snap-test-build.yml` (artifact-only, no credentials/publish):
  triggers on `pull_request` ONLY — no `workflow_dispatch`, so it cannot be
  launched manually; it would need a push to a PR branch. M1 is local-only
  (no push/PR allowed in this phase), and PR #1 must stay untouched.
  Verdict: currently unusable without push + PR actions.
- Conclusion: no unprivileged isolated local build exists today. Both viable
  paths need exactly one approval: (A) one-time LXD setup, or (B) one
  destructive build, or (C) push approval for the artifact-only workflow
  (note: it checks out the PR *merge* commit, so provenance would be
  M1+master rather than pure M1 HEAD — document the SHAs either way).

Recommended: (A) LXD one-time setup, then unprivileged `snapcraft pack`
(user already in the `lxd` group) — full isolation, host APT untouched,
repeatable for re-verification.

### 11b. Prerequisites (verified, nothing installed)

- `snapcraft` 9.2.1 present (classic snap). LXD absent (see above).
- `gh` authenticated as fabx-dev (read-only `auth status` check; no repo
  changes made). No workflow invoked, no tags created.
- Disk 61 GB free; RAM 4 GB total (tight but sufficient — the snap does not
  compile VLC, it stage-packages `libvlc5`/`vlc-plugin-*`).
- Network required at build time (stage-packages, Deno 2.9.7 zip, pip
  yt-dlp 2026.7.4, Meson toolchain) — same as the validated branch.
- No missing project files: `snap/snapcraft.yaml`, `pkgs/deno.env`,
  `pkgs/ytdlp.env` pins and `meson.build` 2.0.7 all present and unchanged
  vs the integration branch.

### 11c. Exact commands (NOT executed — gated)

Build (option A, after approved one-time `sudo snap install lxd` +
`sudo lxd init --auto`):
```sh
cd <private-path>
snapcraft pack
mkdir -p dist && mv apricus_*.snap dist/
sha256sum dist/apricus_*.snap | tee dist/apricus.snap.sha256
unsquashfs -d /tmp/snap-check dist/apricus_*.snap \
  usr/bin/apricus usr/share/apricus \
  usr/share/apricus/gnome-extension/install-host.sh \
  "usr/share/apricus/gnome-extension/apricus-integration@fabx-dev/metadata.json"
head -n1 /tmp/snap-check/usr/bin/apricus | grep -q "/usr/bin/env python3"
test -f /tmp/snap-check/usr/bin/apricus
rm -rf /tmp/snap-check
```
Expected artifact: `dist/apricus_2.0.7_amd64.snap` (+ `.sha256` sidecar).
Build (option B, only if A is rejected and approved):
`sudo snapcraft pack --destructive-mode` in the worktree, then the same
verify/move/hash steps.

Install (later approval, strict, no store):
`sudo snap install --dangerous dist/apricus_2.0.7_amd64.snap`
Preseed selection via the sandbox profile
`~/snap/apricus/<rev>/.config/apricus/config.json` (host-writable user
file), exactly like the validated x3 layout; record GNOME URIs before/after
each phase.

### 11d. Snap test matrix (post-install approval)

GUI/server start, Hawaii first frame (user-confirmed), STOP + player release,
RESUME + new adoption, static + missing-cache fallback, STOP persistence
(reload path), playlist advance/freeze/resume, public stream, local web,
static OFF, 5x cycles + orphan check, adoption events, DING/Overview
(user-confirmed), bridge telemetry via the `gnome-status` slot
(applicable here, unlike Flatpak), STOP-latency breakdown with the
`T_call → T_reply → T_death → T_uri → T_visible` method (≥5 samples; the
Flatpak 3.9 s single sample is the comparison baseline, not a verdict),
60 s PLAYING/STOPPED/RESUMED benchmarks kept separate from source/Flatpak
numbers. Extension-reload scenarios EXCLUDED (pre-fix host extension).

### 11e. Rollback (no automatic restore)

`sudo snap remove apricus`; leave snapd snapshot 9 (old x3 data) untouched
unless the user later explicitly asks for `snap restore`;
restore recorded GNOME URIs; re-verify no Apricus processes, PoC installed,
extensions ACTIVE. Never auto-reinstall the old x3 payload.

STOPPED before compiling or installing Snap. No sudo used in this phase; no
push/PR/tag/release/publication; no workflow, secret, ruleset, extension,
sandbox, renderer or SSRF-guard changes.
