# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Lightweight Unix-domain-socket RPC for Snap confinement.

Inside a strict Snap without custom D-Bus slots, AppArmor denies owning
bus names (`io.github.fabx_dev.Apricus.*`) on the host session bus. This
module provides a drop-in local RPC over a Unix socket in the user's
runtime directory so `server`, `player` and `gui` communicate with the
exact same method/property interface as `pydbus`, with all server/player
handlers dispatched on the GLib main loop.
"""

import json
import logging
import os
import socket
import threading

from gi.repository import GLib

from apricus.commons import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


def _sock_dir():
    base = os.environ.get("XDG_RUNTIME_DIR") or f"/tmp/apricus-ipc-{os.getuid()}"
    # Inside Snap, $XDG_RUNTIME_DIR/snap.<snap_name> is always writable and
    # shared across all processes of the same snap.
    snap_name = os.environ.get("SNAP_NAME")
    if snap_name and os.path.isdir(os.path.join(base, f"snap.{snap_name}")):
        d = os.path.join(base, f"snap.{snap_name}", "ipc")
    else:
        d = os.path.join(base, "apricus-ipc")
    os.makedirs(d, mode=0o700, exist_ok=True)
    return d


def _sock_path(bus_name):
    short = (bus_name or "ipc").rsplit(".", 1)[-1]
    return os.path.join(_sock_dir(), f"{short}.sock")


def _invoke_on_mainloop(obj, req):
    done = threading.Event()
    box = {"ok": True, "res": None, "err": None}

    def _run():
        try:
            op = req.get("op")
            attr = req.get("attr", "")
            if not attr or attr.startswith("_"):
                raise AttributeError(f"Forbidden attribute: {attr}")
            if op == "get":
                box["res"] = getattr(obj, attr)
            elif op == "set":
                setattr(obj, attr, req.get("val"))
            elif op == "call":
                fn = getattr(obj, attr)
                args = req.get("args") or []
                box["res"] = fn(*args)
            else:
                raise ValueError(f"Unknown op: {op}")
        except Exception as e:  # noqa: BLE001
            box["ok"] = False
            box["err"] = f"{type(e).__name__}: {e}"
        finally:
            done.set()
        return False

    GLib.idle_add(_run)
    done.wait(timeout=30)
    return box


def publish_service(bus_name, obj):
    """Listen on the Unix socket for *bus_name* and dispatch onto *obj*."""
    path = _sock_path(bus_name)
    try:
        os.unlink(path)
    except OSError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    os.chmod(path, 0o600)
    srv.listen(16)

    def _accept_loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                break
            threading.Thread(target=_handle_client, args=(conn, obj), daemon=True).start()

    threading.Thread(target=_accept_loop, daemon=True, name=f"ipc-{bus_name}").start()
    return srv


def _handle_client(conn, obj):
    with conn:
        try:
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
                if b"\n" in chunk:
                    break
            if not data:
                return
            req = json.loads(data.decode("utf-8").splitlines()[0])
            resp = _invoke_on_mainloop(obj, req)
            conn.sendall((json.dumps(resp) + "\n").encode("utf-8"))
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[SnapIPC] client error: {e}")


class _MethodProxy:
    def __init__(self, proxy, name):
        self._proxy = proxy
        self._name = name

    def __call__(self, *args):
        return self._proxy._rpc({"op": "call", "attr": self._name, "args": list(args)})


class ServiceProxy:
    """Client proxy mirroring pydbus attribute/method access."""

    _METHODS = {
        "null",
        "video",
        "stream",
        "webpage",
        "playlist",
        "pause_playback",
        "start_playback",
        "reload",
        "feeling_lucky",
        "show_gui",
        "quit",
        "preview_static",
        "switch_media",
        "reload_config",
        "quit_player",
        "playback_error",
    }

    def __init__(self, bus_name):
        object.__setattr__(self, "_bus_name", bus_name)

    def _rpc(self, payload):
        path = _sock_path(object.__getattribute__(self, "_bus_name"))
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(15)
                s.connect(path)
                s.sendall((json.dumps(payload) + "\n").encode("utf-8"))
                buf = b""
                while True:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    if b"\n" in chunk:
                        break
            resp = json.loads(buf.decode("utf-8").splitlines()[0])
        except OSError as e:
            raise GLib.Error(str(e)) from e
        if not resp.get("ok"):
            err = resp.get("err") or "RPC error"
            if err.startswith("AttributeError:"):
                raise AttributeError(err)
            raise GLib.Error(err)
        return resp.get("res")

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._METHODS and name != "playback_error":
            return _MethodProxy(self, name)
        return self._rpc({"op": "get", "attr": name})

    def __setattr__(self, name, value):
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        self._rpc({"op": "set", "attr": name, "val": value})


def get_service(bus_name):
    """Return a live ServiceProxy if the socket responds, else None."""
    path = _sock_path(bus_name)
    if not os.path.exists(path):
        return None
    proxy = ServiceProxy(bus_name)
    try:
        _ = proxy.mode
        return proxy
    except Exception:  # noqa: BLE001
        return None
