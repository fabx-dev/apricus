"""Cancellable GLib RPC; no UI/main-loop socket waits or dedicated threads."""

import json

from gi.repository import Gio, GLib


def call(bus_name, method, args, callback, *, prop=False):
    from apricus.utils import is_snap

    cancel = Gio.Cancellable()
    done = False
    connection = None
    timer = None

    def finish(value=None, error=None):
        nonlocal done
        if done:
            return
        done = True
        if timer is not None:
            GLib.source_remove(timer)
        cancel.cancel()
        if connection is not None:
            connection.close_async(GLib.PRIORITY_DEFAULT, None, None, None)
        callback(value, error)

    def expired():
        nonlocal timer
        timer = None
        finish(error="timeout")
        return False

    timer = GLib.timeout_add(3000, expired)
    if is_snap():
        from apricus.snap_ipc import _sock_path

        client = Gio.SocketClient()
        client.set_timeout(3)
        payload = {"op": "get" if prop else "call", "attr": method}
        if not prop:
            payload["args"] = args

        def connected(source, result):
            nonlocal connection
            try:
                connection = source.connect_finish(result)
                if done:
                    connection.close(None)
                    return
                stream = connection.get_output_stream()

                def written(output, result):
                    try:
                        output.write_all_finish(result)
                        incoming = Gio.DataInputStream.new(connection.get_input_stream())

                        def read(input_stream, result):
                            try:
                                data, _ = input_stream.read_line_finish(result)
                                if data is None or len(data) > 65536:
                                    raise ValueError("invalid RPC response")
                                response = json.loads(data)
                                finish(
                                    response.get("res"),
                                    None if response.get("ok") else response.get("err", "rejected"),
                                )
                            except (GLib.Error, ValueError) as error:
                                finish(error=str(error))

                        incoming.read_line_async(GLib.PRIORITY_DEFAULT, cancel, read)
                    except GLib.Error as error:
                        finish(error=str(error))

                stream.write_all_async(
                    (json.dumps(payload) + "\n").encode(), GLib.PRIORITY_DEFAULT, cancel, written
                )
            except GLib.Error as error:
                finish(error=str(error))

        client.connect_async(Gio.UnixSocketAddress.new(_sock_path(bus_name)), cancel, connected)
    else:
        path = "/" + bus_name.replace(".", "/")
        interface = "io.github.fabx_dev.apricus." + bus_name.rsplit(".", 1)[-1]

        def acquired(_source, result):
            try:
                bus = Gio.bus_get_finish(result)
                if done:
                    return
                signature = (
                    "(ss)"
                    if prop
                    else "("
                    + "".join(
                        "b" if isinstance(a, bool) else "u" if isinstance(a, int) else "s"
                        for a in args
                    )
                    + ")"
                )
                parameters = GLib.Variant(signature, (interface, method) if prop else tuple(args))

                def replied(source, result):
                    try:
                        values = source.call_finish(result).unpack()
                        finish(values[0] if values else None)
                    except GLib.Error as error:
                        finish(error=str(error))

                bus.call(
                    bus_name,
                    path,
                    "org.freedesktop.DBus.Properties" if prop else interface,
                    "Get" if prop else method,
                    parameters,
                    None,
                    Gio.DBusCallFlags.NO_AUTO_START,
                    2500,
                    cancel,
                    replied,
                )
            except GLib.Error as error:
                finish(error=str(error))

        Gio.bus_get(Gio.BusType.SESSION, cancel, acquired)
    return cancel
