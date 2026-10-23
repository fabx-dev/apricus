# GNOME → Apricus status bridge (implementation under validation)

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
90/30 and watchdog costs still require confined real-session measurement.

Bridge state is authoritative after its first valid registration, including
UNKNOWN after invalidation: an old positive host receipt must not hide that
transition. If no valid bridge registration has ever arrived, the existing
Shell detection and freshness-bounded `status.json` fallback remain available.
Native/Flatpak do not start a competing service or get new permissions.

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

## Validation status (not a completion declaration)

| Check | Current result |
|---|---|
| Python state/auth/fallback tests | PASS locally |
| GJS client lifecycle harness | PASS locally |
| ZIP + host installer includes helper | PASS locally |
| Real Shell sender under strict | PASS for preceding isolated PoC; NOT YET this implementation |
| Automatic ACTIVE/disable/enable/GUI restart under strict | NOT TESTED for this implementation |
| CPU, actual messages, renewal loss and recovery | NOT TESTED |
| Real suspend/resume | NOT TESTED; clock simulation is not a real suspend |
| First video frame, playlist, DING, Overview | NOT TESTED for this implementation |
| Flatpak GUI regression | NOT TESTED for this implementation |
| Test Snap build and CI | PENDING |

The test Snap must be installed separately with preserved settings, then
the extension must be backed up **again** before changing its installed files.
Prepare a terminal-only restore script, verify the backup, and present exact
operations before touching the host extension. Every logout/login or actual
suspend needs user participation/authorization; never initiate it automatically.
No test is complete solely because a process exists or source looks correct.
