"""Window policy editability, separate from transient sensor availability."""


def window_policy_support(*, wayland, gnome, snap, flatpak, verified=False):
    """Flatpak needs positive runtime evidence, not assumed talk permissions.
    Callers retain verified capability across temporary push disconnections.
    Snap Wayland is enabled only with verified bridge-carried push: the strict
    sandbox cannot subscribe to the extension's signals, so frames arrive as
    method calls on the server-owned Snap bridge (no control-center dependence).
    """
    if not wayland:
        return True, ""
    if not gnome:
        return False, "Requires GNOME on Wayland"
    if snap:
        if verified:
            return True, "Requires the Apricus GNOME extension on Wayland"
        return False, "Window policies are not available in Snap on Wayland"
    if flatpak and not verified:
        return False, "Window-state access has not been verified in Flatpak"
    return True, "Requires the Apricus GNOME extension on Wayland"
