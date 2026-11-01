# Apricus 2.1.0 — Release Readiness

Local preparation, 2026-11-01/02. **NOT READY FOR PUBLICATION.**
PASS means the stated check passed within its stated scope; NOT TESTED is not
an implied pass. No external publication or privileged install is authorized.

## 1. Version and candidate commits

- PASS: version 2.1.0; planned tag `v2.1.0` (not created).
- PASS: functional freeze includes Streaming information commit `8e69974`.
- PASS: candidate source `e4d6223` (full hash in the local artifact directory's
  `SOURCE_COMMIT.txt`). Later readiness-report commits do not certify a new build.
  Full source ID: `e4d62237d1f74b6ff880627fa20c228624e6367b`.
- PASS: native HTTPS, immediate pause, restart/reseek fallback, audio/policy and
  rev6 GNOME bridge remain unchanged by release preparation. AVIO not restored.
  Read-only diff against `8e69974` is empty for application code, compatibility
  helper, packaging definitions and extension sources.
- PASS: publication gating and documentation prepared in `389766a`; `e4d6223`
  only formats the Streaming GUI tests for the existing CI check.

## 2. Tests and final installed-Snap acceptance

- PASS: full suite on candidate source: **617 passed, 5 skipped, 30 warnings**.
  Warnings include GI/Python deprecations and an ignored multiprocessing pipe
  destructor exception; they are recorded, not a warning-free health claim.
- PASS: Ruff lint over `src/ tests/`, CI format check (88 files), actionlint and
  `git diff --check`.
- PASS: GJS status bridge, actor rescan and window-state sensor harnesses.
  The sensor timer sample was ~252 ms; not end-to-end pause latency.
- PASS: seven real GTK4 Streaming-info checks on Xvfb, including mouse click /
  outside dismissal, Tab/Return/Space/Escape, wrapping and light/dark layouts.
  Also passed against the installed DEB on Ubuntu 24.04/libadwaita in a private
  display/session, not just the source venv.
- PASS: DEB and RPM server startup, second-launch handoff to a single server,
  graceful quit and no tracebacks in isolated Ubuntu 24.04 container smoke tests.
  RPM was installed with `--nodeps` on Ubuntu; this does **not** test Fedora
  dependency resolution. Initial media state was `unavailable` (empty profile);
  these startup checks do not establish decoded video, audible audio or M3.
- PASS: Flatpak build-tree startup smoke on a private bus/Xvfb: version help,
  server startup, second-launch handoff, single server and graceful quit. This
  was `flatpak-builder --run`, not a user installation or production-permission
  acceptance test. Private-bus portal/GVfs/Wayland-service warnings occurred;
  they are not suppressed or claimed to be resolved. Spawned private-session
  helper PIDs were checked absent after the test; user app data was not reused.

Historical x8/x9 user acceptance is retained as historical evidence only; it is
not certification of this final installed Snap artifact.

| Final candidate check | Status | Scope / remaining acceptance |
|---|---|---|
| Installed strict Snap launch | NOT TESTED | Requires interactive sideload |
| Local moving video | NOT TESTED | Verify visible content, not Playing state |
| Working YouTube video | NOT TESTED | Bounded visible-content and audible test |
| Audio and mute | NOT TESTED | Listen to actual output, mute/unmute and sync |
| Immediate pause | NOT TESTED | Manual click and automated pause on installed candidate |
| Correct resume | NOT TESTED | Compare content before/after hold; timestamps alone insufficient |
| Fullscreen/maximized | NOT TESTED | Real GNOME window transitions |
| Automation with GUI closed | NOT TESTED | Close control center, repeat transitions |
| Preference persistence | NOT TESTED | Relaunch installed candidate and compare saved values |
| Streaming icon | NOT TESTED | Installed Snap mouse/keyboard and light/dark check |
| No duplicate app/helper processes | NOT TESTED | Observe candidate lifecycle and graceful quit |

Keep this final acceptance bounded: one local A/V file and one working YouTube
sample; no renewed transport/client/UA campaign. Observe known problematic
`https://youtu.be/DFf37yQHDn8` separately if needed; do not label it solved or
substitute another video as proof that its audible playback works.

## 3. Packages, provenance and checksums

Local artifact directory: `../apricus-release-artifacts-2.1.0-e4d6223/` relative
to the repository. Only its `packages/` outputs are prospective release assets;
logs, extracted trees, temporary build state and private backups are not assets.
All four package formats and the Flatpak locale bundle were built locally.
Checksums below were verified with `sha256sum -c packages/SHA256SUMS.txt`.

| Artifact in `packages/` | Build / payload | SHA-256 |
|---|---|---|
| `apricus_2.1.0_amd64.snap` | PASS | `33750b7d00d0444088900b09606d2d5fb18c218bcfebf7b810a6c5100ee4dac0` |
| `apricus_2.1.0_amd64.deb` | PASS | `3942e24ac54463ecae0150a307c43689a81bc674edf39fdaae9bf6dceb1da901` |
| `apricus-2.1.0-1.x86_64.rpm` | PASS | `53792c834166aa5cdc0c65ee9b5299cfbbb9284fa1d3be018a60809e94508b28` |
| `apricus-2.1.0.flatpak` | PASS | `398903069433ee5c57c6e556633d86e3819263a58adbace64672e703c2c28951` |
| `apricus-locale.flatpak` | PASS | `85b61fc339ee29336a58ed7626abf5022385e0773ddf7508119cc8c424e74fb8` |

- PASS: DEB, RPM, Flatpak and Snap payload checks: all 34 installed application source /
  asset files match the candidate byte-for-byte; host extension sources match
  rev6 (metadata package version is separately 2).
- PASS: compiled application resources, original GSettings schema, desktop file,
  application icons and all five translated catalogs are present in the package
  staging/extracted trees. Flatpak translations are exported separately as locale.
- PASS: Snap `meta/snap.yaml` is byte-identical to installed x9. The Snap-only
  fallback helper is unchanged, SHA-256
  `acb9c221be681bc935f115650e2fe59cc9d558d623a5e1908e544f5cd27f4961`.
  Helper is correctly absent from DEB/RPM/Flatpak; those builds do not require
  this Snap-specific interposer. Flatpak keeps its existing VLC compatibility patch.
- PASS: metainfo validates offline (two informational control-relation notices)
  and desktop launcher validates. Package versions/architectures are 2.1.0 amd64
  for DEB/Snap and 2.1.0-1 x86_64 for RPM. No diagnostic logs/packages are embedded
  in the installed application-data tree.
- PASS: Flatpak manifest/permissions metadata matches the earlier built package.
  Main and locale bundles were imported into a separate local OSTree repository
  without installing the app; both imported commit IDs match the export repo.
  Main commit: `43d9f53d0f7df63898d37ce2914b3b989f6043db69a1740f3937dadf164cdf44`.
  Locale commit: `42cac74c679362b11dadeb02f5bc267f93fbdb4d7dee85713e6ad79097fd95d0`.

| Runtime/package acceptance | Status | Qualification |
|---|---|---|
| DEB startup + installed Streaming GUI checks | PASS | Ubuntu 24.04 container/private bus/Xvfb; no A/V acceptance |
| RPM startup | PASS | Ubuntu 24.04, `--nodeps`; Fedora dependency resolution NOT TESTED |
| Flatpak startup | PASS | Build-tree sandbox/private bus/Xvfb; installed filtering NOT TESTED |
| Strict Snap final installed runtime | NOT TESTED | Sudo sideload intentionally not executed |
| Final real-desktop playback/audio/automation parity | NOT TESTED | Blocking final-package acceptance, not inferred from source hashes |

All final packages must be generated from `e4d6223`, with submodule
`pkgs/flatpak/shared-modules` at `b8236c7961e3dd447b1b9605375a54a12f9f24b6`.
Byte-reproducible builds and identical runtime dependencies across distributions
are not claimed. DEB/RPM/Flatpak pin yt-dlp 2026.7.4, while x9 had 2026.10.26 and
Snapcraft currently installs unpinned yt-dlp/EJS. Record actual candidate versions;
do not silently update frozen dependencies to conceal this difference.
Verified Snap candidate: yt-dlp 2026.10.26, EJS 0.8.0; DEB/RPM/Flatpak: yt-dlp
2026.07.04, EJS 0.8.0, Deno 2.9.7. Snap Deno is pinned to the same 2.9.7 release.

## 4. Remaining issues and blockers

- NOT TESTED / BLOCKING: final installed-Snap acceptance, final package runtime
  parity and actual 2.0.7 upgrade acceptance remain open until verified.
- FAIL (known limitation, accepted for documentation): intermittent YouTube A/V
  stalls remain unresolved. The information icon is not a playback fix; no proven
  upstream root cause, universal continuity or recovery guarantee is claimed.
- NOT TESTED: physical hardware, Fedora GNOME visual behavior, GNOME 49/51 runtime,
  rigorous sensor-to-visible-pause latency <=500 ms, and screen-reader experience.
- NOT TESTED: a dedicated bridge lease/liveness release review beyond existing
  automated/historical validation. No permissions or architecture changed here.

## 5. Documentation and metadata

- PASS: README, CHANGELOG, release notes and metainfo describe immediate pause,
  rev6 extension/strict bridge and the unresolved streaming limitation.
- PASS: unpublished status is explicit; M4 is excluded.
- PASS: install examples distinguish Store stable from the local candidate;
  DEB/RPM and Flatpak examples retain their package-specific paths and runtimes.
- PASS: corrected stale `--version` documentation to the supported `--help`
  version description, verified against the built native launcher.
- NOT TESTED: final publication date/type approval. AppStream 2.1.0 is marked
  development for this local candidate; approve stable metadata and rebuild
  artifacts before tagging if promoting this candidate to a stable release.
- FAIL (cosmetic): existing Settings/Streaming screenshots predate final GUI
  changes; they are illustrative, not final-candidate acceptance evidence.

## 6. Safe upgrade from 2.0.7

- PASS: synthetic 2.0.7 schema round-trip tests exercise both mute states,
  playlist ordering, preferences, source/library paths, favorites and unrelated
  persistent-file preservation, plus a semantically intact config backup.
- NOT TESTED: actual DEB/RPM/Flatpak replacement, Snap revision data migration,
  autostart and Store-delivered 2.0.7 rev15 -> 2.1.0 upgrade.
- PASS: official Store rev15 Snap and its assertion were downloaded read-only
  into `upgrade-baseline/`, without installing them or moving any Store channel.
  Baseline package SHA-256:
  `a76153ff04c8ebd4845ed19f377a7ff1ce4ef9e785e7fee796a13a66856cd737`.
  The download alone does not validate an upgrade or local assertion acceptance.

Safe protocol (not executed): use a disposable VM clone with verified backups;
start from Store 2.0.7 rev15, populate playlist/preferences/favorites and hash
persistent data. Gracefully quit all app/helpers, preserve both revision-specific
Snap data and `common`, then sideload the exact approved candidate. Verify launch,
all user data and acceptance matrix, restart and compare again. Do not use purge,
uninstall/reinstall or an empty profile as an upgrade proof. A sideload exercise
does not certify Store assertions, channel delivery or interface auto-connection.

Store-delivered test requires separate authorization: upload an unreleased Snap,
obtain review/approval for the exact `gnome-status` D-Bus slot declaration if
required, then explicitly release only that revision to `candidate` (or another
approved non-stable channel). In a disposable VM installed from stable 2.0.7,
refresh to that channel and repeat data/lifecycle checks. **Stop before upload or
channel publication. Stable rev15 must not move.**

## 7. GitHub preparation and remaining publication steps

- PASS: candidate descends from sanitized PR source `1674b01`; no rewrite of
  private original repositories or attribution is needed.
- PASS: heuristic new-history scan (`1674b01..e4d6223`, 22 commits) found no
  credential signatures, signed media URLs or private user-home paths. The only
  current-tree home path is synthetic `/home/user/local.mp4` in a test.
- NOT TESTED: exhaustive secret audit; inherited public history is outside the
  new-history scan and retains previously documented legacy reservations.
  A supplementary heuristic scan of all 518 reachable commits found no key/token
  signatures or signed media URLs, but two inherited historical home-path
  occurrences. Thus absolute absence of historical personal paths is **FAIL**,
  not silently certified; no public ancestors were rewritten. Review that
  reservation before authorizing a push.
  Both commits (`362a5fb`, `9546a35`) are ancestors of existing public master
  `754d86c`; no new private history was introduced.
- PASS: an offline source bundle was created and verified for the candidate
  branch only, without backup refs or ignored diagnostic artifacts. It is local
  transfer material, not automatically a public release asset.
- PASS: readiness and final README/release-note corrections are a separate local
  documentation commit; candidate runtime/metadata/packaging sources stay at
  `e4d6223`. Worktree cleanliness is rechecked after recording this report.
- PASS: release workflow defaults `github_release=false` and
  `snap_store_upload=false`. Tag pushes build only; a GitHub Release requires a
  separate explicit dispatch on a version-matching tag. Snap upload has no
  `--release`; no automatic stable publication. Release assets are not clobbered.

After separate approval only: verify the actual remote/PR tip (the local
worktree's origin is a filesystem repository), publish only the approved branch,
run CI for the exact final source, review/merge deliberately, then create/push the
version-matching tag and explicitly dispatch publication. Never push `--all`,
backup refs, private bundles or build/diagnostic directories. A final CI rebuild
produces different artifacts unless checksums prove otherwise; certify those
artifacts, not earlier local checksums. No push, merge, tag, release or remote
workflow was executed in this preparation.

## 8. Snap Store publication steps (not executed)

After separate authorization and all blocking gates: verify candidate provenance,
strict manifest and hash; upload exact approved Snap without a channel; complete
Store review/declaration and the separately authorized test-channel upgrade.
Record the returned Store revision. Only after final user approval promote that
exact tested revision to `stable`; check revision/channel mapping and rollout.
No Store credentials, rulesets, stable revision, permissions or secrets changed.
PASS: read-only `snap info` check during this preparation reported stable 2.0.7
revision 15; no upload or promotion commands were executed.

## 9. Risks, rollback and stop point

Native HTTPS streaming instability is disclosed but unresolved. Package dependency
versions differ; headless smoke tests cannot prove real GNOME visuals or audible
A/V. New code must not be deployed concurrently with another Apricus instance.

Before sideload: gracefully quit the exact installed app, verify all owned
helpers exited, create a private consistent backup and record the installed
revision/connections. Stop for the user's interactive sudo command. Do not run
sudo through this session. The final command and pre-install evidence will be
recorded below after candidate verification.

- PASS: installed x9 gracefully quit through its exact IPC socket; subsequent
  confined-profile process scan found **zero app/helper processes**. x9 remains
  installed; candidate was not installed.
- PASS: a separate private backup of the full `snap/apricus` tree was created
  after quit, with all **165 regular files** and symlink targets verified against
  the quiescent originals. It is outside the repository and release assets.
  This protects x9 VM test data; it is not a backup or upgrade proof for Store
  2.0.7. Recheck process exit and refresh the backup if Apricus is reopened.
- PASS: all build containers used here are stopped. No Apricus service, new
  backend, persistent diagnostic process or application dependency was added.

**Stop here; user terminal only, not executed:**

```bash
sudo snap install --dangerous "$HOME/apricus-release-artifacts-2.1.0-e4d6223/packages/apricus_2.1.0_amd64.snap"
```

The home-relative path above identifies this VM's local artifact. If transferred,
reverify SHA-256 and use the actual VM path.

Rollback is an explicit, separately approved operation: preserve the previous
verified x9 artifact and data backup for VM testing; `snap revert` is only usable
if the desired revision/data are actually retained (not assumed). For Store
rollout, a re-promotion of rev15 requires confirmation that the Store still
retains it and explicit authorization. Neither binary rollback nor channel
rollback guarantees undoing data writes; restore a validated backup in the
disposable VM first. Never purge user data as a rollback shortcut.
PASS: retained x9 immediate-pause package was rehashed to
`9a867bbf92582c7fd56b3eb054256f4a556e674c89766df3664d2ac2da5bb3f1`.
