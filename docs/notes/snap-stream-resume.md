# Strict Snap stream resume — local candidate, 2026-11-01

## Status and scope

The installed-app YouTube resume acceptance test is **PENDING**. This is not
release approval, a full M1–M3 campaign, or proof of imperceptible resume latency.
No manifest, interfaces, permissions, extension, remote workflow or publication
was changed for this fix. Store operations were not performed.

## Reproducible symptom and evidence

The installed strict Snap x6 could initially play the YouTube sample
`https://youtu.be/Z4F3AXvrLKo`. After manual/automatic pause it remained effectively
paused although the server wanted `playing`, with no manual hold or active
automatic reason and no reported media error. GUI icon/action fixes alone did
not repair the input.

A standalone comparison used the installed Snap's LibVLC 3.0.20, command chain,
strict confinement, GTK/X11 embedding and Apricus format selection. One format
resolution supplied all alternatives. The sample reported `can_pause=0` and
`is_seekable=1`. After `pause()`, its state became `Ended`, not `Paused`:

| Resume operation | Observation |
| --- | --- |
| `play()` | returned 0; still Ended, no time advance after 15 s observation |
| `set_pause(0)` | still Ended, no time advance after 15 s observation |
| `stop(); play()` on the same media/player | Playing and time advanced |
| rebind same descriptor, then play | Playing and time advanced |
| create a new descriptor from the same resolved URLs | Playing and time advanced |

These comparison deadlines are test observation limits, not production delays.
The first standalone runs accidentally used Wayland without successful X11
embedding; they are **not visual validation**. The final embedded comparison
(`vlc-final-comparison-embedded.log`) supersedes them.

Upstream [VLC 3.0.20 media_player.c](https://github.com/videolan/vlc/blob/3.0.20/lib/media_player.c)
corroborates the API lifecycle: `set_pause(1)` calls `input_Stop()` if the input
cannot pause; `play()` sends a state command when an input thread is retained;
`stop()` releases that thread. This explains the observed API-level failure
mode. **Why this resolved AVIO stream reports non-pausable, and any additional
decoder/network behavior, remain unproven.** Hardware, AppArmor and the sandbox
itself are not established root causes. Decoder warnings alone are not proof.

## Applied correction

- Capture non-pausable stream input state at pause completion. At the next
  authorized Play, release only the VLC input with internal `stop()` and replay
  the **same media descriptor and media player**. This is not Apricus manual
  STOP, does not spawn a process and does not resolve YouTube again.
- Save media time for seekable inputs; restore it on the new input's Playing
  event, on GLib's main loop. Non-seekable/live inputs reconnect without a
  promise to restore a historical position.
- Keep normal resume for pausable streams and local files. No elapsed-time
  heuristic, polling retry or arbitrary recovery timer is used.
- Resolve again only when a reconnect has an explicit expired signed URL, or
  a real playback error makes an explicit Play a retry. Bypass stale metadata
  only in that path. Resolve once per shared source, off the main loop; apply
  completion on GLib. Failure is visible, releases the busy state, and permits
  another explicit Play, without an automatic retry loop.
- Source generations, current play intent, manual pause/STOP and closing state
  invalidate/veto stale asynchronous completion. Signed URLs are not added to
  recovery logs.
- Serialize the existing fade steps on GLib instead of timer threads. A
  cancelled generation cannot finish by pausing a later Play; zero-volume
  fades complete. Existing duration/interval and local transport are retained.
- Explicit server Play may retry an effectively paused input even when the
  previous desired state was already Playing. Ordinary sensor reconciliation
  remains deduplicated; active automatic/manual holds still take precedence.

## Alternatives and timing

Rebinding/recreating media was not required for the ordinary pause failure.
Explicit unpause did not work. A separate prototype preconfigured a fresh
descriptor's `start-time` instead of seeking after Playing; it did not establish
a consistent latency advantage and is **not** the production path.

The final updated `PlayerWindow` diagnostic ran in the installed strict Snap
with **worktree code**, not a newly installed full application. Six consecutive
cycles restored seekable position, advanced media time by at least 250 ms and
increased LibVLC's displayed-picture counter, without playback error:

| Cycle | Request to restored/advancing/displayed criterion (s) |
| --- | ---: |
| 1 | 4.273 |
| 2 | 1.822 |
| 3 | 3.676 |
| 4 | 5.021 |
| 5 | 1.606 |
| 6 | 2.696 |

Initial first displayed picture in that run: 1.460 s, excluding resolution.
The initial and resumed criteria differ; this is not a controlled overhead
benchmark. Earlier frame-count runs reached roughly 6–10 s. Variability and
perceptible delay remain explicit acceptance reservations, not attributed to
the VM or networking without proof. The fix prevents the indefinite dead-input
state in this diagnostic; it does not prove sufficiently fast daily use.

## Automated verification

`584 passed, 5 skipped, 28 warnings` in the final full repository pytest run.
Ruff checks/formatting passed for changed code and probes. Warnings include the
previously observed pipe-readiness bad-FD test warning; it is not resolved here.

`tests/test_stream_resume.py` covers manual/fullscreen/maximized resume through
the real server/policy/player methods with a deterministic VLC lifecycle model,
without GUI; manual STOP then Play; repeated transitions; pausable/local fast
paths; zero-volume and stale fades; opening/pause and stale seek races; source
replacement; shared-source refresh single-flight; pause/STOP during resolution;
expired URL and actual playback-error retries; cache bypass; resolution failure
and explicit retry. These are automated regressions, **not live Shell tests**.

Reproducible diagnostics: `tests/manual/stream_resume_probe.py` and
`tests/manual/stream_resume_fixed_probe.py`. Local evidence is under
`/tmp/opencode/stream-resume-analysis/`, including
`vlc-final-comparison-embedded.log`, `vlc-final-production-window.log`,
`vlc-start-time-comparison.log`, and `pytest-final.log`.

## Still required after the user's sideload

Real installed-app YouTube manual pause/Play, fullscreen and maximized automatic
resume, with GUI open and closed; repeated cycles; perceived latency and desktop
safety. Do not mark the issue resolved before these pass. No unrelated M1–M3
campaign, Store upgrade, publication or M4 is authorized by this test plan.
