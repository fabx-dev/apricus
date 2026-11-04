// Integration test on an ISOLATED dbus-run-session bus, never the real Shell bus.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const NAME = 'io.github.fabxdev.Apricus.GnomeBridge';
const PATH = '/io/github/fabxdev/Apricus/GnomeBridge';
const loop = new GLib.MainLoop(null, false);
const bus = Gio.DBus.session;
let failure = null;

function call(connection, method, signature, args) {
    return connection.call_sync(NAME, PATH, `${NAME}1`, method,
        new GLib.Variant(signature, args), null, Gio.DBusCallFlags.NONE, 3000, null).deepUnpack();
}
function rejected(callback) {
    try { callback(); }
    catch (error) {
        if (error.message.includes('.Rejected'))
            return;
        throw error;
    }
    throw new Error('Expected rejection');
}
const watcher = Gio.bus_watch_name_on_connection(bus, NAME, Gio.BusNameWatcherFlags.NONE,
    () => {
        try {
            bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus',
                'org.freedesktop.DBus', 'RequestName',
                new GLib.Variant('(su)', ['org.gnome.Shell', 4]), null,
                Gio.DBusCallFlags.NONE, 3000, null);
            const [token, lease, renew] = call(bus, 'Register', '(uubbu)', [1, 1, true, true, 0]);
            if (lease !== 90 || renew !== 30)
                throw new Error('Wrong lease');
            const other = Gio.DBusConnection.new_for_address_sync(
                GLib.getenv('DBUS_SESSION_BUS_ADDRESS'),
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT |
                Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, null, null);
            // Even the SAME PID/UID with a different connection must fail.
            rejected(() => call(other, 'Register', '(uubbu)', [1, 1, true, true, 0]));
            other.close_sync(null);
            call(bus, 'Update', '(subbu)', [token, 2, true, true, 2]);
            rejected(() => call(bus, 'Update', '(subbu)', [token, 2, true, true, 0]));
            call(bus, 'Unregister', '(su)', [token, 3]);
            rejected(() => call(bus, 'Update', '(subbu)', [token, 4, true, true, 0]));
            print('PASS: real bus authentication, credentials, update, replay rejection and unregister');
        } catch (error) { failure = error; }
        loop.quit();
    }, () => {});
GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 10, () => {
    failure = new Error('Bridge startup timeout');
    loop.quit();
    return GLib.SOURCE_REMOVE;
});
loop.run();
Gio.bus_unwatch_name(watcher);
if (failure)
    throw failure;
