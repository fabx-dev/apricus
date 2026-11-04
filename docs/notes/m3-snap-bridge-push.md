# M3 on strict Snap: bridge-carried window push (design + implementation)

## Why direct push cannot work under strict confinement

`ExtensionPushWindowStateBackend` needs two things the Snap sandbox denies:

1. `GetState()` calls on `org.gnome.Shell.Extensions.ApricusIntegration`;
2. subscribing to `DesktopStateChanged` signals owned by the Shell connection.

Snap AppArmor D-Bus mediation (see `src/apricus/snap_ipc.py` header) denies
arbitrary session-bus names; only declared slots are reachable. Reproducing
the native push inside the Snap would require a new `dbus` slot for the
extension's name plus a Store snap-declaration — a permission/manifest change
that is **not** made here.

## The sanctioned direction: Shell calls Snap

The status bridge already proves the allowed direction: the *unconfined*
Shell extension performs method calls on the **Snap-owned** well-known name
`io.github.fabxdev.Apricus.GnomeBridge` (slot `gnome-status`, Store-approved
exact name). The Snap side validates every call identically: sender must
equal the current `org.gnome.Shell` owner, plus per-connection UID/PID
credentials from the daemon.

The window push reuses that exact channel — no new slot, no new permission,
no manifest or snapcraft change:

- Extension (`gnome-extension/.../windowPush.js`, new): `WindowPush` watches
  the same bridge name. On every tracker emission it sends the settled frame
  as a method call: `WindowRegister(uubb) → (suu)`, then
  `WindowState(subb)`, `WindowUnregister(su)` on stop. UnknownMethod (older
  Snap without window push) quiets only this channel; the status bridge is
  unaffected.
- Snap side (`src/apricus/gnome_bridge.py` + `src/apricus/server.py`): the
  **server process itself owns the bridge name** (same declared slot, same
  app label: no manifest, slot or permission change, nothing for the Store
  to re-approve), so the push survives the control center being closed.
  `WindowRegistration` mirrors the `Registration` trust model
  (generation/sequence/lease, strict bool validation, suspend-aware clocks)
  on the same interface. `window_push_frame`/`window_push_lost` are
  same-UID authorized like every other mutating verb, drop
  duplicates/obsolete sequences, rebaseline on new generations, and feed
  the policy through the settled path (`snap-bridge-push` source, no second
  250 ms debounce). Loss freezes without auto-resume, mirroring
  extension-push loss. The control center reads the registration snapshot
  over Snap IPC (`gnome_bridge_status`) for its integration row instead of
  owning the name; it forwards nothing.
- GUI (`policy_support.py`, `gtk4app.py`): Snap + Wayland preferences are
  enabled with verified bridge-carried push, retained across transient
  loss like Flatpak. Native/X11 behavior is byte-identical.

## Honest limits (must stay in release docs)

1. **Not live-verified end to end.** All transport/auth/sequencing/policy
   paths are covered by unit, private-bus and GJS regression tests, but no
   strict-Snap install + rev6 extension + logout/login campaign has run here
   (install/login/logout are out of scope). The dedicated *new-slot*
   alternative (separate trust domain) still needs a slot plus Store
   declaration approval and is intentionally **not** implemented: the
   server-owned existing slot achieves persistence without it.
2. The extension revision moved 5 → 6 and `metadata.json` 1 → 2 for the
   protocol addition. Older extension + new Snap: no frames, old degraded
   path. New extension + older Snap: UnknownMethod tolerated, old degraded
   path. The ZIP (`build-zip.sh`) picks up `windowPush.js` automatically.
