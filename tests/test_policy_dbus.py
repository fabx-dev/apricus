"""Real pydbus property marshalling on a private, subprocess-owned bus."""

import os
import shutil
import subprocess
import sys
from pathlib import Path


def test_engine_snapshot_private_dbus():
    assert shutil.which("dbus-run-session"), "dbus-run-session is required"
    source = Path(__file__).resolve().parents[1] / "src"
    env = dict(os.environ, PYTHONPATH=str(source), PYTHONDONTWRITEBYTECODE="1")
    # Never inherit the user's session bus into the child.
    env.pop("DBUS_SESSION_BUS_ADDRESS", None)
    script = r'''
import threading
import xml.etree.ElementTree as ET
from gi.repository import GLib
from pydbus import SessionBus
from apricus.server import ApricusServer, _DBusServer
from apricus.policy import PlaybackPolicyEngine

server = ApricusServer.__new__(ApricusServer)
server.policy = PlaybackPolicyEngine()
view = _DBusServer(server)
property_xml = ET.fromstring(view.dbus).find(".//property[@name='engine_snapshot']")
assert property_xml.attrib == {
    "name": "engine_snapshot", "type": "a{sv}", "access": "read"
}
# Publish just the production diagnostic property: no server initialization,
# sensors, players, GUI, configuration writes or GNOME calls.
xml = """<node><interface name="io.test.Policy">
<property name="engine_snapshot" type="a{sv}" access="read"/>
</interface></node>"""
bus = SessionBus()
publication = bus.publish("io.test.Policy", ("/io/test/Policy", view, xml))
context = GLib.MainContext.default()
tick = GLib.timeout_add(10, lambda: True)

def read_snapshot(expected):
    errors = []
    def client():
        try:
            proxy = SessionBus().get("io.test.Policy", "/io/test/Policy")
            assert proxy.engine_snapshot == expected
            assert proxy.GetAll("io.test.Policy")["engine_snapshot"] == expected
        except BaseException as exc:
            errors.append(exc)
        finally:
            context.wakeup()
    thread = threading.Thread(target=client)
    thread.start()
    while thread.is_alive():
        context.iteration(True)
    thread.join()
    if errors:
        raise errors[0]

read_snapshot(server.policy.snapshot())  # Includes the empty string array.
server.policy.request_manual_pause()
server.policy.reason_active_changed("fullscreen", True)
server.policy.set_manual_mute(True)
read_snapshot(server.policy.snapshot())
server.policy.request_manual_stop()
read_snapshot(server.policy.snapshot())
view.transport_test_value = 7
assert server.transport_test_value == 7
assert view.pause_playback == server.pause_playback
GLib.source_remove(tick)
publication.unpublish()
'''
    result = subprocess.run(
        ["dbus-run-session", "--", sys.executable, "-c", script],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
