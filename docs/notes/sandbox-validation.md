# Snap / Flatpak / native packages: validation procedures

Updated 2026-10-24, branch `feat/gnome-wayland-integration`.
Rule: never claim a runtime or clean-profile success without executing it.
The current protocol, evidence matrix and unresolved limits are in
[gnome-status-bridge.md](gnome-status-bridge.md). Earlier conclusions that
Snap could *never* show automatic Active are superseded by the verified
Shell → dedicated Snap slot direction; direct Snap → Shell queries remain
denied. No sandbox bypass is involved.

## 1. Snap — CI build and real strict validation

Local **compilation** still has these constraints; they are not blockers to
installing an artifact from the separate test-only CI workflow:

1. The repo builds the snap with `sudo snapcraft pack --destructive-mode`
   (see `.github/workflows/release-packages.yml`). Destructive mode
   performs privileged operations (bind mounts, build-snap installation,
   snapd state changes) and requires root.
2. The managed alternatives need daemons that are not present:
   no `lxc`/`lxd` binaries, no Multipass. The user is in the `lxd`
   group, but the daemon itself is absent.
3. Installing the resulting `.snap` requires
   `snap install --dangerous`, which requires root.
4. `sudo -n true` fails: passwordless sudo is unavailable and no
   interactive authentication exists in this session.

Packaging inspection and real installation were both executed:
` snapcraft expand-extensions` resolves to `confinement: strict`,
`grade: stable`, with standard desktop plugs (home, network,
network-bind, network-status, audio-playback, opengl, x11, wayland,
desktop, desktop-legacy, gsettings, screen-inhibit-control) —
no `personal-files`, no classic confinement, no broad filesystem
access. The new **session D-Bus slot** owns only
`io.github.fabxdev.Apricus.GnomeBridge`; it is not an outgoing Shell plug.
Store distribution of this name requires review/snap-declaration approval,
which has not been requested. Meson stages the guided-install helpers under
`share/apricus/gnome-extension/` (verified in DESTDIR staging,
executable bit preserved), matching the `$SNAP/...` lookup in
`src/apricus/gnome_shell.py`.

Runtime `97e3684` was built by
[artifact-only CI](https://github.com/fabx-dev/apricus/actions/runs/40075441639),
downloaded, SHA/content-verified, and installed by the user as local revision
x2. The real Shell sender, enforce profile, same-UID impostor rejection,
automatic state/reconnection and bounded lease were exercised; x2 additionally
passed video/playlist/DING/Overview and the GTK4 timer regression check.
The precise version split and limitations are in the bridge evidence matrix.

### Procedure for an isolated test environment (GNOME Wayland)

```
# 0. Preserve configuration and back up the complete host extension first.
#    Prepare a terminal-only rollback before replacing any installed file.
#    Use the artifact-only build on its supported Ubuntu 24.04 runner;
#    do not run destructive core24 compilation on this Ubuntu 26.04 desktop.
#    Verify artifact provenance, checksum and strict manifest before install.
# 1. Clean profile: fresh user, no ~/.local/share/gnome-shell/extensions/
#    apricus-integration*, no ~/.config/apricus, no dev install.
sudo snap install --dangerous ./apricus_2.0.7_amd64.snap
# Same command updates an already installed local snap. Do NOT use
# snap refresh --dangerous: refresh has no --dangerous option.
snap connections apricus
# Verify the installed manifest and the process's enforce profile too;
# a running process or manifest alone does not prove a successful bridge.
snap run apricus &
# 2. With no bridge and no usable host receipt, expect "Status unavailable",
#    NOT "Not installed": the snap cannot see the host extension directory.
# 3. Click Prepare → copy the shown command (runs entirely in the
#    sandbox; files land in ~/snap/apricus/common/gnome-integration,
#    the SAME path visible on the host by Snap design).
# 4. On the HOST (explicit user action, no sudo):
#      bash ~/snap/apricus/common/gnome-integration/install-host.sh \
#        --zip ~/snap/apricus/common/gnome-integration/apricus-integration@fabx-dev.zip \
#        --enable
#    Confirm: ~/.local/share/gnome-shell/extensions/apricus-integration@fabx-dev/
#    contains metadata.json + extension.js + statusBridge.js, identical to ZIP.
# 5. Log out/in (Shell discovers new extensions only at login).
#    Only the USER logs out/in. With the updated bridge extension and GUI,
#    expect automatic Active (read-only switch ON), without Check again.
#    Zero adopted windows is valid when no video is open.
#    If the bridge is unavailable, the bounded host-receipt path remains.
# 6. Start a video wallpaper: icons visible/clickable above video.
# 7. Update flow: install a newer local artifact; update host extension only
#    if its code changed, preserving backup/rollback; any logout stays manual.
# 8. Removal flow: `sudo snap remove apricus` → confirm the host
#    ~/.local/.../apricus-integration@fabx-dev REMAINS (documented,
#    must be removed manually per README), wallpaper gone.
```

## 2. Flatpak — validated locally, clean-profile pending

Validated on this machine (no root needed, no publishing):

- `flatpak-builder` full build from `pkgs/flatpak/` succeeds
  (all modules incl. VLC/ffmpeg/deno; one manifest fix required, see below).
- `flatpak build-bundle` exports a local `apricus-2.0.7.flatpak` (never uploaded).
- Bundle content verified: `bin/apricus`, vendored `deno`/`ffmpeg`,
  python `apricus` package, `share/apricus/gnome-extension/` with
  `install-host.sh` + UUID dir (matches the `/app/...` lookup),
  per-locale `apricus.mo`.
- Permissions reviewed (`finish-args`): network, ipc, dri, x11,
  wayland, pulseaudio, `xdg-videos:ro`, Shell/SessionManager/
  ScreenSaver/Login1 talk-names — all justified by features.
  `org.freedesktop.Flatpak` talk-name is REQUIRED and actively used:
  `src/apricus/backends.py` calls `flatpak-spawn --host gsettings`
  to read/write the host background (capture/apply/restore static
  wallpaper). It must NOT be removed.

Manifest fix applied on this branch: dropped the obsolete
`-Dlibdir=lib` for `totem-pl-parser` (commit `276968f`) — current
flatpak-builder injects `--libdir` itself and the duplicate flag
aborted the build. Rebuilt green after the fix.

For `97e3684`, rebuilt locally without extra permissions and installed only
the temporary `bridge-test` ref. The fresh-config GUI smoke passed: responsive
pages, existing live Active detection and 65s without traceback. This is NOT
a new full video/playlist validation of that Flatpak build. The Snap bridge
is not started there; its owned name remained absent. Temporary app removed
without `--delete-data`, previous config/data compared and preserved.

Isolation detail: passing `--env=XDG_CONFIG_HOME=...` to `flatpak run` did not
select the intended directory in this environment. The first launch was
invalidated and stopped; contents were unchanged, one cache-image timestamp
was restored. The corrected test uses `flatpak run --command=env APP ...`
to set the XDG variables inside the unchanged sandbox, with a preflight
`CONFIG_PATH` check BEFORE GUI startup. This adds no filesystem permissions.
Never claim isolation solely from the launch command; verify the actual path.

### Clean-profile procedure (needs a second user/session)

```
# Fresh user or VM, GNOME Wayland, flathub configured.
flatpak install --user ./apricus-2.0.7.flatpak   # local bundle only
flatpak run io.github.fabx_dev.Apricus &
# Settings → Desktop icons → expect "Not installed".
# Prepare → files land in
# ~/.var/app/io.github.fabx_dev.Apricus/data/gnome-integration
# (identical inside/outside the sandbox by Flatpak design).
# Run the shown command ON THE HOST (no sudo), log out/in,
# Check again → Active. Start video → icons above video.
# Removal: `flatpak uninstall io.github.fabx_dev.Apricus` leaves the
# host extension behind by design (README documents manual removal).
```

## 3. Native packages (.deb/.rpm)

- `.deb`: `pkgs/deb/build-deb.sh` builds cleanly (local + CI);
  contents verified (extension under
  `/usr/share/gnome-shell/extensions/`, locales, metainfo, desktop);
  `postinst` only refreshes caches (guarded, `|| true`);
  `--root-owner-group` ownership. Install/remove test NOT EXECUTED
  here (needs root + disposable system) — procedure: install the
  `.deb` in a fresh Ubuntu 24.04+ VM snapshot, `gnome-extensions
  enable apricus-integration@fabx-dev`, logout/in, verify; then
  `dpkg -r` and confirm `/usr/share/...` files are gone while a
  previously host-installed `~/.local` copy is untouched.
- `.rpm`: `%files` uses directory globs (no new files to list after
  this branch's changes), no `%post` scriptlets; builds green on CI
  (no local `rpmbuild`). Install test: same VM procedure with
  `rpm -i` / `rpm -e` on a Fedora/RHEL-family GNOME VM — NOT EXECUTED.

## 4. Snap: direct query limits versus the inbound status bridge

Direct legacy observations must not be confused with inbound bridge evidence:

- **installed**: extension files where GNOME Shell looks
  (`~/.local/share/gnome-shell/extensions/`). Verifiable from the
  sandbox: NO. The snap sees `~/snap/apricus/<rev>/` as HOME and
  the `home` interface explicitly excludes dotfiles
  (`owner @{HOME}/[^s.]*` patterns in the AppArmor profile), so
  `~/.local` is invisible by policy.
- **enabled**: `org.gnome.shell enabled-extensions` in dconf. Verifiable
  from the sandbox: only with the schema XML present (it is not
  shipped) AND via the `gsettings` plug's dconf access. Deliberately
  NOT wired: without Shell confirmation it cannot distinguish
  "enabled, waiting for login" from "enabled and loaded".
- **loaded via direct Shell API**: Shell reports state ENABLED(1).
  The legacy outgoing query remains unavailable:
  `org.gnome.Shell.Extensions` has no allow rule in the snap's
  AppArmor profile (verified: only `org.freedesktop.DBus`,
  portals, a11y, StatusNotifier peers); calls are denied, same as
  the ScreenSaver introspection denials in the logs.
- **bridge evidence**: the host Shell sends loaded/operational/adopted fields
  to a dedicated Snap service. This direction works under enforce policy.
  Operational means initialized/healthy self-report; adopted=0 is valid.
  Neither field proves decoded frames or visual icon ordering by itself.

Current consequences:

- With updated host extension + GUI endpoint, Settings shows automatic Active
  and adoption count; disable, enable, restart and lease expiry update it
  without Check again. It cannot toggle host enable/disable from the sandbox.
- If bridge evidence is absent, use the host-written `status.json` fallback
  (unsigned, 15-minute age limit). After a valid bridge registration in this
  GUI lifetime, stale receipt positives cannot mask bridge invalidation.
- `prepare_host_install()` and the installer also ship statusBridge.js;
  files land in the sandbox-identical export dir. Explicit host install and
  manual logout/login remain required for initial/revised extension loading.
- The `org.freedesktop.Flatpak` talk-name stays: `backends.py`
  needs `flatpak-spawn --host gsettings` for the static wallpaper.
- Schema staging (`gnome-shell-common`) was evaluated and REJECTED:
  it would only enable the dconf *enabled* read, which cannot prove
  *loaded*, while `integration_active()`'s enabled-fallback would
  then stand the legacy guard down on a mere "enabled" — less safe
  than today's guard-up behavior when the Shell state is unknown.
  Adding the dedicated inbound slot does not make that enabled-only fallback
  safer or add permission to query/control the Shell directly.

The lease is 90s with renewal every 30s. Duplicate or old-generation updates
cannot refresh it. A controlled 95.5s GUI freeze + timed-out Unregister led
to UNKNOWN on unfreeze and a fresh registration recovered Active. This was
not a deliberate process crash. Genuine suspend/resume remains BLOCKED by
the VM's same freeze reproduced with the original extension and no bridge.

## 5. Release approval gate (documented, not applied)

Recommended hardening for `fabx-dev/apricus` (Settings → Environments):

1. New environment named `release`, no wait timer.
2. Required reviewers: the maintainer(s) who may publish.
3. Deployment branches: `refs/tags/v*` only.
4. In `release-packages.yml`, add `environment: release` to jobs
   `build-snap` (Snap Store upload step) and `attach-to-release`.
   `build-deb`/`build-rpm`/`build-flatpak` only upload CI artifacts
   and need no gate; `workflow_dispatch` then builds without any
   publish path unless a tag job is also approved.

This was intentionally NOT applied here: it changes release
configuration, and the task requires documenting — not modifying —
release safety. Rulesets `Protect master` / `Protect release tags`
previously checked via API. This work uses only CI and the artifact-only PR
Snap job; no release workflow dispatch, tags, Store publication/approval requests, environment,
ruleset or secrets changes were performed. Their mere presence is not a
claim that future publication is impossible; release approval remains a
separate maintainer action outside this task.
