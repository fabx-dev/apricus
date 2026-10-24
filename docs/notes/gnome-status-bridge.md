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

| Check | Current result |
|---|---|
| Python suite | PASS: 349 tests, including 26 bridge cases; final review adds unauthorized-state preservation, ownership epoch and receipt-priority cases |
| GJS client lifecycle harness | PASS: coalescing, renew, stale-owner replies, stop races, bounded retries and snapshot-error recovery |
| ZIP + host installer includes helper | PASS roundtrip |
| Real Shell sender under strict | PASS: sender equals daemon-reported Shell owner, UID/PID match; enforce profile verified |
| Independent same-UID GJS | PASS: identical registration rejected |
| Automatic ACTIVE with zero wallpaper | PASS; no Check again |
| Disable/enable and GUI/server restart | PASS real automatic transitions; fresh generation/reconnection |
| Ownership changes during authentication | PASS unit/private-bus tests; live Shell replacement not forced |
| Lease expiry under strict | PASS: GUI frozen 95.5s, Unregister intentionally timed out, UNKNOWN after unfreeze; enable restored ACTIVE |
| Real process crash | NOT TESTED as a deliberate crash; controlled freeze is not a Shell crash |
| Video/playlist/DING/Overview | PASS on x2: visible frames, two clips alternate, clickable/draggable icons, no wallpaper Overview tile, ACTIVE/adopted=1 |
| GTK4 timer regression | PASS: fixed `is_active()` API, 65s real GUI observation without traceback |
| Flatpak GUI | PASS smoke: isolated config, responsive pages and existing live detection, 65s without traceback; no new bridge service/permissions |
| Flatpak new protocol | NOT ENABLED/NOT TESTED; existing path retained |
| Native GUI install | NOT TESTED end-to-end in this round; unit/import/package checks cover compatibility, not a new install claim |
| Test Snap build and CI | PASS for runtime `97e3684`; links below |
| Real suspend/resume | BLOCKED: VM freezes with and without new bridge; clock simulations do not close this gap |

The lifecycle/expiry tests initially ran on x1 (`3663254`). The x2 change is
the GTK4 timer getter plus a regression test; bridge backend and client bytes
were unchanged and checked. x2 revalidated real registration/adoption, timer,
video, playlist, DING and Overview. Do not mislabel every earlier test as a
fresh x2 fault-injection run.

### CPU and message measurements

One 95s interval under strict: **3 accepted Update calls**, GUI CPU 0.03s
(~0.032% of one core), entire Shell CPU 11.28s (~11.9%). One 95s disabled
interval: **0 bridge calls**, GUI CPU 0.02s (~0.021%), Shell CPU 9.69s (~10.2%).
These are whole-process samples, not causal attribution of Shell cost; one
pair cannot establish a statistically reliable overhead. Daemon credential
queries/replies are excluded from the application-call count. Each accepted
request normally uses two GetNameOwner queries and one credential query.

An additional private-bus 150s experiment exercised timed expiry and recovery;
it is not evidence of confinement or genuine GNOME identity. Real 95.5s GUI
freeze results above are separate. A stalled GUI cannot repaint while frozen;
the observed UNKNOWN is after it resumes and checks expiry.

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
- **Distribution:** no Store name approval has been requested. GNOME 49/51,
  multiple monitors, hardware decode and 4K remain outside this runtime test.

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

The test Snap must be installed separately with preserved settings, then
the extension must be backed up **again** before changing its installed files.
Prepare a terminal-only restore script, verify the backup, and present exact
operations before touching the host extension. Every logout/login or actual
suspend needs user participation/authorization; never initiate it automatically.
No test is complete solely because a process exists or source looks correct.
