# Apricus 2.0 — RC2 Plan

Branch: `gtk4-migration` · Baseline: `70aa946` (RC1 `f37dd2d` preserved) ·
Status of this document: **OPEN** (plan only, no app-code changes).

Priorities: **P1** = must fix/verify, **P2** = should, **P3** = nice/future.
States: **OPEN**, **IN PROGRESS**, **BLOCKED**, **VERIFIED**, **DEFERRED**.
No item is VERIFIED without fresh evidence on the RC2 baseline.

## 1. Update 1.0 → 2.0

| ID | Pri | Description | Initial state | Rationale | Tests needed | Acceptance | Deps | Regression risk |
|---|---|---|---|---|---|---|---|---|
| RC2-UPDATE-01 | P1 | Isolated 1.0→2.0 migration run: copy a real-shape 1.0 config (v5, no stream/web keys) into a temp `XDG_CONFIG_HOME`, load+save with 2.0 code, verify values kept, keys backfilled, `.bak` written | VERIFIED (`test_update01_isolated_10_shape_migration`: byte-identical disk after load, `.bak` == original bytes, volume/mode kept) | Only static audit + unit tests so far; the real first-run path was never executed | pytest scenario with fixture config; assert `.bak` content == original bytes; assert disk untouched by `load()` alone | All assertions green, no access to real `~/.config` | — | Low (test-only) |
| RC2-UPDATE-02 | P1 | Corrupt-config recovery drill: truncated JSON + valid `.bak` → load recovers; garbage with no `.bak` → template, no crash | VERIFIED (`test_update02_garbage_without_backup_falls_back_to_template` + pre-existing backup-recovery test) | Recovery path declared but never drilled end-to-end | Isolated load() on both fixtures | Recovered values correct; no wipe; no traceback | — | Low |
| RC2-UPDATE-03 | P2 | Write-failure drill (read-only dir / full disk simulation viachmod): save raises, original intact, no `.tmp` left | OPEN (unit covers blocker-file case only) | Atomicity claim needs at least one realistic failure mode | Isolated save under `chmod 555` dir | OSError raised, original byte-identical | RC2-UPDATE-01 | Low |
| RC2-UPDATE-04 | P2 | Downgrade/recovery doc: 2.0 writes v5 + new keys; define what 1.0 does with them (ignores unknown keys?) and document rollback via `.bak` | OPEN | Users testing RC may revert; must not strand them | Manual code-read + note in README/docs | Documented, no code change unless a crash is found | — | None |
| RC2-UPDATE-05 | P1 | Second-boot idempotence: load→save→load→save produces byte-stable config and no `.bak` churn | VERIFIED (`test_update05_second_boot_is_stable`) | Guards migration loops | Unit test on mtime/content stability | Green | RC2-UPDATE-01 | Low |

## 2. Flatpak & playback

| ID | Pri | Description | Initial state | Rationale | Tests needed | Acceptance | Deps | Regression risk |
|---|---|---|---|---|---|---|---|---|
| RC2-FLAT-01 | P1 | Install RC build into an isolated test user/VM (never over the 1.0 install): app starts, panel opens, D-Bus names reply | IN PROGRESS (install forbidden on this machine; substituted `flatpak-builder --run` on `build-flatpak-rc2`: entry point runs, prints `Apricus v2.0.0-rc1`; D-Bus/panel run still pending) | Build ≠ runnable; install path never exercised | `flatpak install` from local repo/bundle into sandbox user; `gdbus` ping server; panel screenshot | Starts with no traceback | — | None (isolated) |
| RC2-FLAT-02 | P1 | In-sandbox Deno discovery: `yt-dlp` resolves `/app/bin/deno`, extracts metadata for a public test clip (e.g. Big Buck Bunny) | VERIFIED (`--run`: `deno 2.9.7`, `js_runtime_options()=={'deno': {'path': '/app/bin/deno'}}`, `get_formats(youtu.be/Z4F3AXvrLKo)` → 28 formats; ffprobe 4.4.8 present) | Bundled ≠ invoked | `flatpak run --command=sh` + `deno --version` + yt-dlp extract (metadata only, no download) | deno 2.9.7 runs; metadata OK | RC2-FLAT-01 | None |
| RC2-FLAT-03 | P1 | Playback: local video renders a frame; remote stream plays ≥10 s; pause/quit clean | OPEN | Core feature unverified in sandbox | Manual/automated run with timers | First frame + no freeze/crash | RC2-FLAT-02 | None |
| RC2-FLAT-04 | P2 | X11 vs Wayland sessions: panel + player under both (XWayland fallback expected for player) | OPEN | `GDK_BACKEND=x11` + wayland sockets need runtime proof | Run on both session types if HW available | Documented behavior per session | RC2-FLAT-01 | None |
| RC2-FLAT-05 | P2 | Error/fallback paths: offline sandbox (no network) → clean errors, static fallback, no hangs | OPEN | Timeouts/caps declared, offline behavior unknown | Revoke network, apply stream | Bounded errors, UI responsive | RC2-FLAT-01 | None |

## 3. Security

| ID | Pri | Description | Initial state | Rationale | Tests needed | Acceptance | Deps | Regression risk |
|---|---|---|---|---|---|---|---|---|
| RC2-SEC-01 | P1 | Entry-point inventory re-check on RC2 baseline: every `requests.*`/`extract_info`/ffmpeg/VLC-media call reachable from a URL | OPEN (RC1 map exists, must re-verify post-change) | New gates must cover all sinks, no stragglers | `grep` audit + checklist vs this table | All sinks listed with gate status | — | None |
| RC2-SEC-02 | P1 | Mock-based regression: creds/file/ftp URLs, redirect-to-local (mocked 302 → 127.0.0.1), oversized bodies, timeouts, malformed hosts — no socket for rejected, caps for accepted | OPEN (partial: unit gates exist for direct rejects) | RC1 tests cover direct rejects, not redirect landing | pytest with mocked transport | Green, zero real sockets | RC2-SEC-01 | Low |
| RC2-SEC-03 | P1 | Non-public-address mitigation on app-controlled HTTP paths (loopback, private, link-local, redirects, DNS resolution): implement bypass-resistant checks in `requests` probes and `_capped_get`; analyze external processes (yt-dlp, FFmpeg, VLC) separately — full mediation there is invasive, so document residual risk + achievable mitigation | OPEN (decision approved: implement where the app owns the socket, document where it does not) | Textual URL validation is not complete SSRF protection; redirect/DNS-rebinding bypass it | Mock-transport tests: direct + single/multi redirect to 127.0.0.1/10.x/169.254; userinfo/downgrade cases; legit public URLs unaffected | Direct paths refuse non-public landings with a clear message; external-process limits documented | — | Medium (false positives on legit LAN use — measure before tightening) |
| RC2-SEC-04 | P3 | DNS-rebinding analysis (resolve-pin would be architectural) | DEFERRED | Disproportionate for same-user desktop app | None for RC2 | Documented as known limit | — | None |

## 4. Localization

| ID | Pri | Description | Initial state | Rationale | Tests needed | Acceptance | Deps | Regression risk |
|---|---|---|---|---|---|---|---|---|
| RC2-I18N-01 | P1 | Re-run `make pot` + `msgfmt` + placeholder checker on RC2 baseline; confirm it 115/0-fuzzy, no regressions in de/fr/es/ja counts | OPEN | Counts are historical, must be re-evidenced | Commands + counts table | All compile; it unchanged or better | — | None |
| RC2-I18N-02 | P2 | Verify installed `.mo` in a fresh Flatpak build contain GTK4 strings (it spot-check) | OPEN (done once on rc1 build) | Packaging must include new catalogs | `msgunfmt` on built `.mo` | New msgids present | RC2-FLAT-01 | None |
| RC2-I18N-03 | P2 | GUI smoke per language where locale data exists (at least it + fallback EN) | OPEN (partial: EN fallback + no-locale runs) | Rendering with real translations unverified | Xvfb runs with `LANGUAGE=` set + screenshots | No traceback, no layout breakage | — | None |
| RC2-I18N-04 | P2 | Stable language goal (approved): English complete as fallback; Italian complete and verified; German/French/Spanish/Japanese to be completed and reviewed IF they remain officially supported. RC2 accepts partial-but-valid catalogs with working fallback. No unreviewed machine translations count as done | OPEN | Goal set by maintainer decision | Professional/linguistic review per language; placeholder + msgfmt checks | Goal met or support list reduced explicitly | — | None |

## 5. Packaging & docs

| ID | Pri | Description | Initial state | Rationale | Tests needed | Acceptance | Deps | Regression risk |
|---|---|---|---|---|---|---|---|---|
| RC2-PKG-01 | P1 | `xdg-videos:ro` impact note: custom `video_dir` outside `xdg-videos` invisible in sandbox; folder creation from app impossible — document in README/troubleshooting | OPEN | Behavior change vs 1.0 (`:create` → `:ro`) undocumented | Doc review | Note present and accurate | — | None |
| RC2-PKG-02 | P1 | Re-validate: `meson.build` version, `pyproject`/`uv.lock` sync, AppData release block + notes, manifest JSON, pypi-deps == pyproject deps (post-Pillow) | OPEN (must re-run after any change) | Single-source-of-truth drifted twice already | Parse + cross-check script/test | All equal, no 3.7 residue | — | None |
| RC2-PKG-03 | P2 | Release-notes draft for RC2 (features, fixes since RC1, known limits, install/rollback instructions) | OPEN | Needed before any tag | Doc review | Notes accurate, no invented artifacts | RC2-FLAT-01 | None |

## 6. Stability & tech debt

| ID | Pri | Description | Initial state | Rationale | Tests needed | Acceptance | Deps | Regression risk |
|---|---|---|---|---|---|---|---|---|
| RC2-DEBT-01 | P3 | Lazy-import server top-level GTK3 (`control`/`menu`/player) | DEFERRED (analyzed, risky) | Real but requires careful refactor; RC1 works around via processes | None for RC2 | Documented | — | High if rushed |
| RC2-DEBT-02 | P3 | `GDK_BACKEND=x11` global (forces XWayland on GTK4 panel) | DEFERRED | Tied to VLC-xid player design | RC2-FLAT-04 evidence first | Decision post-evidence | RC2-FLAT-04 | Medium |
| RC2-DEBT-03 | P3 | Tray libwnck/GTK3 legacy, `.tmp` hygiene, error-path polish | DEFERRED | Non-blocking, tracked | None for RC2 | Listed | — | Low |
| RC2-DEBT-04 | P1 | Full regression suite green on RC2 baseline (ruff, headless, Xvfb, msgfmt, manifest/appdata parse, GUI smoke) | OPEN | Mandatory gate before any tag | Full runs with counts | All green, counts recorded | All P1 fixes | None |

## Exit criteria

**RC2 exit (mandatory):** RC2-UPDATE-01/02/05, RC2-FLAT-01/02/03, RC2-SEC-01/02,
RC2-I18N-01, RC2-PKG-01/02/03, RC2-DEBT-04 green; RC1 tag untouched until then.
**Stable exit (mandatory):** real 1.0→2.0 end-to-end evidence, sandbox playback
evidence, complete regression suite, SEC-03 decision implemented, I18N-04 goal
met, all RC2 items VERIFIED.
**Optional:** RC2-FLAT-04/05, RC2-UPDATE-03/04 (recommended but not gating RC2).

## Tag strategy (approved, tags NOT created yet)

- Development branch: `gtk4-migration` (no merge assumed).
- RC1 candidate tag: `v2.0.0-rc1`, to point at the approved RC1 commit when
  the maintainer authorizes publication. Do not create it now.
- RC2 candidate tag: `v2.0.0-rc2`, to point at the approved post-regression
  commit when the maintainer authorizes publication. Do not create it now.
- No push, no merge, no release without explicit per-step authorization.
- Historical tags (`v1.0`, `v2.0`, `v2.0-2`, `v3.x`) are never renamed,
  moved, or deleted.

## Decisions recorded (approved by maintainer)

1. Private-IP/loopback: implement mitigation on app-controlled HTTP paths
   (RC2-SEC-03 upgraded to P1); external processes analyzed separately with
   residual risk documented.
2. Stable language goal: EN fallback + verified IT complete; de/fr/es/ja to
   complete/review if still supported; RC2 accepts partial valid catalogs.
3. Tag strategy: `v2.0.0-rc1` / `v2.0.0-rc2` candidates as above; nothing
   created, pushed, merged, or released without explicit authorization.
