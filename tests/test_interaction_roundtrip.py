import os
import pathlib
import shutil
import subprocess
import sys

import pytest


def test_real_dbus_interaction_roundtrip():
    if not shutil.which("gjs") or not shutil.which("dbus-run-session"):
        pytest.skip("private D-Bus and GJS required")
    driver = r"""
import os
import subprocess
from gi.repository import GLib
from apricus.gnome_bridge import Bridge
from apricus.interaction import WebInteraction

bridge = Bridge(lambda: None)
def rpc(method, args, callback):
    def reply():
        callback({"pid": 42, "title": machine.title} if method.endswith("prepare") else True, None)
        return False
    GLib.idle_add(reply)
machine = WebInteraction(lambda: "", rpc, bridge.publish_interaction, GLib.timeout_add, GLib.source_remove)
integration = machine.integration
started = False
def ready(value):
    global started
    integration(value)
    if value and not started:
        started = True
        GLib.idle_add(lambda: machine.begin(42) and False)
machine.integration = ready
bridge.set_interaction(machine)
loop = GLib.MainLoop()
code = 1
def exited(pid, status):
    global code
    code = os.waitstatus_to_exitcode(status)
    loop.quit()
def start():
    if bridge.bus is None:
        return True
    child = subprocess.Popen(["gjs", "-m", "tests/gjs/interaction-roundtrip.js"])
    GLib.child_watch_add(child.pid, exited)
    return False
GLib.timeout_add(100, start)
loop.run()
bridge.stop()
raise SystemExit(code)
"""
    root = pathlib.Path(__file__).resolve().parents[1]
    result = subprocess.run(
        ["dbus-run-session", "--", sys.executable, "-c", driver],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(root / "src"), GIO_USE_VFS="local"),
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS:" in result.stdout
