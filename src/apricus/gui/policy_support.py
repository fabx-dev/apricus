"""Window policy editability, separate from transient sensor availability."""


def window_policy_support(*, wayland, gnome, snap, flatpak, verified=False):
    """Flatpak needs positive runtime evidence, not assumed talk permissions.
    Callers retain verified capability across temporary push disconnections.
    Snap Wayland is deliberately excluded until its M3 transport exists.
    """
    if not wayland:
        return True, ""
    if not gnome:
        return False, "Requires GNOME on Wayland"
    if snap:
        return False, "Window policies are not available in Snap on Wayland"
    if flatpak and not verified:
        return False, "Window-state access has not been verified in Flatpak"
    return True, "Requires the Apricus GNOME extension on Wayland"
