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

## 8. Validation matrix (per packaged variant, after install approval)

Avvio GUI/server, bridge/integration state (Snap slot; Flatpak has no
`--own-name` for the bridge bus name, so bridge telemetry is expected-limited
there), first visible frame (user-confirmed), STOP + resource release, RESUME
+ new adoption, static + fallback, STOP persistence, playlist, DING/Overview,
orphan check, config integrity, final restore. Benchmarks only after smoke
PASS; keep GNOME-packaged numbers separate from source/Xvfb numbers.

## 9. Current status

- Tests: PASS (392). Packaging diff: none. Snap build: PREPARED, gated on
  sudo approval. Flatpak build: RUNNING (log retained). Installs: NOT
  PERFORMED. No production/sandbox/permission changes.
