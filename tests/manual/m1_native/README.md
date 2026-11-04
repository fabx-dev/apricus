# Unconfined source M1 — preparation only

No Landlock, sitecustomize adapter, install target or application override is
used by these tools. `prepare_removal.py` creates a new private evidence directory
and archives state; it never quits Snap, changes settings or runs package actions.
`inspect_environment.py` only inspects dependency/source paths using the dedicated
venv with `-S`, avoiding even Ubuntu's Apport sitecustomize. It does not run M1.

## Current prepared recovery set

`<private-path>`

- `apricus_2.0.7_previous.snap`: retained candidate, SHA256
  `<artifact-sha256>`.
- `package-content-comparison.json`: all 8,708 regular file contents, 6,243
  symlink targets and 693 directories match the mounted x3 image. This does not
  replace the pending checksum of the installed root-readable compressed blob.
- `user-data.tar.gz`: full user Snap tree (revision/common/cache included),
  native Apricus/Hidamari configs, existing relevant caches/VLC data, and an
  evidence-only copy of the host extension. 131 readable regular files.
- `system-data-readable.tar.gz`: readable `/var/snap/apricus` state, 24 files;
  checksum in `additional-evidence.json`. No privilege was needed to read it.
- `manifest.json`, `runtime.json`: full configuration, playlist and original
  values; settings/inventory/absence records; no files are removed on backup.
- Complete relevant schema outputs and `dconf-dump.txt`: recovery evidence,
  **not an instruction to load the whole dconf dump**.
- `verify.py`: independent stdlib verification of retained files and archive
  readability; run with `/usr/bin/python3`, without GUI or venv.
- `preparation.sha256`: supplemental checksums for files added after the first
  manifest, including this procedure and the readable system-data archive.

## Privileged checkpoint — NOT performed

Ask the user to run only the following first, without removing anything:

```sh
sudo sha256sum /var/lib/snapd/snaps/apricus_x3.snap
```

Require exact equality to the retained package hash above. If different, stop:
the candidate must not be called the exact installed package. Preserve the
installed blob with a separately approved privileged copy and verify it before
any removal. Do not try to bypass its root-only mode through a block device.

## Subsequent removal — requires explicit user confirmation

1. Recheck inventory, live state and relevant settings against this backup.
   Because Snap remains active now, make a **new final checkpoint** if anything
   changed; never overwrite this recovery set.
2. Quit the authenticated Snap normally through its existing IPC. Verify server,
   player, descendants and any other native/Snap Apricus instances are absent.
   Do not signal an old PID without checking process start identity.
3. With separate user intervention, recommended independent snapd snapshot:
   `sudo snap save apricus`. Record its newly returned ID and verify using
   `sudo snap check-snapshot ID`. Never substitute one of the old saved IDs.
4. Only after explicit confirmation: `sudo snap remove apricus` (**no --purge**).
   Record the change result, snapshot ID and `snap saved apricus` output.
   Do not remove apricus-channel-poc, runtimes, host extension or DING.
5. Verify that only apricus is absent. Do not alter autostart/services or repos.

## Future source launch — not performed in this preparation

Use `<private-path>`, the source tree and real
HOME/config/runtime/bus/GSettings. Do not include the old adapter in PYTHONPATH
or use its live supervisor (it automatically reinstalls/restarts Snap, contrary
to the new user decision). The ordinary native config is
`<private-config-dir>/config.json`, now backed up; cache uses the normal
host cache tree. This is **not private-profile isolation**.

Use a minimal `-S` source launcher adding only venv `site-packages`, this source
tree and system GI paths, as the inspection probe demonstrates. No `site.main`,
sitecustomize, path redirection, application monkeypatch or filesystem sandbox.
The actual launcher will be prepared only after removal approval; child-import
propagation must be checked before starting playback. Preserve production
behavior, including its existing GTK3 XWayland selection on the real Wayland
session. Do not use `make install`, ambiguous PATH `apricus`, systemd enable or
autostart setup. No production code change without a reproduced bug/regression.

Smoke gate: visible frame confirmed by user, STOP/process/resource release,
static/fallback, RESUME/video return, native extension adoption, interactive
DING and Overview. Stop before deeper tests on decoder/renderer failure.
Only after smoke PASS run the agreed functional matrix and >=60s CPU windows,
separating VLC-playing and actually-visible timing. No package M1 tests.

## End-of-test restoration — no automatic Snap reinstall

1. Quit M1 normally; verify all descendants (including helpers) and service
   ownership are gone. Identity-scope any fallback termination.
2. Inventory new/changed files in the ordinary native config/cache/data paths.
   Preserve test-generated data in a new timestamped directory; do not delete
   personal media or broadly replace shared GTK/WebKit/user cache directories.
3. Stage archive extraction into a new private recovery directory. Restore only
   original application files known to have changed, preserving recorded
   absent/present state. Quarantine newly created application files instead of
   deleting broad paths. **Do not extract the whole archive over HOME and do not
   overwrite the evidence-only extension copy.** Shared VLC/WebKit state needs
   per-file review so unrelated user work is not overwritten.
4. Restore exactly the recorded background/settings that M1 changed, using
   `manifest.json` values and `gsettings set` for those keys only. Do not load the
   entire dconf dump. Compare relevant settings with the initial inventory.
   Until Snap is reinstalled, an original URI under `~/snap/apricus/x3/...` may
   be unavailable: report it and use an approved preserved static image only
   with the user's decision, never silently call that exact URI restoration.
5. Verify backup integrity and report all results plus any incomplete recovery.
6. **Ask whether to reinstall the old Snap or leave the source checkout available.**
   Never auto-reinstall or restart Snap under the previous supervisor policy.

## Optional reinstall, only after a later explicit decision

Verify the retained package hash and recovery set again, then (user privilege):

```sh
sudo snap install --dangerous <rollback-package.snap>
```

This reinstalls the exact old **payload/version**, once its installed-blob hash
has matched. snapd can assign a different local `xN` revision; do not promise x3
or force it with an unsupported revision option. Check the actual revision,
compare saved connections, and restore only apricus from the freshly verified
snapshot (`sudo snap restore ID apricus`) after reviewing its result. Reconcile
revision-relative paths and the preserved configuration with the current
revision; do not blindly extract x3 into a new revision or restore another Snap.
User-data and readable system-data archives are independent fallback evidence
if snapshot recovery fails; do not declare success without checking config,
playlist, volume/mute/static and exact media paths. Launch explicitly via
`/snap/bin/apricus -b` only after confirming M1 is gone; confirm visible wallpaper,
DING and installed-extension integrity with the user.

No reinstall/removal command in this document has been executed by preparation.
