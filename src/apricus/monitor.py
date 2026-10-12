import gi


def _gdk():
    """Gdk namespace, lazily loaded without pinning a major version.

    GTK3 processes get Gdk 3.0; GTK4 processes reuse the already-loaded
    Gdk 4.0. Callers must use only APIs present in both (or branch)."""
    try:
        gi.require_version("Gdk", "3.0")
    except ValueError:
        pass  # another major version already loaded; use it as-is
    from gi.repository import Gdk

    return Gdk


class Monitor:
    def __init__(
        self, name, width=0, height=0, x=0, y=0, is_primary=False, wallpaper=None, key=None
    ):
        self.width = width
        self.height = height
        self.wallpaper = wallpaper
        self.is_primary = is_primary
        self.x = x
        self.y = y
        self.name = name
        # Stable config key. Falls back to the display name (legacy behavior).
        self.key = key or name

    def set_wallpaper(self, wallpaper):
        self.wallpaper = wallpaper

    def __str__(self):
        return f"Monitor(key={self.key}, name={self.name}, x={self.x}, y={self.y}, width={self.width}, height={self.height}, is_primary={self.is_primary}, wallpaper={self.wallpaper})"


def assign_keys(infos):
    """Assign stable unique config keys to a list of monitor info dicts.

    Monitors with a unique model keep the bare model as key (back-compatible
    with existing configs). Duplicate models get a `Model#i` suffix in
    (x, y) order so identical screens no longer overwrite each other in
    `data_source`. Input order is preserved.
    Each info dict must have `name`, `x`, `y`. Returns new dicts with `key`.
    """
    counts = {}
    for info in infos:
        counts[info["name"]] = counts.get(info["name"], 0) + 1
    order = sorted(
        range(len(infos)), key=lambda i: (infos[i]["name"], infos[i]["x"], infos[i]["y"])
    )
    keys = [None] * len(infos)
    seen = {}
    for i in order:
        name = infos[i]["name"]
        if counts[name] == 1:
            keys[i] = name
        else:
            keys[i] = f"{name}#{seen.get(name, 0)}"
            seen[name] = seen.get(name, 0) + 1
    return [{**info, "key": key} for info, key in zip(infos, keys, strict=True)]


class MonitorInfo:
    @staticmethod
    def get_unique_monitor_count():
        Gdk = _gdk()
        display = Gdk.Display.get_default()
        if display is None:
            return 0
        if hasattr(display, "get_monitors"):
            return display.get_monitors().get_n_items()
        return display.get_n_monitors()

    @staticmethod
    def monitors():
        Gdk = _gdk()
        display = Gdk.Display.get_default()
        if display is None:
            # Headless (CI, tests): no monitors to report.
            return []
        if hasattr(display, "get_monitors"):
            # GTK4: monitors exposed as a Gio.ListModel.
            return assign_keys(MonitorInfo._from_list_model(display.get_monitors()))
        return assign_keys(MonitorInfo._from_indexed(display))

    @staticmethod
    def _from_indexed(display):
        monitors = []
        for i in range(display.get_n_monitors()):
            monitor = display.get_monitor(i)
            monitors.append(MonitorInfo._describe(monitor))
        return monitors

    @staticmethod
    def _from_list_model(model):
        monitors = []
        for i in range(model.get_n_items()):
            monitors.append(MonitorInfo._describe(model.get_item(i)))
        return monitors

    @staticmethod
    def _describe(monitor):
        geometry = monitor.get_geometry()
        try:
            width_mm = monitor.get_width_mm()
        except Exception:
            width_mm = 0
        try:
            height_mm = monitor.get_height_mm()
        except Exception:
            height_mm = 0
        for attr in ("get_model", "get_connector", "get_manufacturer"):
            name = getattr(monitor, attr, lambda: None)()
            if name:
                break
        else:
            name = "Unknown"
        try:
            is_primary = bool(monitor.is_primary())
        except Exception:
            is_primary = False
        return {
            "x": geometry.x,
            "y": geometry.y,
            "width": geometry.width,
            "height": geometry.height,
            "width_mm": width_mm,
            "height_mm": height_mm,
            "name": name,
            "is_primary": is_primary,
        }


class Monitors:
    def __init__(self):
        self.monitors = {}
        for monitor in MonitorInfo.monitors():
            self.monitors[monitor["key"]] = Monitor(
                name=monitor["name"],
                width=monitor["width"],
                height=monitor["height"],
                x=monitor["x"],
                y=monitor["y"],
                is_primary=monitor["is_primary"],
                key=monitor["key"],
            )

    def get_monitor(self, key):
        return self.monitors[key]

    def get_primary_monitor(self):
        for _model, monitor in self.monitors.items():
            if monitor.is_primary:
                return monitor

        return self.monitors.items[0]

    def get_primary_monitor_index(self):
        for i, (_model, monitor) in enumerate(self.monitors.items()):
            if monitor.is_primary:
                return i

        return 0

    def get_monitors(self):
        return self.monitors

    def __str__(self):
        return "\n".join(str(monitor) for monitor in self.monitors)
