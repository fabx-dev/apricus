# Snap / Flatpak / native packages: validation procedures

Status 2026-10-23, branch `feat/gnome-wayland-integration`.
Rule: never claim a clean-installation success without executing it.
Anything below marked NOT EXECUTED was not executed — follow the
procedure on adequate hardware to close the gap.

## 1. Snap — why local validation is blocked here

Precise blockers on this machine (no workaround attempted, as instructed):

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

Statically verified instead (no root needed):
` snapcraft expand-extensions` resolves to `confinement: strict`,
`grade: stable`, with only standard desktop plugs (home, network,
network-bind, network-status, audio-playback, opengl, x11, wayland,
desktop, desktop-legacy, gsettings, screen-inhibit-control) —
no `personal-files`, no classic confinement, no broad filesystem
access. Meson stages the guided-install helpers under
`share/apricus/gnome-extension/` (verified in DESTDIR staging,
executable bit preserved), matching the `$SNAP/...` lookup in
`src/apricus/gnome_shell.py`.

### Exact procedure for a dedicated VM (Ubuntu 24.04+, GNOME Wayland)

```
# 0. Fresh VM (GNOME Boxes/QEMU), GNOME Wayland session, user with sudo.
#    Snapshot BEFORE installing anything (to test removal later).
sudo snap install snapcraft --classic
cd ~/apricus   # feat/gnome-wayland-integration, clean checkout
sudo snapcraft pack --destructive-mode
ls apricus_*.snap
# 1. Clean profile: fresh user, no ~/.local/share/gnome-shell/extensions/
#    apricus-integration*, no ~/.config/apricus, no dev install.
sudo snap install --dangerous ./apricus_*.snap
snap info apricus | grep -i confinement   # expect: strict
apricus &
# 2. Settings → Desktop icons → expect "Not installed".
# 3. Click Prepare → copy the shown command (runs entirely in the
#    sandbox; files land in ~/snap/apricus/common/gnome-integration,
#    the SAME path visible on the host by Snap design).
# 4. On the HOST (explicit user action, no sudo):
#      bash ~/snap/apricus/common/gnome-integration/install-host.sh \
#        --zip ~/snap/apricus/common/gnome-integration/apricus-integration@fabx-dev.zip \
#        --enable
#    Confirm: ~/.local/share/gnome-shell/extensions/apricus-integration@fabx-dev/
#    contains metadata.json + extension.js (byte-identical to the ZIP).
# 5. Log out/in (Shell discovers new extensions only at login).
#    Settings → Check again → expect Active (switch ON).
# 6. Start a video wallpaper: icons visible/clickable above video.
# 7. Update flow: reinstall a newer snap, reopen Settings, re-run the
#    shown command, log out/in, confirm Active again.
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

## 4. Snap: why self-detection cannot confirm Active (verified, by design)

Four independent states must be distinguished:

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
- **loaded**: Shell reports state ENABLED(1). Verifiable: NO.
  `org.gnome.Shell.Extensions` has no allow rule in the snap's
  AppArmor profile (verified: only `org.freedesktop.DBus`,
  portals, a11y, StatusNotifier peers); calls are denied, same as
  the ScreenSaver introspection denials in the logs.
- **operational**: loaded + video actually below icons. Only the user
  can confirm visually.

Consequences (all verified live):

- The guided flow works up to host install + enable. The Settings row
  can never show Active from inside the snap; with the reorder in
  `integration_status()` it honestly reports "Status unavailable"
  instead of the previous misleading "not installed".
- `prepare_host_install()` + `install-host.sh` need no change: files
  land in the sandbox-identical export dir, the host command is
  copy-paste runnable (after the `set_content` clipboard fix).
- The `org.freedesktop.Flatpak` talk-name stays: `backends.py`
  needs `flatpak-spawn --host gsettings` for the static wallpaper.
- Schema staging (`gnome-shell-common`) was evaluated and REJECTED:
  it would only enable the dconf *enabled* read, which cannot prove
  *loaded*, while `integration_active()`'s enabled-fallback would
  then stand the legacy guard down on a mere "enabled" — less safe
  than today's guard-up behavior when the Shell state is unknown.
  No new permissions were added.

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
verified active via API; no workflow on this branch can publish:
feature-branch pushes trigger only the CI `pull_request` job.
