# M2 — Playback Policy Engine: design (no code yet)

Base: `a7d34a8` (M1 + GNOME reload fix). Worktree
`<private-path>`, branch `feat/gnome-playback-policy`.
Reference: roadmap `docs/plans/gnome-integration-evolution.md` §6 (+ M2
milestone). This phase is design-only: no production file is modified.

## 1. Decision points today (where Apricus starts/pauses/resumes/stops)

| # | Location | What decides today | Problem for M2 |
|---|---|---|---|
| D1 | `server.py:433` `pause_playback()` | unconditionally forwards to player | no latch, no STOP awareness at call site (relies on player absence) |
| D2 | `server.py:439` `start_playback()` | early-returns only on `is_stopped` | manual pause is not persisted; after restart it is lost |
| D3 | `server.py:597` `is_paused_by_user` | live mirror of player attribute | volatile, player-local, no disk latch |
| D4 | `player/video_player.py:658` `_should_playback_start()` | maximize prefs + `is_paused_by_user` | in-player policy; knows nothing of STOP intent, mute, or other reasons |
| D5 | `player/video_player.py:618` `_on_active_changed()` | screensaver lock → pause; unlock → maybe resume | resident in player; no manual-vs-auto distinction beyond D4 |
| D6 | `player/video_player.py:627` `_on_window_state_changed()` | maximize/fullscreen → start/pause or `volume_fade(0)` | resident in player; auto-mute path bypasses any latch |
| D7 | `player/video_player.py:1076` `start_playback()` | re-checks `is_stopped` + D4 | last guard before fade; still player-side |
| D8 | `player/web_player.py:405` `pause_playback`/`start_playback` | no-ops | web ignores pause/stop/mute entirely |
| D9 | `gui/gtk4app.py:4319` `on_play_pause` | toggles `is_paused_by_user` + pause/start verbs | duplicate decision logic in GUI (also `menu.py:64`) |
| D10 | `gui/gtk4app.py:4311` `on_stop_resume` + `menu.py:72` | M1 STOP/RESUME verbs (latched + persisted) | correct; the pattern to generalize |
| D11 | `player/video_player.py:889` `_play_queue_item` / EndReached advance | advances unless STOPPED (`_on_media_end` checks latch) | paused-state advance not gated on a central output |
| D12 | mute path | `is_mute` key is manual-only; maximize-mute uses direct `volume_fade(0)` | separation exists but is incidental, not enforced |

Consequences today: manual pause evaporates on restart (req 7 violated),
auto paths can only see one reason at a time, web ignores everything, and
the GUI duplicates toggle logic.

## 2. State and priority model

Single server-owned engine; players/GUI/menu become intent sources and
actuators, never deciders.

- **Latches (persisted, manual-only, never auto-cleared):**
  `manual_stop` (existing M1 `is_stopped` key — kept as-is, see §4),
  `manual_pause` (new `policy_manual_pause`), `manual_mute` (existing
  `is_mute` key, semantics unchanged).
- **Auto reasons, M2 set:** `FULLSCREEN`, `MAXIMIZED` (from the existing
  window-state backend, X11 Wnck path unchanged), `SCREEN_LOCKED` (existing
  `ActiveHandler`). M3/M4 producers (extension push, blank, suspend) are
  out of scope but feed the same `reason_active_changed()` entry point.
- **Output:** `compute()` → `{desired: PLAY|PAUSED|STOPPED, wanted_mute}`.
  Priority: `MANUAL_STOP` > `MANUAL_PAUSE` > auto-STOP (reserved, none in
  M2) > auto-PAUSE (any active). STOP dominates PAUSE.
  `wanted_mute = manual_mute OR any(auto_mute_active)`; the `is_mute` key
  stores only the manual latch, byte-identical through auto cycles.
- **Intent vs actual (req 8/10):** the engine computes *desired* state;
  `player._is_actually_playing()` stays the ground truth for *reported*
  state. `playback_state` never reports `playing` unless the player
  confirms frames; mismatch surfaces as `paused`/`unavailable` plus the
  existing `playback_error` channel — never a fake `playing`.

## 3. Transition matrix (global; per-monitor deferred to M7)

| Latches | Auto reasons | Output |
|---|---|---|
| STOP latched | any | STOPPED (fresh selection still allowed, never plays) |
| PAUSE latched, no STOP | any | PAUSED |
| none | none active | PLAY |
| none | any pause-reason active | PAUSED |
| none | mute-reason active only | PLAY + muted |

Core flows: STOP → destroy player (existing M1 path), resume only via
explicit `resume_playback` (M1 verb kept); PAUSE → keep player, fade;
resume → recompute; wallpaper/mode/playlist/stream/web switches while
STOPPED update selection and stay stopped (M1 behavior generalized);
playlist EndReached advances only when output is PLAY (holds position
otherwise, no index churn); mute toggles never touch play state and vice
versa; all transitions idempotent (same event twice = single transition,
single emission); server restart reloads latches → starts PAUSED/STOPPED
with the static frame already applied, autos dropped.

Edge cases (from roadmap §6.6, M2-scoped): pause-during-fullscreen +
unlock ⇒ stays paused; stop-during-lock + unlock ⇒ stays stopped; auto
mute + manual unmute ⇒ muted until all autos clear AND latch off, key
untouched; rapid reason flicker ⇒ 250 ms debounce, no fade spam; suspend
during STOP ⇒ stays STOPPED (no suspend producer in M2 — behavior
unchanged from today, explicitly not claimed); double pause/stop ⇒ single
fade, no orphan VLC (assert one `VLCWidget` per window); missed
ActiveChanged ⇒ reconcile on player reconnect/startup; extension absent ⇒
freeze autos, no surprise resume, diagnostics flag; web parity for
pause/stop/mute; config reload while stopped never autoplays.

## 4. Minimal architectural changes (proposal, NOT implemented)

- NEW `src/apricus/policy.py` (<600 lines per roadmap): `Reason` set,
  `PlaybackPolicyEngine` (latches + auto dict + `compute()` +
  `request_manual_*` + `reason_active_changed()` + `snapshot()` +
  debounce + `extension_lost` freeze), pure logic with an injected
  persist/apply/clock seam for headless tests.
- `server.py`: own the engine; route `pause/start/stop/resume_playback`
  through it; `_apply_policy_output` maps to EXISTING actuators only
  (fade verbs, M1 destroy/recreate paths, static fallback); `is_paused_by_user`
  becomes an engine-latch mirror (persisted); additive read-only
  `engine_snapshot` (+ `PolicyChanged` signal as specified — additive,
  v1 intact); `snap_ipc.py` mirrors new names only.
- `player/*`: move `_on_active_changed`/`_on_window_state_changed`
  subscriptions to the server (X11 Wnck backend unchanged, only re-owned);
  player keeps fade application + `_is_actually_playing` reporting;
  `_should_playback_start` becomes a thin `engine.compute_desired()`
  query; wire web pause/start to freeze (no new WebKit APIs).
- `gui/gtk4app.py` + `menu.py`: keep calling server verbs (no new logic);
  read `engine_snapshot` for badge/disabled states instead of ad-hoc
  `is_paused_by_user` probes (small, contained).
- Persistence: keep M1 `is_stopped` as the manual-STOP latch (no
  dual-source-of-truth, no migration risk — deliberate, documented deviation
  from roadmap §6.4 naming); add `policy_manual_pause`,
  `policy_manual_mute`, `policy_version=1` via existing atomic
  `ConfigUtil` write + `.bak`.
- Explicitly OUT: extension `state.js`/push subscription (M3), blank/sleep
  listeners (M4), per-monitor policy (M7), bridge/AppArmor/manifest/sandbox
  changes, v1 D-Bus breakage, renderer changes, SSRF-guard changes.

## 5. Risks

- X11 fade timing: decisions cross D-Bus instead of in-process idle
  callbacks (mitigation: application stays in-player; only the decision
  moves; Wnck traces compared pre/post).
- Single-latch migration: old volatile pause becomes persisted (intended
  behavior change per req 7; GUI copy must not fight it — same file-lock
  discipline as M1).
- Over-centralization temptation: engine decides, never animates/fades.
- Scope creep into M3: the reason feed exists, but only X11/poll/lock
  producers are wired.

## 6. Regression tests (to write on implementation)

New `tests/test_policy_engine.py` (headless matrix): every §3 row;
idempotency incl. single-emission asserts; manual-prevails-over-all-autos;
auto-mute cycle with `is_mute` byte-identical; crash recovery (latches kept,
autos dropped); missed-event reconcile; extension-lost freeze; web parity
stub; debounce (flicker ⇒ one actuation). Updates: GUI headless tests for
snapshot-driven buttons; server verb-routing tests; player
compute-desired query tests; Wnck trace-identity test; existing
`test_stop_resume.py` / `test_playlist.py` / `test_backends.py` suites keep
passing unchanged (STOP/RESUME/playlist/static/volume/mute behavior is
preserved by construction).
