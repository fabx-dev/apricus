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
  uninstalled. Snap build: DONE (§13); Snap install: NOT PERFORMED, still
  gated.

## 10. Current status (Flatpak done, Snap gated)

- Tests: PASS (392). Packaging diff: none. Flatpak matrix complete with one
  documented integration finding; restore verified (data 51/51, URIs exact,
  no processes, Snap absent at test time).
- Snap build: DONE (see §13); Snap install NOT performed, still gated. LXD
  stays installed pending the package-validation decision (see §13).
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

## 12. LXD pre-install read-only check ([validation date omitted], NOTHING installed)

All commands in this section were read-only (`id`, `groups`, `ip link`,
directory listings, `df`, `free`, `.gitignore` inspection). No user added to
any group, no LXD installed, no `init`, no build, no host/session/package
change.

### 12a. Group membership — already effective

`id -Gn` and `groups <test-host>` (sorted diff) are identical and include
`lxd`: the group is effective in the current session. No `usermod`, no
re-login needed (re-login/logout is in any case out of scope).

### 12b. No pre-existing LXD footprint

Absent: `/var/snap/lxd`, `/var/lib/lxd`, `/var/lib/lxc`,
`~/.local/share/lxc`, `~/.config/lxc`, `lxdbr0`/docker bridges, any LXD snap
blob, any LXD/dnsmasq processes. Everything LXD-related observed after a
future install is therefore test-created — verify with before/after
`lxc list`, `lxc network list`, `lxc storage list`, `ip link`.

### 12c. Persistent effects of the proposed one-time setup (to verify at install)

`sudo snap install lxd` adds the LXD snap (mount + socket service, enabled
persistently until purged). `lxd init --auto` (documented upstream
behavior) creates: a `default` dir-backend storage pool under
`/var/snap/lxd/common/lxd/storage-pools/default`, an `lxdbr0` bridge with
dnsmasq DHCP/DNS + NAT/iptables rules, and a `default` profile attaching
both to new instances. Alternative for tighter control: explicit `lxd init`
(non-auto) with a size-limited loop volume — dir backend has no quotas, so
either monitor `df` and time-box the build, or pick a quota-capable pool at
init (preallocates disk). Decision deferred to install approval.

### 12d. Resources and limits

Disk 61 GB free (LXD image + core24 base + build need roughly 5–10 GB);
RAM 4.7 GB total / 2.4 GB available + 3.7 GB swap free — tight for a
parallel desktop + build container. Mitigation (commands documented, NOT
executed): `lxc config set <snapcraft-apricus-*> limits.cpu=4
limits.memory=3GiB` (or set on the `default` profile before the build so it
applies to the fresh snapcraft instance). Snapcraft names its instances
`snapcraft-<project>-…`; target them by prefix, never by guessing.

### 12e. Rollback (test resources only)

Delete snapcraft instances (`lxc delete --force`), then — only because §12b
proves nothing pre-existed — `lxc network delete lxdbr0`,
`lxc storage delete default`, `sudo snap remove --purge lxd`, and verify
`ip link`/iptables/socket are clean. Build artifacts live in ignored
`build/` and `dist/` (see `.gitignore`), never in the repo. Snapd snapshot 9
and the PoC snap are untouched by all of this.

## 13. Snap core24 build results ([validation date omitted], LXD backend, no install)

Built from worktree HEAD `90fd876` (only docs/test-tooling after the M1 code
commit; `src/` identical to the validated Flatpak build input). Preseed
`build/lxd-preseed.yaml` applied as authorized: standalone, local socket only,
`lxdbr0` <private-address>/24 (host route is <private-address>/24 — no overlap), NAT on for
dependency downloads, dir pool. Observed host changes: `lxdbr0` bridge,
`ip_forward` 0→1, LXD socket service. No user/group changes; `lxd` group was
already effective.

Limits: default-project profile limits do NOT propagate into snapcraft's own
`snapcraft` project (separate profile namespace — documented pitfall). Applied
directly to the live build instance before compilation started and verified
by re-read: `limits.cpu=4`, `limits.memory=3GiB`
(`<test-build-container>`, project `snapcraft`). Build log retained:
`build/snapcraft-m1.log`. No `--destructive-mode`, no host APT changes, no
manifest/permission/code changes. Memory stayed healthy (≥2.9 GB available
throughout per samples); disk went 34G→41G used (+~7 GB: LXD snap, base
image, build data).

Artifact: `dist/apricus_2.0.7_amd64.snap` (123,392,000 bytes) +
`dist/apricus_2.0.7_amd64.snap.sha256`:
`<artifact-sha256>`
Verification (same checks as the artifact-only workflow, plus M1/payload
proof): python3 entrypoint, `install-host.sh`, extension `metadata.json`
present; `meta/snap.yaml` = name apricus, version 2.0.7, base core24,
confinement strict, grade stable; built `server.py` carries the M1
`manual_stop` marker (5 hits); payload diff vs the validated pre-M1 x3 snap
(`b94fac7c…`) is exactly the 11 M1 source files
(child_jobs, commons, gtk4app, mediacache, menu, video/web_player, server,
snap_ipc, stop_support, utils) — no other delta.

LXD state at end: instances `base-instance-…` + `snapcraft-apricus-…`
STOPPED, network `lxdbr0` + storage `default` CREATED/used, no build
processes left. LXD intentionally left installed (decision deferred per
authorization: remove only after package validation). Removal prep (NOT
executed): `lxc delete --force` the two snapcraft instances (keep the cached
base image only if a rebuild is wanted, else delete it too), then the §12e
sequence.

STOPPED before installing the package. No sudo beyond the authorized LXD
install; no snap install/remove; no push/PR/tag/release/publication; no
workflow, secret, ruleset, extension, sandbox, renderer or SSRF-guard
changes.

## 14. Snap strict validation results ([validation date omitted], user-installed --dangerous)

Artifact `dist/apricus_2.0.7_amd64.snap` (123,392,000 bytes),
sha256 `<artifact-sha256>`
(re-verified before install). Installed as revision x1, strict confinement
verified at runtime (`snap.apricus.apricus (enforce)`). No concurrent Apricus,
no Flatpak/native instances. GNOME URIs recorded; no pre-existing
`~/snap/apricus` (fresh profile created by first run). Snapshot 9 intact
before/after. PoC x1 untouched.

### Smoke test — 10/10 PASS

GUI+server start, strict enforcement, Hawaii first frame (user-confirmed),
initial extension adoption, STOP (player gone, server alive, test-video static
user-confirmed), RESUME (new player, new adoption, video user-confirmed),
DING clicks/drags + Overview with video playing (user-confirmed).

STOP sample 1: `T_reply` 179.5 ms = `T_death` 179.6 ms (termination completed
inside the call); `T_uri` never fired in any sample — the static URI was
already applied during playback, so there was nothing to flip. Same pattern
on all samples: termination always lands inside the D-Bus round trip.

### Bridge (separate section, no protocol/AppArmor changes)

- Owned only while the GUI runs (`show_gui` → Register accepted, Update
  flowing; sender uid/pid checks pass). Background-only server: name absent
  (expected — bridge lives in the GUI process).
- After STOP: still owned, Updates continue. After GUI close: cleanly
  unowned. After GUI reopen: re-registered. After full server restart
  without GUI: absent (expected).
- Lease expiry: NOT TESTED (would require interfering with the live
  extension session).
- D-Bus introspection exposes `GnomeBridge1.Register/Update/Unregister`
  (generation + lease + renew); no new permissions were added to make this
  work under strict confinement.

### Extended matrix

| Check | Result |
|---|---|
| STOP persistence across reload + full server restart | PASS (0 players, stays stopped) |
| Double STOP/RESUME | PASS (idempotent, 1 player) |
| Playlist advance/freeze/resume (2s+3s clips) | PASS (5 advances, 0 while stopped, resumes) |
| Public HTTP stream | PASS (playing + user-confirmed visual + cached thumbnail static) |
| Local web page | PASS (user-confirmed render, WebKit procs, snapshot works) |
| Loopback HTTP stream | NOT TESTED in snap (same SSRF guard as native — intentionally not bypassed) |
| Static ON/OFF + missing-cache fallback | Mechanism observed working; see anomaly A — final verdict NOT VERIFIED for the OFF/ON pair |
| 5x STOP/RESUME + orphan check | PASS (0 leftovers, adoptions logged) |
| 10s-clip playlist red/blue cleanup | done, dir verified clean |
| Extension reload with live window | EXCLUDED (pre-fix host extension, per authorization) |
| DING/Overview after cycles | PASS (user-confirmed) |

### STOP/RESUME latencies (socket RPC, per-sample unique files)

13 STOP samples: `T_reply` min 2.6 / median 99.2 / max 393.7 ms;
`T_death` == `T_reply` in every sample (player reaped inside the call).
`T_uri` never fired (static pre-applied — method note, not a failure).
9 RESUME samples: reply min 12.7 / median 13.8 / max 237.6 ms (max = first
resume after server restart, includes player spawn); time-to-playing min 662
/ median 726 / max 954 ms. No `Wallpaper player did not stop` timeout in any
sample. Ranges reported, no regression claim (no pre-M1 snap baseline exists).

### Benchmarks (15 CPUs, Hawaii 1080p, machine-normalized %)

| State | CPU mean / max | RSS mean | PSS mean | Procs | Threads |
|---|---|---|---|---|---|
| PLAYING 60s | 2.27% / 2.76% | 408 MB | 370 MB | 4 | 28 |
| STOPPED 60s | 0.00% / 0.00% | 80 MB | 64 MB | 3 | 8 |
| RESUMED 60s | 2.58% / 2.85% | 426 MB | 380 MB | 4 | 28 |

Same profile as native source and Flatpak (playing ≈2.3–2.9%, stopped 0%).
No RESUME spike beyond playing level. Xvfb numbers not compared.

### Anomaly A — intermittent selection revert (snap session only; NOT FIXED)

Observed: `video()` via socket IPC intermittently did not persist — the file
kept an older web-HTML source; 8 `_checkDefaultSource` warnings total; no
`Rejected video path` lines; `snap run --shell` shows the files as visible;
`ok:true` replies throughout; dispatch verified by code reading
(`fn(*args)`, per-request event, no reuse).
Ruled out: snap path visibility, RPC arg passing, GUI as sole cause (flips
also occurred with GUI closed and bridge unowned), orphan players (zero
leftovers at every check), lock contention (instant acquire), snapd
background refresh (no tasks in the test window).
Not isolated: the exact writer/trigger (candidates: GUI stale-snapshot
saves — 12 call sites — and transient `isfile` failures inside the long
running server; evidence supports neither conclusively, and the two
hypotheses contradict on the GUI-closed flips).
Impact: the Kelp-fallback, Hawaii-restore and static-OFF/ON observations
from the affected window are re-labeled NOT VERIFIED (outcomes are
indistinguishable between designed fallback and reverted selection), except
the test-video static-apply itself (d47832 observed → source WAS Hawaii at apply
time). Unaffected and standing: all STOP/RESUME mechanics, playlist
advance/freeze/resume, stream/web visuals, orphan checks, bridge behavior,
benchmarks (no selection changes involved).
No production code changed for this; no renderer/SSRF/sandbox change made.

### Restore — VERIFIED

Server quit cleanly (0 processes, both bus names released), test media
deleted, `sudo snap remove apricus` (user-run, no `--purge`), snap absent,
snapshot 9 intact and untouched, PoC x1 intact, extensions ACTIVE, 3 GNOME
URIs restored byte-identical (one self-caught screensaver typo corrected
immediately and re-verified), LXD left installed per decision. New auto
snapshot of the test profile retained by snapd (not restored, documented).

LXD final state: instances STOPPED (base + builder retained for a possible
rebuild — removal prep in §12e unchanged), network/storage present, no build
processes left.

## 15. Anomaly A — targeted diagnosis from existing code/logs (no live tests)

### 15a. Warning sequence (facts)

8 `_checkDefaultSource` warnings, all in `build/snap-live/server.log`
lines 5355–5585 (static-OFF block era), all with `mode: MODE_VIDEO` and a
`file://...web.html` URI as Default. Zero such warnings in all native logs,
the Flatpak log, `server2.log` (post-restart), and zero `Rejected video
path` lines except one intentional negative probe. The URI that kept
reappearing was selected once via `webpage()` earlier in the same session;
the plain-path videos (Hawaii/Kelp) were never rejected by the `isfile`
guard (no rejection logs) and `snap run --shell` confirms their visibility.

### 15b. IPC command and responses (facts)

All implicated calls used socket RPC `{"op":"call","attr":"video",...}`,
all returned `ok:true`, all round trips measured ≈0.00 s at the client
(i.e. no 30 s `done.wait` timeout). Dispatch is `fn(*args)` with a
per-request event — verified by reading `snap_ipc.py`, no shared-event
reuse possible. A control call with a nonexistent path correctly returned
ok + logged `Rejected video path`, proving the method body runs.

### 15c. Subsequent wallpaper-state writes (facts)

Every flip coincided with `_checkDefaultSource` replacements (logged) or
with plain saves. Candidate writers enumerated from code: server paths
(always load-then-modify-then-save), player `_save_queue_index`
(loads fresh, skips when stopped, playlist-only), GUI (12 blind
`ConfigUtil().save(self.config)` call sites, several without a preceding
reload in the same handler). No orphan player existed at any check; the
config lock was never contended (instant acquire); snapd recorded no tasks
during the test window (rules out refresh-driven AppArmor churn).

### 15d. Reload-previous-selection paths (code reading)

`_checkDefaultSource` (replaces invalid/empty Default with first `isfile`
value), `_migrateLegacyMonitorKeys`/`_createMissingMonitors` (key juggling,
never invents values), GUI blind saves (can restore a stale snapshot),
player queue-index save (guarded). `is_stopped=true/false` does not select
the writer: flips were observed only while stopped because every snap
selection change was deliberately made while stopped.

### 15e. x3 vs M1 in the responsible files (artifact evidence, no install)

Unsquashed the retained x3 payload and diffed `utils.py`/`server.py` against
the M1 tree: `_checkDefaultSource` is byte-identical; `video()` source
setting and `_setup_player` source block are unchanged; M1 touched only
save locking + STOP latch preservation + the STOPPED guard + STOP/RESUME
itself + GUI badge/button (no new saves, no save-path changes). So the
validator and the selection-setting logic are pre-existing; they cannot be
an M1 regression by code inspection.

### 15f. Facts vs hypotheses

- FACT: flips happened only while the first GUI instance was alive (opened
  [event time omitted], closed before the surgical tests); 0 warnings before its launch,
  0 after its death; native/Flatpak runs (never opened the GUI) show 0.
- FACT: after the GUI was gone for good, repeated `video()` calls stick,
  including the 30 s stability watch.
- HYPOTHESIS H1 (GUI stale-snapshot save): fits the window correlation and
  the both-directions reversions; NOT proven — no single GUI save was caught
  red-handed in the logs, and one flip series appeared to start just after
  a GUI close (teardown-save race possible but unlogged).
- HYPOTHESIS H2 (transient `isfile` failure in the long-running server):
  no supporting evidence; every direct visibility check passes; contradicts
  nothing but explains nothing uniquely either.
- CONCLUSION: most consistent with H1, but explicitly UNCONFIRMED. No code
  changed, per authorization. The re-labeled NOT VERIFIED items from §14
  stand as re-labeled.

### 15g. Minimal comparative live-test plan (x3 vs M1 — NOT EXECUTED)

Goal: decide whether the flip reproduces on validated x3 under identical
conditions. No installs performed in this phase.
- Artifacts (both verified, hashes recorded): x3
  `<private-evidence-dir>`
  (`b94fac7c…f76581`); M1 `dist/apricus_2.0.7_amd64.snap`
  (`5091b926…e1e77`).
- Preconditions per arm: no apricus installed/processes, URIs recorded,
  no `~/snap/apricus` (fresh profile each install), GUI never launched.
- Protocol per arm (identical): user-run `sudo snap install --dangerous
  <artifact>` → launch `-b -d` → 10× [`video(Hawaii)` + read-back at +1 s
  and +30 s] + 5× alternating Hawaii/Kelp with read-back → count flips
  (flip = read-back differs from requested path at either checkpoint).
- Objective criterion (pre-registered): flip-rate comparison; M1 "not worse
  than x3" if both zero or statistically indistinguishable; flips ONLY on
  M1 with zero on x3 across 15 trials each = evidence of an M1-introduced
  difference → only then authorize code investigation.
- Rollback per arm: socket `quit`, 0-process check, user-run `sudo snap
  remove <name>` (no purge), URI restore + verification, snapshot-9 + PoC +
  extensions check. No auto-restore, no snapshot touch.
- Estimated time: ~20 min per arm including waits. A GUI-open arm follows
  ONLY if both GUI-less arms are clean.

## 16. Comparative protocol 2×2 for anomaly A (PREPARED — NOT EXECUTED)

Supersedes the GUI-less-only plan in §15g. Four arms, same script:

- A: Snap x3, GUI closed. B: Snap x3, GUI open.
- C: Snap M1, GUI closed. D: Snap M1, GUI open.

Fixed order A→B→C→D (one package fully before switching; order recorded as
a limitation). Never both packages installed; server quit + 0-process check
+ user-run `sudo snap remove <name>` (no purge) between arms. No snapshot-9
restore, PoC untouched, extension/AppArmor/manifests unchanged, no code
changes. Live runs need a separate authorization; nothing below installs
anything.

Artifacts (hashes re-verified, no installs in this phase):
x3 `<private-evidence-dir>`
(`b94fac7c…f76581`); M1 `dist/apricus_2.0.7_amd64.snap` (`5091b926…e1e77`).

Identical per-arm script (same wallpaper/URLs, commands, counts, intervals,
log collection, initial config, start/stop conditions):
1. Preconditions: no apricus snap/processes, record 3 GNOME URIs, no
   `~/snap/apricus`, per-arm evidence dir `build/snap-compare/<arm>/`
   (independent backup: URIs, install output, first-launch config copy).
2. User-run `sudo snap install --dangerous <artifact>`; launch
   `/snap/bin/apricus -b -d`; wait for socket RPC; open GUI only in B/D
   via `show_gui` (no other GUI interaction).
3. Phase 1 (plain paths): 10× `video(Hawaii)`; after each, record IPC reply
   plus config+URI read-backs immediately, at +1 s and +30 s.
4. Phase 2 (web-URI trigger, explicit): `webpage(file://…/.m1-cmp-web.html`
   in `~/Video/Apricus`, created fresh per arm, deleted afterwards) →
   STOP → 10× `video(Hawaii)` with the same +1 s/+30 s read-backs. This
   replicates the historical flip pattern (webpage → STOP → video); whether
   a URI-in-VIDEO state emerges is recorded as data, not assumed.
5. Per attempt record: exact command, IPC reply, immediate selection,
   +1 s/+30 s selections, config-write evidence (new `Saved` server-log
   lines in the attempt window), any `_checkDefaultSource` WARNING or
   `Rejected video path` lines, GUI-displayed selection at phase ends
   (user reads the Now Playing panel in B/D), actually displayed wallpaper
   at phase ends (user-confirmed when feasible).
6. Server quit via IPC, 0-process check, user-run `sudo snap remove
   apricus` (no purge), URI-restore check, PoC/extension/snapshot-9 check.

Pre-registered criteria:
- A log WARNING alone never counts as proof of a revert.
- A revert counts as CONFIRMED only if the effective selection returns to
  a previous state with no new user/test command in between (read-back
  evidence required).
- x3 ≈ M1 (same flip rate/pattern across matched arms) → anomaly probably
  PRE-EXISTING.
- Reproducible ONLY on M1 under identical conditions → investigate a
  possible M1 regression (code work still needs its own authorization).
- Not reproducible on either → report NOT REPRODUCED (not "resolved").
- Abort (per arm): M1/server processes surviving quit, unexpected Apricus
  processes, session instability → stop, restore, document.
- Estimated time: ~15 min per arm including the +30 s waits (≈1 h total).

Privileges: user-run sudo ONLY for install/remove. Observation, logging,
verification and restore need no privileges. Sudo for anything else is out
of scope.

## 17. Corrected 2x2 protocol (AUTHORIZED for live comparison)

Supersedes §16: x3 has no STOP command, so Phase 1 is STOP-free for all
arms (directly comparable); Phase 2 is package-specific and classified
NON-equivalent. Arms: A=x3/GUI-closed, B=x3/GUI-open, C=M1/GUI-closed,
D=M1/GUI-open. Fixed order A→B→C→D.

Per arm (identical within phase): fresh profile (no `~/snap/apricus`
before install), recorded URIs, per-arm dir `build/snap-compare/<arm>/`,
user-run `sudo snap install --dangerous <artifact>` (no classic/devmode),
launch `-b -d`, GUI only in B/D via `show_gui` (no other GUI interaction).
Phase 1: 10× `video(Hawaii)` + config read-backs immediate/+1s/+30s.
Phase 2: 5× [`webpage(local file)` then (STOP only on M1) then
`video(Hawaii)`] with the same read-backs; file fixture
`~/Video/Apricus/.m1-cmp-web.html` created per arm, deleted afterwards.
Per attempt: command, IPC reply, immediate/+1s/+30s selection, new `Saved`
lines, WARNING/Rejected lines, GUI-displayed selection at phase ends (B/D,
user reads panel), displayed wallpaper at phase ends (user-confirmed when
feasible). Between arms: IPC `quit`, 0-process check, user-run `sudo snap
remove` (no purge), URI restore + verify, snapshot-9 + PoC + extension
check. Snapshot 9 never restored. Abort on concurrent/orphan processes,
confinement errors, session instability, unexpected config/data changes,
or unverifiable restore.

Pre-registered criteria: warning ≠ proof; CONFIRMED revert = selection back
to a previous state with no justifying new command (read-back evidence).
x3≈M1 → probably pre-existing. Only-M1 in Phase 1 → possible M1 regression
to investigate (separate authorization). Only in M1 Phase 2 → investigate
`manual_stop`-state interaction separately. Neither → NOT REPRODUCED.
GUI-open-only on both → pre-existing-compatible.

## 18. Comparative result 2x2 (80 attempts, 0 flips — NOT REPRODUCED)

Arms A (x3/closed), B (x3/open), C (M1/closed), D (M1/open): 10× Phase-1
`video(Hawaii)` + 5× Phase-2 [`webpage(local)` (+STOP on M1) +
`video(Hawaii)`] each, read-backs immediate/+1 s/+30 s, unique per-attempt
evidence (`matrix-compare-NNN.json`), GUI-displayed selection checked at
phase ends in B (Hawaii, consistent) and D (nothing playing + test-video static
— correct end-state: Phase 2 leaves the STOP latch on by design).
Outcome per arm: 20/20 selections stable through +30 s, 0 `_checkDefaultSource`
warnings, 0 `Rejected video path` lines. TOTAL: 80 attempts, 0 confirmed
reverts, 0 warnings.

Per the pre-registered criteria this is NOT REPRODUCED (not "resolved"):
no M1 regression can be declared, and no pre-existing defect is confirmed
either — x3 never flipped either. Honest limitation vs the original
episode: these arms never started playback (selection changes only; the
original flips occurred in sessions with active STOP/PLAY/RESUME cycling
and media players alive). The GUI-open variable alone does not reproduce
it (arms B and D clean). A future reproduction attempt, if authorized,
should interleave selection changes with actual playback/STOP cycles.

Restore after arm D: server quit via IPC, 0 processes, fixture deleted,
user-run `sudo snap remove` (no purge), snap absent, snapshot 9 intact and
untouched, PoC x1 intact, extensions ACTIVE, 3 GNOME URIs byte-identical to
baseline, no `~/snap/apricus`. LXD stays installed (no build/remove this
phase). GUI matrix helper `tests/manual/m1_native/compare_select.py`
committed; per-arm evidence retained under `build/snap-compare/`.

## 19. STOP/RESUME GUI accessibility audit (code-only, no changes)

The user never saw Stop/Resume in any UI used during testing. Verified
against M1 sources (identical in the installed Snap payload) and the
shipped Italian catalog — no GUI run, install or screenshot was possible
in this environment (no desktop-control tools, no authorized runs), so the
interface below is a code-verified widget inventory, not a screenshot.

### Where the controls actually are

1. GTK4 Control Center (`src/apricus/gui/gtk4app.py`), page
   "Now Playing" (`_now_playing_page`), transport row (L1332–1362):
   circular Play/Pause button + `np_stop_btn`
   (`Gtk.Button(label=_("Stop wallpaper"))`, L1340) + mute toggle +
   volume scale. Handler `on_stop_resume` (L4311): disables the button,
   calls `stop_playback()`/`resume_playback()` on a worker thread based on
   live `playback_state`, then re-enables + `_refresh_all`. The Play
   button also routes to resume when stopped (L4300–4302).
2. GTK3 player context menu (`src/apricus/menu.py::build_menu`, used on
   wallpaper right-click by `video_player.py:514` and `web_player.py:228`,
   and for the (X11-only effective) systray): ALWAYS appends both
   "Stop wallpaper" and "Resume wallpaper" (L127–133) with no
   state-based hiding or disabling.

### Visibility, state feedback, translations

- The Now Playing button is never hidden; it is sensitive whenever the
  server object exists, its label toggles Stop↔Resume (L1496–1501), the
  badge shows Playing/Paused/Stopped ("Fermato" in Italian), the
  equalizer hides when not playing, and Play shows the start icon.
- BUT the Control Center opens on the "Library" page by default (L225);
  the user must open the sidebar section "Now Playing" first. Nothing on
  Library/Playlist/Streaming/Web/Settings mentions STOP.
- Italian catalog verified in `po/it.po` AND inside the Snap payload
  (`.mo` unpacked from the M1 artifact): "Ferma wallpaper",
  "Riprendi wallpaper", "Fermato", "Impossibile modificare la
  riproduzione del wallpaper." The Snap ships the M1 GUI code (7
  `np_stop_btn`/`on_stop_resume` references in the packed `gtk4app.py`).

### Why the user never saw it

All validation was IPC-driven (`gdbus`/socket calls). The Control Center
was opened only ~3 times, briefly, for bridge checks — never navigated to
Now Playing on record. The systray path is effectively absent on Wayland
GNOME (no AppIndicator host), leaving only the undiscovered video
right-click menu. Net effect: a working, translated, state-reflecting
control existed two clicks away (open panel → Now Playing) but nothing
surfaced it in the flows the user actually used.

### Minimal UX proposal (NOT implemented — awaiting approval)

Single header-bar STOP/RESUME toggle, visible on every Control Center
page, wired to the existing `on_stop_resume` and reusing the existing
translated strings (no new msgids, no `.po` churn): add the button next to
the window controls/header in the panel chrome and sync its label in
`_refresh_all` next to `np_stop_btn`. Optional second step: make the
GTK3 context menu show only the applicable action (Stop XOR Resume) from
live `playback_state` instead of both unconditionally. Files to touch:
only `src/apricus/gui/gtk4app.py` (+ `src/apricus/menu.py` for step two).
No bridge/extension/IPC/renderer changes.

## 20. M1 + GNOME reload-fix combination check ([validation date omitted], temp worktree only)

Scope: Fase 1 translations completed (Play→Riproduci/Pausa, Abspielen/Pause,
Reproducir/Pausar, Lire/Pause，再生/一時停止； all catalogs compile; other
strings untouched). This section records the isolated combination test.

Worktrees (read-only listing): M1 `<private-path>`
(`8a6d194`→`a21d0f8` after the i18n commit), Agent A
`<private-path>` (untouched), roadmap worktree untouched.
Merge-base M1↔integration: `9cb6ea5`. Integration adds exactly one commit
on top: `31f0c36` (extension enable()-scan fix + rescan GJS harness +
CI step). M1 adds 27 commits on top of the same base (implementation,
tests/tooling/docs, two-button UX + i18n).

Production-code overlap: NONE except `tests/test_gnome_integration.py`
(M1 added a FakeConfigUtil.load helper; the fix added an independent
rescan test in a different hunk). `gnome-extension/`, manifests,
workflows (except the fix's own CI step for its harness), permissions and
protocols are untouched by M1; the fix does not touch `src/`.

Combination: temp worktree `<private-path>`,
temp branch `tmp/m1-gnome-fix-check`, local merge commit `bb3113d`
(M1 `a21d0f8` + `31f0c36`), automatic ort merge, ZERO conflicts. Existing
branches, PR #1, master and Agent A's worktree untouched; nothing pushed.

Test on the combination (M1 venv reused read-only, no installs, no live):
- Full pytest suite: 393 passed, 5 skipped (display-dependent).
- STOP/RESUME + bridge + integration files explicitly: 82 passed.
- Rescan/stacking regression tests: pass.
- Ruff lint + format: clean. GJS status-bridge harness: PASS. GJS
  extension-rescan harness: PASS (re-adoption, no double adoption,
  disable releases, overrides restored).
- gettext: all 6 catalogs compile (including completed Play/Pause).
- Worktree clean after tests (no test pollution).

Excluded from any integration: `build/`, `dist/`, `.venv/` (all git-ignored
artifacts, logs and evidence roots), `tests/manual/` (preflight/live
harnesses and session supervisors), `tests/test_m1_isolation.py`,
`tests/test_m1_native_session.py`, `tests/perf_m1.py`, docs and notes.
Proposed integration payload = exactly the merge-base diff file list for
`src/`, `po/`, `gnome-extension/` (fix side), `tests/test_*.py`
(43 files, no packaging/manifest/permission/workflow changes).

Residual risks:
1. UX two-button change (6c171f0) validated only headless + one graphical
   smoke of the *previous* layout; the new STOPPED-disabled/refresh logic
   has no live-session confirmation yet (explicitly out of scope here).
2. Anomaly A verdict (NOT REPRODUCED) is unchanged by this combination;
   no comparative live runs were performed in this phase.
3. The temp merge commit is verification-only and must not be pushed or
   merged into existing branches.

Recommended strategy: when authorized, fast-forward or PR the integration
branch with the M1 file set (production: 11 `src/` files + 7 `po/` files;
tests: 4 `tests/test_*.py` files; docs as decided), keeping the fix commit
as-is; PR #1 needs an update ONLY if it is the vehicle chosen for that
integration (no change made here, no assessment of PR #1 contents made).
Alternative: keep M1 separate until a live session confirms the new
transport buttons. Either way, delete the temp worktree only after the
decision (it is the only place holding the verified combination).
