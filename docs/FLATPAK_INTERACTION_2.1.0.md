# Flatpak interaction assessment — 2.1.0

**FAIL** — functional parity for **Interact with webpage** with the frozen rev8 architecture.
Base webpage display is separate; no sandbox bypass or broad permission was added.

## Exact names and authentication

The application ID and its default owned namespace are
`io.github.fabx_dev.Apricus` (underscore). The server bridge is instead
`io.github.fabxdev.Apricus.GnomeBridge` (no underscore), object path
`/io/github/fabxdev/Apricus/GnomeBridge`, interface
`io.github.fabxdev.Apricus.GnomeBridge1`.
The manifest permits `org.gnome.Shell.Extensions`, but not this bridge name.
The server authenticates the calling unique sender against the owner of
`org.gnome.Shell`. The aggregate native signal channel is
`org.gnome.Shell.Extensions.ApricusIntegration`; it is not the bridge.

**PASS** — bounded private-bus test using the actual server `Bridge` and
xdg-dbus-proxy filters: current policy cannot own the bridge; adding exactly
`--own=io.github.fabxdev.Apricus.GnomeBridge` allows ownership but authenticated
Register fails because Shell ownership is invisible; adding exactly
`--see=org.gnome.Shell` makes Register succeed. This does **not** certify a real
Flatpak/Shell interaction cycle. Flatpak supports `see` in bus-policy metadata,
but has no public `--see-name` finish argument. Generic `--talk-name=org.gnome.Shell`
would expose unnecessary Shell methods and was deliberately not granted.

## Independent window-identity obstacle

**PASS** — sandbox probe reports Python PID 2 and `NSpid: 2` inside the existing
Flatpak GNOME 50 build. The interaction record uses the player's `os.getpid()`;
rev8 requires equality with `MetaWindow.get_pid()` in the host namespace in
addition to role/title/token/current-window checks. Mutter's X11 backend obtains
the local client PID from XRes; its Wayland backend obtains peer credentials.
An internal PID must not be substituted or allowed to authenticate another
host window merely to make interaction succeed.

Therefore exact bus permissions alone are insufficient. Secure namespace-aware
identity translation would require a protocol/architecture change and dedicated
security validation, beyond the authorized frozen release scope. No PID check
was removed and rev8 was not changed.

## Distribution conclusion

**NOT TESTED** — Flathub submission/review; none was requested or performed.
Existing narrow manifest permissions remain unchanged. No session-bus socket,
wildcard bus access, host process escape or extra filesystem grant was added.
Users must install rev8 on the host separately; sandbox copies cannot load
themselves into the host Shell. This documented package difference is a
NON-BLOCKER, not a claim of parity or Flathub approval.

References: Flatpak Sandbox Permissions documentation;
`flatpak/flatpak` common/flatpak-context.c (see policy and finish options);
GNOME Mutter src/x11/window-x11.c and src/core/window.c (client PID).
Private test evidence is retained outside distributable sources.
