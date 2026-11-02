"""Real Gio async Unix socket exchange, including cancellation."""

import json
import socket
import threading

from gi.repository import GLib

from apricus import async_rpc, snap_ipc, utils


def test_async_snap_rpc_roundtrip(tmp_path, monkeypatch):
    path = str(tmp_path / "rpc.sock")
    monkeypatch.setattr(utils, "is_snap", lambda: True)
    monkeypatch.setattr(snap_ipc, "_sock_path", lambda _: path)
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(path)
    listener.listen(1)
    listener.settimeout(5)
    received = []

    def serve():
        with listener.accept()[0] as connection:
            data = b""
            while b"\n" not in data:
                data += connection.recv(4096)
            received.append(json.loads(data))
            connection.sendall(b'{"ok":true,"res":"prepared"}\n')

    thread = threading.Thread(target=serve)
    thread.start()
    loop = GLib.MainLoop()
    result = []
    timeout = GLib.timeout_add(5000, lambda: loop.quit() or False)
    async_rpc.call(
        "test.player",
        "web_interaction_prepare",
        ["a" * 32, 42],
        lambda *args: (result.append(args), loop.quit()),
    )
    loop.run()
    GLib.source_remove(timeout)
    thread.join(5)
    listener.close()
    assert result == [("prepared", None)]
    assert received == [{"op": "call", "attr": "web_interaction_prepare", "args": ["a" * 32, 42]}]


def test_async_rpc_cancel_completes_once(tmp_path, monkeypatch):
    monkeypatch.setattr(utils, "is_snap", lambda: True)
    monkeypatch.setattr(snap_ipc, "_sock_path", lambda _: str(tmp_path / "absent.sock"))
    loop = GLib.MainLoop()
    result = []
    cancel = async_rpc.call(
        "test.player", "method", [], lambda *args: (result.append(args), loop.quit())
    )
    cancel.cancel()
    timeout = GLib.timeout_add(5000, lambda: loop.quit() or False)
    loop.run()
    GLib.source_remove(timeout)
    assert len(result) == 1 and result[0][0] is None and result[0][1]
