import gi

gi.require_version("Gdk", "3.0")
from gi.repository import Gdk


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
        display = Gdk.Display.get_default()
        return display.get_n_monitors()

    @staticmethod
    def monitors():
        display = Gdk.Display.get_default()
        n_monitors = display.get_n_monitors()
        monitors = []

        for i in range(n_monitors):
            monitor = display.get_monitor(i)
            geometry = monitor.get_geometry()
            width_mm = monitor.get_width_mm()
            height_mm = monitor.get_height_mm()
            name = monitor.get_model()
            is_primary = monitor.is_primary()

            monitors.append(
                {
                    "x": geometry.x,
                    "y": geometry.y,
                    "width": geometry.width,
                    "height": geometry.height,
                    "width_mm": width_mm,
                    "height_mm": height_mm,
                    "name": name,
                    "is_primary": is_primary,
                }
            )

        return assign_keys(monitors)


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
