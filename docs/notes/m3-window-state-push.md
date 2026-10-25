# M3 — Wayland window-state push (design record, implemented)

Compositor truth flows extension → server over a dedicated versioned bus
name. The server remains the only playback decider; the extension carries
no policy, no window identities, and no playback verbs.

## Contract

- Bus name `org.gnome.Shell.Extensions.ApricusIntegration` (well-known,
  owned by the extension), object `/org/gnome/Shell/Extensions/ApricusIntegration`,
  interface of the same name, `Version = 1`.
- Properties (read-only): `Version`, `ShellVersion`, `ExtensionVersion`,
  `CodeRevision`, `FullscreenActive`, `MaximizedActive`,
  `FullscreenMonitors`, `MaximizedMonitors`, `ActiveWorkspace`,
  `NWorkspaces`, `Monitors`, `PushAlive`.
- `GetState() → a{sv}`: full snapshot for reconnect/startup reconciliation.
- `Ping() → u`: CodeRevision liveness check without a state dump.
- Signal `DesktopStateChanged(a{sv})`: `{seq, fullscreen, maximized,
  fs_monitors, max_monitors, workspace, n_workspaces, monitors}`.
- Payload carries counts and indices only — never titles, app IDs, PIDs,
  paths or window references (EGO-safe; also enforced by tests).

## Direction and trust

- Only the extension emits; the server subscribes and pulls `GetState` on
  (re)connect. No Snap → host traffic and no new sandbox permissions are
  involved; nothing in the host bridge changed.
- Each signal is accepted only if the sender equals the current bus owner
  and its sequence number is newer than the last applied one for that
  owner. Owner changes trigger a `GetState` reconcile (never a blind
  continue) and stale/foreign/malformed frames are dropped.
- Verified Mutter 18 detail: there is no single `notify::maximized` — the
  sensor subscribes to `notify::fullscreen`, `notify::maximized-horizontally`
  and `notify::maximized-vertically` (plus enter/leave-monitor and workspace
   signals). Debounce is trailing-edge 250 ms in the extension, using
   `GLib.timeout_add(priority, milliseconds, callback)`. Each new edge cancels
   and reschedules the source; stop cancels it. Already-settled push snapshots
   update all three window reasons atomically without a second engine delay.
   X11/polled/local producers retain the engine's 250 ms debounce. The ≤500 ms
   latency objective is NOT yet validated on the compositor/player path.

## Server ladder (single active producer)

Wayland + GNOME: try extension push first; on absence or loss, fall back
to the existing Introspect poll, else Wnck (X11 only, unchanged), else Null.
X11 never probes push. Losing push freezes the last known auto reasons and
marks `push_alive=false` with a `window_state_source` diagnostic — it never
auto-resumes playback. Reappearance re-probes with `GetState`.

Deliberate deviation from the roadmap draft: the engine's `push_sources`
drop-rule is left empty (diagnostics flag only). Registering the shared
reason names there would wrongly drop the X11/Wnck feed whenever push is
down; the single-producer ladder plus `_feed_auto` dedup already guarantees
no stale or doubled updates.

## What is explicitly NOT in M3

- No new Snap slot/permission, no AppArmor change, no sandbox bypass:
  Snap strict stays on the documented degraded path (`unknown` source,
  honest messaging, no polling storm).
- No Flatpak manifest change without a separate, explicitly authorized
  live verification of the sub-name talk permission.
- No playback logic in the extension (asserted by tests).
- No native GUI install claim and no live suspend/resume proof here;
   those belong to the authorized live campaign.

## Local closure after the third live campaign

See [M3 local closure and reduced validation](m3-local-closure.md). The third
campaign on `6fc5f3f` demonstrated the real native GNOME 50.1 push path and
manual priorities. It did not validate exact latency or each maximize axis
live. The original rev4 extension was restored and loaded after manual login;
configuration and URI matched their backups. **The regenerated static PNG had
no byte-identical baseline backup: full byte-identical restoration is not
claimed.** This reservation remains open and is not repaired by code changes.
