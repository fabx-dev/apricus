# Strict Snap stream resume — continuity correction, 2026-11-01

## Current verdict: x7 rejected; native HTTP candidate awaiting sideload

The user observed that installed x7 restarted from a different point. Its
continuity acceptance is **FAIL**, superseding any earlier inference that
`set_time()` plus advancing time/display counters proved restored content.

### Confirmed operational cause and narrower correction

The earlier Snap-specific AVIO routing made both video/audio inputs report
non-pausable. VLC 3 stops such an input on pause. Reopening it and seeking did
not provide acceptable continuity. The new candidate removes AVIO routing and
restores VLC's native HTTP/TLS and genuine input-preserving PAUSE.

Native TLS previously failed. A syscall comparison **inside the same strict
Snap** isolated an actionable difference: connected TCP `sendmsg()` sent its
payload successfully, while `sendmsg(MSG_FASTOPEN | MSG_NOSIGNAL, peer)` returned
`EACCES`. VLC 3.0.20's `src/network/tls.c:vlc_tls_ConnectWrite()` falls back to
ordinary `connect()` for `EOPNOTSUPP`, but not for this error. The exact kernel
policy layer returning the denial is not established; no hardware/decoder
diagnosis is inferred from it.

`src/compat/vlc_fastopen.c` preserves the failed operation and reports only
denied TCP Fast Open as unsupported to VLC, selecting **VLC's existing normal
TCP fallback**. It does not open sockets or grant permissions. Ordinary denied
writes, other errors, UDP/Unix sockets and successful writes retain their
results. Tests pin these boundaries. HTTPS then opened successfully with
`can_pause=1`, `seekable=1`; bare Play resumed at the same paused time.

The helper is built only in Snapcraft's Meson environment and installed under
`usr/lib/apricus/`. Before the forkserver/player interpreters start,
`snap_vlc.prepare_snap_vlc()` adds its immutable Snap path to their `LD_PRELOAD`,
preserving other entries. Missing packaged compatibility fails loudly. Native
and Flatpak startup do not change their environment or build this helper.

No relay, callback transport, `start-time`/demux override, new listener, new
permission, manifest change, or security-profile change is used in production.
Those transport/reseek prototypes were rejected. A host-side observation of the
strict diagnostic process confirmed `snap.apricus.apricus (enforce)`, seccomp
mode 2 with two filters, and the compatibility library mapped at the same time.
The kernel still rejects Fast Open; no AppArmor/seccomp policy is weakened.

### Content-based validation, not clock/counter-based inference

The previous oracle was wrong in two ways: a seek can echo its requested time
before output replays an earlier prefix; and VLC can increment its displayed
counter while repeatedly displaying the **same paused picture**. In the test
sample `get_time()` also lagged decoded output near EOF (frame 449 of 450 was
reported around 9 s). None of these values alone proves continuity.

The replacement diagnostic compares the paused decoded pixels and the **first
different decoded picture** after Play with the same fixed 15 s YouTube source
decoded independently. It includes the real audio slave, production
`PlayerWindow.play/pause` methods and the strict installed Snap runtime with
worktree code and the helper preloaded. One bounded reference download is a
diagnostic oracle, **not a production download/cache feature**.

Six successive pairs matched frames **90→91, 180→181, 270→271, 360→361,
449→0 (natural EOF), 90→91**. First-new-picture times were **51–71 ms**. A
separate 30 s pause matched **90→91 in 61 ms**. Scaled-RGB reference matching is
approximate (a small neighbouring-frame tolerance is explicit in the script).
An independently numbered-pixel network source gives an exact oracle: after
holding frame **66 for 30 s**, the first new frame was **67**, with the same
input and no seek/recreation. One already-submitted picture may complete as
pause takes effect; subsequent repeated pictures are not treated as progress.

These tests do not measure full Shell/wallpaper presentation latency or audible
mute. Real installed-app manual/fullscreen/maximized continuity, GUI open and
closed, remain **PENDING**. No release approval is implied.

Final automated run for this candidate: **606 passed, 5 skipped, 28 warnings**.
The prior pipe-readiness bad-FD warning remains outside this fix. Ruff passed.
The new tests exercise the C interposer against a deterministic syscall stub,
including unchanged non-TFO/other-socket denials; Snap bootstrap ordering and
preload idempotence; native video/audio URL preservation; rejection of repeated
paused pictures and replayed-prefix false positives; and the GUI-independent
server manual/fullscreen/maximized path with genuinely pausable input. Meson
configuration/compilation confirmed the helper target exists for Snapcraft's
environment and is absent from an ordinary native build.

Reproduction sources: `tests/manual/snap_youtube_frame_continuity.py`,
`tests/manual/stream_resume_probe.py`, `tests/manual/stream_resume_fixed_probe.py`.
The latter is now only a transport/capability check, not a content verdict.
Evidence under `/tmp/opencode/stream-resume-analysis/` includes
`fastopen-syscall-proof.log`, `native-http-fastopen-fallback.log`,
`strict-profile-with-fallback.json`, `native-youtube-six-frame-cycles.log`,
`native-youtube-30s-hold.log`, and `native-marked-next-frame-30s.log`.

## Historical x7 candidate below — not current acceptance evidence

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

## Historical x7 correction (rejected for continuity)

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

The historical updated `PlayerWindow` diagnostic ran in the installed strict Snap
with **worktree code**, not a newly installed full application. Six consecutive
cycles reached the requested time plus 250 ms and increased LibVLC's
displayed-picture counter, without playback error. **This did not prove restored
content; x7 subsequently failed user-observed continuity.** These obsolete
timings are retained only as an audit of the rejected criterion:

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

## Historical automated verification (x7)

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
