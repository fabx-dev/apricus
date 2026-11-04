"""Private-bus driver; run only via dbus-run-session from the pytest test."""

import json
import subprocess
from pathlib import Path

from gi.repository import GLib

from apricus.gnome_bridge import Bridge

loop = GLib.MainLoop()
observed = []
bridge = Bridge(lambda: observed.append(bridge.registration.status()))
child = subprocess.Popen(
    ["gjs", "-m", str(Path(__file__).parent / "gjs/bridge-roundtrip.js")],
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)


def completed():
    if child.poll() is None:
        return True
    loop.quit()
    return False


GLib.timeout_add(50, completed)
loop.run()
stdout, stderr = child.communicate(timeout=2)
bridge.stop()
print(stdout, stderr)
print(json.dumps(observed))
assert child.returncode == 0
valid = [item for item in observed if item]
assert valid[0][0] == "active" and valid[0][1]["adopted"] == 0
assert valid[1][0] == "active" and valid[1][1]["adopted"] == 2
assert valid[2][0] == "disabled"
