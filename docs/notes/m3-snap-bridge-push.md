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
- Snap GUI (`src/apricus/gnome_bridge.py`): `WindowRegistration` mirrors the
  `Registration` trust model (generation/sequence/lease, strict bool
  validation, suspend-aware clocks) on the same interface. Accepted frames
  are forwarded to the server over the existing Snap IPC socket
  (`window_push_frame`/`window_push_lost`, allowlisted in `snap_ipc.py`).
- Server (`src/apricus/server.py`): `window_push_frame` is same-UID
  authorized like every other mutating verb, drops duplicates/obsolete
  sequences, rebaselines on new generations, and feeds the policy through
  the settled path (`snap-bridge-push` source, no second 250 ms debounce).
  Loss freezes without auto-resume, mirroring extension-push loss.
- GUI (`policy_support.py`, `gtk4app.py`): Snap + Wayland preferences are
  enabled only with verified bridge-carried push, retained across transient
  loss like Flatpak. Native/X11 behavior is byte-identical.

## Honest limits (must stay in release docs)

1. **GUI-dependent.** The bridge (status and window push alike) lives in the
   Snap control-center process. With the GUI closed there is no push; the
   server freezes on the last state and the rows disable again. Native M3
   has no such dependence.
2. **Not live-verified end to end.** All transport/auth/sequencing/policy
   paths are covered by unit, private-bus and GJS regression tests, but no
   strict-Snap install + rev6 extension + logout/login campaign has run here
   (install/login/logout are out of scope). The dedicated server-owned slot
   alternative (headless, no GUI dependence) still needs a new slot plus
   Store declaration approval and is intentionally **not** implemented.
3. The extension revision moved 5 → 6 and `metadata.json` 1 → 2 for the
   protocol addition. Older extension + new Snap: no frames, old degraded
   path. New extension + older Snap: UnknownMethod tolerated, old degraded
   path. The ZIP (`build-zip.sh`) picks up `windowPush.js` automatically.
