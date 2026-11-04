"""Run ONLY under dbus-run-session: production callbacks, no host services."""

import os
import subprocess
import sys
import threading
import time

from gi.repository import GLib
from pydbus import SessionBus
from pydbus.generic import signal

from apricus import server
from apricus.backends import ExtensionPushWindowStateBackend as Push
from apricus.player.video_player import VideoPlayer
from apricus.policy import PlaybackPolicyEngine

assert os.environ.get("M3_PRIVATE_BUS_TEST") == "1"
bus = SessionBus()
loop = GLib.MainLoop()
thread = threading.Thread(target=loop.run, daemon=True)
thread.start()


class Source:
    """<node><interface name='org.gnome.Shell.Extensions.ApricusIntegration'>
    <method name='GetState'><arg type='a{sv}' direction='out'/></method>
    <property name='Version' type='u' access='read'/>
    <signal name='DesktopStateChanged'><arg type='a{sv}'/></signal>
    </interface></node>"""

    Version = 1
    DesktopStateChanged = signal()

    def GetState(self):
        return self.state

    def update(self, seq, fullscreen):
        self.state = {
            "seq": GLib.Variant("u", seq),
            "fullscreen": GLib.Variant("b", fullscreen),
            "maximized": GLib.Variant("b", False),
        }
        self.DesktopStateChanged(self.state)


obj = server.ApricusServer.__new__(server.ApricusServer)
obj.config = {"is_pause_when_maximized": True, "is_mute_when_maximized": False}
obj.policy = PlaybackPolicyEngine()
obj._auto_fed = {}
obj._window_source = "extension-push"
obj._save_config = lambda: None
outputs = []
obj._apply_policy_output = outputs.append
server.get_instance = lambda _name: None


class ServerView(server._DBusServer):
    dbus = """<node><interface name='io.github.fabx_dev.apricus.server'>
    <property name='is_pause_when_maximized' type='b' access='readwrite'/>
    <property name='is_mute_when_maximized' type='b' access='readwrite'/>
    <property name='engine_snapshot' type='a{sv}' access='read'/>
    </interface></node>"""


class PlayerView:
    # Real production XML + getters: regression for the GetAll marshaller.
    dbus = VideoPlayer.__doc__
    config = {
        "mode": "MODE_VIDEO",
        "data_source": {"Default": "/test.mp4"},
        "audio_volume": 50,
        "is_mute": False,
    }
    mode = VideoPlayer.mode
    data_source = VideoPlayer.data_source
    volume = VideoPlayer.volume
    is_mute = VideoPlayer.is_mute
    is_playing = VideoPlayer.is_playing
    playback_state = "playing"
    is_paused_by_user = False
    last_error = ""

    def __getattr__(self, _name):
        return lambda *_args: None


def wait_for(predicate):
    end = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < end, "private bus delivery timed out"
        time.sleep(0.005)


source = Source()
source.state = {
    "seq": GLib.Variant("u", 1),
    "fullscreen": GLib.Variant("b", False),
    "maximized": GLib.Variant("b", False),
}
with (
    bus.publish(Push.BUS_NAME, (Push.OBJECT_PATH, source)),
    bus.publish(
        "io.github.fabx_dev.Apricus.server", ("/io/github/fabx_dev/Apricus/server", ServerView(obj))
    ),
    bus.publish(
        "io.github.fabx_dev.Apricus.player", ("/io/github/fabx_dev/Apricus/player", PlayerView())
    ),
):
    backend = Push.try_probe(obj._on_window_state_changed, bus=bus)
    assert backend is not None and backend.start()
    assert backend.start()  # no duplicate subscription
    source.update(2, True)
    wait_for(lambda: outputs[-1].desired == "paused")
    count = len(outputs)
    source.update(2, False)
    source.update(1, False)
    time.sleep(0.05)
    assert len(outputs) == count and obj.policy.compute().desired == "paused"

    # GTK4 must be isolated from the GTK3 server. Its real state-set callback
    # writes actual pydbus properties; disk config is replaced by memory only.
    gui = r"""
from pydbus import SessionBus
import apricus.gui.gtk4app as app
class Store:
    data = {}
    def load(self): return dict(self.data)
    def save(self, value): self.data = dict(value)
store = Store()
app.ConfigUtil = lambda: store
panel = app.ControlPanel.__new__(app.ControlPanel)
panel._server = lambda: SessionBus().get('io.github.fabx_dev.Apricus.server')
panel.config = {}
panel._on_settings_flag(None, True, 'is_mute_when_maximized')
panel._on_settings_flag(None, False, 'is_pause_when_maximized')
assert store.data['is_pause_when_maximized'] is False
"""
    subprocess.run([sys.executable, "-c", gui], check=True, timeout=20)
    assert outputs[-1].desired == "playing" and outputs[-1].wanted_mute
    assert obj.policy.snapshot()["active_reasons"] == ["maximized_mute"]
    source.update(3, False)
    wait_for(lambda: not outputs[-1].wanted_mute)
    backend.stop()
    player = bus.get("io.github.fabx_dev.Apricus.player")
    state = player.GetAll("io.github.fabx_dev.apricus.player")
    assert state["data_source"] == {"Default": "/test.mp4"}
    assert state["playback_state"] == "playing"
    print(
        "PASS: real private bus push, duplicate/stale rejection, GUI -> server -> policy, player GetAll"
    )
loop.quit()
thread.join(timeout=2)
