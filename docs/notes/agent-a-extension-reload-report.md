# Technical report for Agent A — wallpaper window not re-adopted after external extension reload

Status: observation only. No code changed (M1 worktree, branch
`feat/gnome-policy-stop`). Extension component owned by Agent A; this file is
a forwardable note, not a modification of their branch, PR or worktree.

## Facts

1. Session: Ubuntu 26.04.1 / GNOME 50.1 Wayland. M1 Flatpak user install
   (`io.github.fabx_dev.Apricus` 2.0.7, locally built, strict user sandbox),
   video wallpaper playing and adopted.
2. Timeline (journal, `gnome-shell[<pid>]`, [validation date omitted]):
   - [event time omitted] → [event time omitted]: 12 × `adopted "apricus" into the background layer`
     (one per player window created by STOP/RESUME cycles during validation).
   - [event time omitted]: session `FileManager1` activation (`org.gnome.Nautilus`
     connecting to Tracker), then `[apricus-integration@fabx-dev] enabled,
     code revision 4`, then `startup scan: 1 windows, 0 wallpaper role
     match(es)`, then `Launching DING process` (+ DING/Nautilus/Gvfs
     interface lines).
   - [event time omitted] (after tester-issued RESUME created a fresh player):
     `adopted "apricus" into the background layer` again.
3. Trigger: a session-level file-manager/DING (re)initialization. The test
   performed no extension, Nautilus, DING, autostart or settings operations
   (only Apricus D-Bus playback calls and GSettings reads); the reload was
   external to the test.
4. Video state before reload: playing, adopted ([event time omitted]). After reload:
   still playing, but the live window was released to the normal stack by
   `disable()` and the scan did not re-adopt it (0 matches).
5. Effect on DING: the desktop Home icon (DING `show-home=true`) disappeared
   behind the video (user-confirmed: video animating, icon invisible).
6. Recovery without touching the extension: STOP destroyed the player window
   → icon visible again (user-confirmed). RESUME created a fresh window →
   adopted at [event time omitted] → video + icon coexist (user-confirmed).
7. M1 code does not touch this mechanism: zero M1-branch commits to
   `gnome-extension/`, `src/apricus/gnome_shell.py` or
   `src/apricus/player/base_player.py` (verified `git log`/`git diff` against
   the integration branch). The role constant, matching predicate and
   disable/release/scan logic are branch-identical. No runtime comparative
   data exists (no reload event occurred during native source runs), so this
   is NOT declared an M1 regression — it is reported as observed behavior of
   the shared mechanism.

## Hypotheses (not facts)

- H1: the scan's single window was a non-player window (e.g. terminal) and
  the player actor was temporarily absent from the actor list.
- H2: the player window was present but `get_role()` did not yet (or no
  longer) expose `apricus-wallpaper` at scan time.
- H3: even when matched later, nothing re-lowers an already-mapped window:
  the player's bottom restack runs on first frame, and adoption only happens
  on window-created/scan — a live window missed by one scan stays uncovered
  until the next player restart.

## Suggested diagnostics (Agent A's side, no change requested here)

- Log window title/role/wm-class in the startup scan line so a missed
  adoption is attributable.
- Consider adopting on role change (not only window-created + enable scan).
- Consider re-lowering adopted actors when DING (re)starts, mirroring the
  existing first-frame re-pin.

## Reproduction (needs a new authorized desktop run; NOT performed here)

1. Start video wallpaper (any backend), confirm adoption line.
2. Trigger an extension reload (e.g. DING/Nautilus restart or manual
   disable/enable of the integration extension).
3. Check the scan line: if `0 wallpaper role match(es)` while video plays,
   the live window is uncovered — desktop icons hide behind the video.
4. STOP (icons return) / RESUME (new window adopted) confirms the loop.
