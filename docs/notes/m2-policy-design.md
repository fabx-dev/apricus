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

## 4. Minimal architectural changes (IMPLEMENTED as below)

- NEW `src/apricus/policy.py` (stdlib only, pure logic + injected clock):
  manual STOP/PAUSE/MUTE latches, auto reasons (`fullscreen`,
  `maximized`, `maximized_mute`, `screen_locked`; unknown and reserved
  auto-STOP names raise), `compute()` → `EngineOutput(desired,
  wanted_mute, active_reasons, generation)`, immediate manual verbs,
  debounced auto updates with `pop_due()`, push-channel freeze limited to
  push-fed reasons, JSON-safe `snapshot()`, `load_latches()` for restarts.
- `server.py`: owns the engine; all four verbs route through it;
  `_apply_policy_output` actuates EXISTING paths only (fades, M1
  destroy/recreate, stopped-background fallback) with per-(desired,
  wanted_mute) idempotency (mute-only changes never re-trigger fades);
  fresh players created by `_setup_player` are aligned via the
  `fresh=True` path (no re-issued start — they auto-started on creation;
  only the mirror, an optional pause, and the mute switch apply);
  `_feed_auto` + generation-guarded `_apply_policy_pending` feed X11
  lock/maximize sensors moved here from the player; additive read-only
  `engine_snapshot` (+ introspection XML). `is_paused_by_user` is now an
  engine-latch mirror (always a bool — this also fixes the old
  `None`-over-D-Bus wart); `is_mute` setter routes through the engine.
  `PolicyChanged` signal deferred: emissions are already single per
  transition via server-side dedup; a new bus signal is left to M6
  diagnostics to avoid an unreviewed API.
- `player/video_player.py`: subscriptions and `_on_active_changed` /
  `_on_window_state_changed` removed (single decider);
  `_should_playback_start` kept as a local veto; `_play_queue_item`
  honors the persisted manual-pause latch (same pattern as the M1
  `is_stopped` check); `_save_locked` gains `manual_pause` preservation
  mirroring M1's STOP latch (GUI direct saves can no longer clobber a
  pause). Removed dead `_setup_handlers_idle` plumbing and unused imports.
- `player/web_player.py`: pause hides the webview (frozen-frame visual via
  the applied static frame underneath), start re-shows it; honest
  `is_playing` flag; `set_is_mute` wired through the engine path. No new
  WebKit APIs. Residual: page JS timers may continue while hidden (same
  cost class as paused VLC allocations).
- `gui/gtk4app.py` + `menu.py`: NO changes needed — both already call
  server verbs (now engine-routed) and read `playback_state` /
  `is_paused_by_user` (now engine-backed with identical visible
  semantics). No new indicators (per UX constraints).
- Persistence: M1 `is_stopped` stays the manual-STOP latch (no parallel
  key — avoids dual sources of truth); added `policy_manual_pause` +
  `policy_version=1` with no-bump backfill; `is_mute` remains the single
  manual-mute key (no `policy_manual_mute` — the draft's extra key was
  dropped to avoid a redundant latch).
- Explicitly OUT: extension `state.js`/push subscription (M3), blank/sleep
  listeners (M4), per-monitor policy (M7), bridge/AppArmor/manifest/sandbox
  changes, v1 D-Bus breakage, renderer changes, SSRF-guard changes.

## 5. Risks

- X11 fade timing: decisions cross D-Bus instead of in-process idle
  callbacks (mitigation: application stays in-player; only the decision
  moves; a dedicated Wnck trace-identity test was considered but dropped as
  brittle — backend selection is covered by existing
  `test_backends.py`, and the moved subscriptions are byte-identical calls).
- Single-latch migration: old volatile pause becomes persisted (intended
  behavior change per req 7; GUI copy must not fight it — same file-lock
  discipline as M1, now also covering the pause latch).
- Over-centralization temptation: engine decides, never animates/fades.
- Scope creep into M3: the reason feed exists, but only X11/poll/lock
  producers are wired.

## 6. Regression tests (implemented)

New `tests/test_policy_engine.py` (18 headless matrix cases): every §3 row;
idempotency incl. single-delivery asserts; manual-prevails-over-all-autos;
auto-mute cycle with `is_mute` byte-identical; crash recovery (latches kept,
autos dropped); missed-event reconcile; push-channel freeze limited to
push-fed reasons; debounce (flicker never delivers PAUSE); JSON-safe
snapshot. New `tests/test_policy_wiring.py` (11 cases): server verb routing,
idempotent actuation, STOP-while-playing teardown, resume clearing both
latches, mute orthogonality incl. maximized-mute with untouched manual key,
debounced auto actuation + stale-generation drops, snapshot shape,
web pause/freeze/unpause, playlist advance held while paused.
Updated: `tests/test_server.py` (engine-backed `is_paused_by_user`, always
bool), `tests/test_config.py` (no-bump backfill), `tests/test_stop_resume.py`
(new `_last_applied` fixture field). Unchanged suites
(`test_playlist.py`, `test_backends.py`, GUI headless tests) keep passing:
STOP/RESUME/playlist/static/volume/mute behavior is preserved by
construction. Deliberately NOT added: a `PolicyChanged` bus-signal test
(signal deferred), live/Wayland/session tests (out of scope).
