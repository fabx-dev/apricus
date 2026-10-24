# GNOME → Apricus status bridge: protocol and validation

This replaces neither the player RPC nor the receipt installer. No VLC,
actor placement, DING, fullscreen, power-control or Quick Settings behavior
is changed. The GUI starts this endpoint **only in Snap on GNOME Wayland**;
native and Flatpak retain their existing detection paths. Closing the GUI
removes the endpoint; the extension watches for its next appearance.

## Interface and identity

Session name `io.github.fabxdev.Apricus.GnomeBridge`, path
`/io/github/fabxdev/Apricus/GnomeBridge`, interface
`io.github.fabxdev.Apricus.GnomeBridge1`:

- `Register(u version, u sequence, b loaded, b operational, u adopted)`
  returns `(s generation, u leaseSeconds, u renewSeconds)`; version/sequence
  must initially be 1. A new Register supersedes the prior generation.
- `Update(s generation, u sequence, b loaded, b operational, u adopted)`
  reports changes or renews the lease with an unchanged snapshot.
- `Unregister(s generation, u sequence)` declares disable.

All requests are authenticated using **daemon-provided** sender/credentials:
`GetNameOwner(org.gnome.Shell)` == actual unique sender, credentials of that
connection have the current UID and a positive PID, second owner lookup
still matches. As the sender equals the owner, those UID/PID credentials
belong to both, not just another same-UID process. Owner-change epoch checks
reject in-flight old requests. Identity is revalidated on every request.
The server caps global pending requests at eight and permits one per sender.
There is no generic command, file path or arbitrary JSON argument.

This is NOT binary attestation or authentication of one particular extension.
Other code in Shell shares its connection. A same-user hostile process might
acquire the well-known Shell name while it is absent. Initial/new ownership
is therefore a session trust assumption, not cryptographic certification.
No PID, UUID, timestamp or identity claim from the client is trusted.

The client uses the shared session connection, async calls with 3s timeout,
one in-flight request, coalesced changes and no calls while Apricus is absent.
It addresses the observed unique service owner. Owner loss clears generation;
appearance registers a full snapshot. Replies from previous owners are ignored.
Failures trigger at most three registration retries (5/10/20s), then wait for
new ownership or a new local state change. No Snap → host signals are used.

## State and freshness

- `loaded`: extension code loaded but initialization did not establish an
  operational integration. Not disabled.
- `active`: initialization completed. `adopted` may validly be **zero**.
- `disabled`: authenticated Unregister was received.
- `unknown`: lease expired, bus/owner was lost, resume invalidated the state,
  or the bridge has no reliable evidence.

`loaded`, `operational` and `adopted` are distinct fields. None proves video
decoding, a first rendered frame, or every function's health. Runtime errors
in unrelated callbacks cannot automatically be detected by a timer.

The initial TEST configuration is lease **90 seconds**, Update renewal
**30 seconds** after the last successful update. Duplicate/out-of-order
updates, wrong generations or expired registrations cannot extend the lease.
New generations are random server-issued values, scoped to a GUI lifetime
and a registration; sequence restarts at 1 for a new registration/enable.
Register deliberately negotiates a new generation, not an anti-replay counter
across distinct extension instances. Other code already in Shell is not
isolated by this protocol.

The server uses CLOCK_BOOTTIME and checks lease expiry on each access and a
local one-second watchdog (no D-Bus polling). Comparing BOOTTIME and monotonic
clock offsets invalidates registration after suspend >2s, even before a
90-second deadline. Recovery requires Register, not a late renew. Timer
delivery can be delayed under load; expiry is always checked before using
state. Sub-two-second sleeps are still bounded by the BOOTTIME lease.

Disable sends Unregister before local teardown; an in-flight Register/Update
is followed by Unregister after acknowledgement. Lost messages, crashes or
snapshot failures cannot keep ACTIVE beyond the lease without valid renewal.
The extension's snapshot callback must remain healthy for renewal, but this
is a bounded **self-report**, not an independent liveness test of rendering.
90/30 is the initial test configuration, not a universally tuned performance
guarantee. Confined measurements and their limits are recorded below.

Bridge state is authoritative after its first valid registration, including
UNKNOWN after invalidation: an old positive host receipt must not hide that
transition. If no valid bridge registration has ever arrived, the existing
Shell detection and freshness-bounded `status.json` fallback remain available.
Native/Flatpak do not start a competing service or get new permissions.

"First valid registration" is scoped to the current GUI process. A restarted
GUI starts without bridge evidence and may use a still-fresh receipt until
the extension registers. The receipt is **not signed** and describes a past
host check for at most 15 minutes, not continuous liveness or identity. Its
timestamp remains visible in the UI. The 90-second bridge lease is not a
promise about this separate receipt fallback. Invalid or unverifiable requests
cannot promote state or extend a lease; a previously valid snapshot may remain
valid until its deadline unless an ownership/bus change invalidates it earlier.

## Snap Store

The manifest adds only a session `dbus` slot for the exact dedicated name,
attached to the GUI app profile. No outgoing Shell plug, AppArmor modification,
classic/devmode, privileged daemon or generic host permissions are added.
Store delivery of this owned name requires a reviewer-approved
**snap-declaration**. The documented procedure is upload and manual review;
no upload or approval request is authorized as part of these tests.
Local --dangerous installation is not proof of Store acceptance.

`dbus` auto-connect=no concerns confined client snap plug/slot connections;
the unconfined host Shell does not need a snap-connect step, as verified by
the earlier PoC. Name approval is not blanket permission to call Shell.
Parallel instances cannot share this fixed name. Flatpak ownership/filtering
must be tested before enabling this service there; currently unchanged.

References:
- https://snapcraft.io/docs/reference/interfaces/dbus-interface/
- https://github.com/canonical/snapd/blob/2.77.1/interfaces/builtin/dbus.go

## Validation record — 2026-10-24

Runtime code tested: `97e3684`, CI-built local Snap `2.0.7` revision `x2`,
Ubuntu 26.04, GNOME Shell/Mutter 50.1, Wayland, one virtual monitor, VirtIO/VirGL.
The final review changes documentation and regression tests only; it does not
change that tested runtime. This remains a Draft PR, not a published release.

### Final matrix: environments are not interchangeable

Here **x1/x2 mean local revisions of the `apricus` Snap**, not the separate
`apricus-channel-poc` package. Preceding transport/auth PoCs are supporting
history, not substitutes for tests of the production bridge implementation.
"Not repeated" means no fresh runtime execution in that column; identical
backend/client bytes do not turn an earlier result into another test run.

| Check | Snap x1 — `3663254`, real GNOME 50.1, strict | Snap x2 — `97e3684`, real GNOME 50.1, strict | Isolated bus — unconfined fixture, NOT GNOME Shell |
|---|---|---|---|
| Real Shell owner/UID/PID and Register | PASS; enforce profile checked | PASS; fresh Register from real Shell, enforce profile checked | NOT APPLICABLE to genuine Shell; fixture connection only |
| Independent same-UID GJS rejection | PASS, real request rejected | Not repeated; backend bytes unchanged | PASS private-bus roundtrip, including another connection of the same PID/UID |
| ACTIVE with no wallpaper | PASS, adopted=0, no Check again | Not separately repeated with no wallpaper | PASS fixture snapshot only |
| Disable → disabled → enable → ACTIVE | PASS, GUI transitions confirmed | Not repeated as a full live toggle cycle | PASS typed Unregister; unit/harness separately cover enable lifecycle |
| Appearance/reconnection of receiver | PASS: extension first, receiver start and restart | PASS on x2 launch after local update; not a separate x2 crash/restart campaign | PASS fixture/harness; not evidence of real confinement |
| 90/30 renewal and CPU sample | PASS: 95s active sample/3 Update; disabled sample/0 calls | NOT REPEATED: 65s GUI observation records 3 total Update, not a dedicated renewal/CPU sample | PASS separate timed experiment; metrics are not Snap/Shell costs |
| Expiry after missing renewal/Unregister | PASS: 95.5s controlled GUI freeze, UNKNOWN after unfreeze, enable → ACTIVE | NOT REPEATED | PASS controlled snapshot failure, expiry and recovery timeline below |
| Video/playlist/DING/Overview | Video and DING passed; GTK4 timer error discovered, then fixed in x2 | PASS: first frame, two clips alternate, icons clickable/draggable, no wallpaper Overview tile, ACTIVE/adopted=1 | NOT APPLICABLE |
| GTK4 timer regression | FAIL: `get_is_active()` traceback; retained as historical defect | PASS: corrected `is_active()`, 65s without traceback | NOT a GUI test |
| Actual suspend/resume | BLOCKED by VM; also reproduced without bridge | No further attempt; remains BLOCKED, not a PASS | Clock simulation only, not real suspend |
| Deliberate real Shell/process crash | NOT TESTED; controlled freeze is not a crash | NOT TESTED | Fixture exit/owner loss is not a genuine Shell crash |

Separate non-Snap checks:

- **PASS:** local Python suite 349 tests, including 26 bridge cases; GJS
  lifecycle harness; ZIP/host installer roundtrip; CI and artifact-only builds.
- **PASS in unit/private-bus tests only:** ownership changes during auth,
  ownership epochs, old generation/duplicate/out-of-order rejection and
  receipt priority. Live Shell replacement was not forced.
- **PASS Flatpak GUI smoke on `97e3684`:** isolated config, responsive pages,
  existing live detection and 65s without traceback. No dedicated Snap bridge
  service or new permission. New protocol inside Flatpak NOT ENABLED/NOT TESTED.
- **NOT TESTED:** newly installed native GUI end-to-end in this round;
  unit/import/package checks are not a native installation claim.

The x1 → x2 runtime change was the GTK4 timer getter plus a regression test.
Bridge backend and client bytes were checked unchanged; this supports the
version comparison, not relabeling x1 fault injection as fresh x2 coverage.

### CPU and message measurements

**Real GNOME session, Snap x1:** one 95s interval under strict:
**3 accepted Update calls**, GUI CPU 0.03s
(~0.032% of one core), entire Shell CPU 11.28s (~11.9%). One 95s disabled
interval: **0 bridge calls**, GUI CPU 0.02s (~0.021%), Shell CPU 9.69s (~10.2%).
These are whole-process samples, not causal attribution of Shell cost; one
pair cannot establish a statistically reliable overhead. Daemon credential
queries/replies are excluded from the application-call count. Each accepted
request normally uses two GetNameOwner queries and one credential query.

**Real GNOME session, Snap x1, expiry:** the GUI was deliberately frozen for
95.5s; the extension was disabled and Unregister intentionally timed out while
the Shell stayed alive. After unfreeze the user observed UNKNOWN, then a new
enable returned ACTIVE. This establishes bounded stale state in this scenario,
not an exact visible transition at t=90, and not a real process crash or system
suspend. A stalled GUI cannot repaint until it runs again.

**Real GNOME session, Snap x2:** 65s observation checked the corrected GUI
timer and current status/adoption; the log records one Register and three
total Update calls. These include possible state changes and are not all
classified as renewals. No new 95s CPU sample or 90s expiry injection was run.

**Isolated private bus only:** an earlier 151.2s experiment used the backend
from `745dbdf` and client snapshot-recovery fix later committed in `3663254`.
It did not use the genuine Shell or Snap confinement. Observed timeline:

| Elapsed | Fixture/server event |
|---|---|
| ~0s | Register, ACTIVE |
| ~30s | Accepted renewal Update |
| ~45s | Fixture snapshot marked broken; next renewal sends no Update |
| ~121s | UNKNOWN, about 91s after the last valid renewal |
| ~135s | Recovered client sends old-generation Update; rejected as expired |
| ~140s | Fresh Register, ACTIVE |
| ~145s | Unregister, disabled |
| ~150s | Fixture connection exits; owner loss, UNKNOWN |

Five application calls included one rejected late Update. The CPU figure
included the measurement driver and is not a GNOME/Snap performance measure.
The timing summary was retained in the local report; original transient `/tmp`
trace/driver files did not survive later VM reboots. Retained real-session
metrics and freeze records are listed below. No new experiment was run to
recreate missing logs during documentary closeout.

### Separate limitations, not features added by this bridge

- **VM suspend:** both baseline and bridge trials complete kernel s2idle, then
  the desktop becomes unresponsive. Logs show Mutter/SPICE display timeouts.
  No OOM evidence in the checked logs; root cause is not established. A
  reversible non-atomic-KMS trial also failed and was rolled back. No more
  suspend attempts are part of this review; no workaround remains installed.
- **Playlist title:** Now Playing intentionally displays `queue[0]`, so its
  heading is not live clip metadata even though playback advances. Recorded
  separately; no player/RPC expansion here.
- **Guided rows:** Prepare host installation also appears when disabled;
  this existing guidance is redundant when the bridge already proved an
  installation, not proof that extension files disappeared.
- **GNOME 49/51:** runtime NOT TESTED; metadata/static API review is not a
  runtime compatibility PASS. This is independent of the VM suspend blocker.
- **Snap Store:** reviewer approval/snap-declaration for the exact owned name
  is still required before distribution; no request was made. This is a
  distribution gate, not a failed runtime test or a reason to conflate the
  GNOME 49/51 and suspend gaps.
- **Other hardware:** multiple monitors, hardware decode and 4K remain
  outside this VM runtime test.

### Evidence and reproducibility

- [Runtime CI](https://github.com/fabx-dev/apricus/actions/runs/40075441631)
- [Artifact-only Snap build](https://github.com/fabx-dev/apricus/actions/runs/40075441639)
- Tested `.snap` SHA256:
  `fb39bc085f8e9e5d44e0273a5e1a18273ab88ff0cc7ed24e44362d92b35fc5b8`.
- Local validation folder `~/apricus-bridge-validation-20261024/` contains
  `RESULTS.md`, `validation-logs/`, `strict-idle-metrics.json`,
  `strict-disabled-baseline.json`, `freeze-lease-result.json`,
  `snap-x2-visual-tests.json`, `timer-fixed-observation.json`, and
  `flatpak-test-97e3684/observation.json`. These are local logs/user visual
  confirmations, not uploaded personal data. Large build trees were removed
  only after approval; final Snap, source scripts, logs and backups retained.
- Final machine state chosen by the user: test Snap x2 and tested extension
  retained, temporary Flatpak branch removed without deleting prior data.
  Terminal-only extension rollback and original byte-verified backups remain.

### Runtime identity and final machine confirmation

Documentary closeout rechecked installed revision **x2** against `97e3684`:
all 23 mounted Python source files matched Git bytes, mounted launcher and
manifest matched the retained CI artifact, and installed `extension.js`,
`statusBridge.js` and `metadata.json` matched that commit. The retained `.snap`
has the SHA256 above. Direct checksum of the root-owned installed archive was
not available without elevated access; no new sudo action was requested.

`git diff 97e3684 f514f9d -- src gnome-extension snap .github` is empty.
The changes through `f514f9d` are documentation/regression tests only, so the
installed runtime does **not** require another installation. This documentary
closeout likewise changes no runtime or installed settings.

At closeout: Snap `apricus` 2.0.7 x2 retained; extension code revision 4 and
DING ACTIVE; temporary Flatpak ref absent, prior data retained. Separate
`apricus-channel-poc` 0.1 x1 remains installed (not the production x1 column),
with its source/evidence kept. Original extension backups were rechecked for
contents, modes, ownership and mtime. `restore-extension.py` remains available;
no VM KMS override remains. The final artifact, logs, configuration backups
and demo videos used by the Snap playlist were preserved during approved
build-output cleanup. This is the user's chosen working state, not a claim
that the original extension was left installed instead of revision 4.

**Disposition:** technical review concluded; functional runtime frozen at the
tested code. PR #1 stays Draft/Open. Remaining validation/distribution limits
above are explicit; no merge, tag, release, publication or Store request.

The test Snap must be installed separately with preserved settings, then
the extension must be backed up **again** before changing its installed files.
Prepare a terminal-only restore script, verify the backup, and present exact
operations before touching the host extension. Every logout/login or actual
suspend needs user participation/authorization; never initiate it automatically.
No test is complete solely because a process exists or source looks correct.
