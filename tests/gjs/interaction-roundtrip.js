// Actual D-Bus signatures/authentication with the production Python coordinator.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const name = 'io.github.fabxdev.Apricus.GnomeBridge';
const bus = Gio.DBus.session;
bus.call_sync('org.freedesktop.DBus', '/org/freedesktop/DBus', 'org.freedesktop.DBus',
    'RequestName', new GLib.Variant('(su)', ['org.gnome.Shell', 4]), null, Gio.DBusCallFlags.NONE, 3000, null);
function call(method, signature, args) {
    return bus.call_sync(name, '/io/github/fabxdev/Apricus/GnomeBridge', `${name}1`, method,
        new GLib.Variant(signature, args), null, Gio.DBusCallFlags.NONE, 5000, null).deepUnpack();
}
function assert(value, message) { if (!value) throw new Error(message); }
const [generation] = call('InteractionOpen', '()', []);
let [revision, text] = call('InteractionWatch', '(su)', [generation, 0]);
assert(JSON.parse(text).state === 'WALLPAPER', 'initial snapshot');
[revision, text] = call('InteractionWatch', '(su)', [generation, revision]);
const request = JSON.parse(text);
assert(request.state === 'ENTERING' && request.pid === 42, 'prepared original player');
assert(call('InteractionAck', '(sssb)', [generation, request.token, 'INTERACTIVE', true])[0], 'entry ack');
assert(!call('InteractionAck', '(sssb)', ['a'.repeat(32), request.token, 'WALLPAPER', true])[0], 'old channel rejected');
assert(call('InteractionAck', '(sssb)', [generation, request.token, 'WALLPAPER', true])[0], 'Done ack');
[revision, text] = call('InteractionWatch', '(su)', [generation, revision]);
assert(JSON.parse(text).state === 'WALLPAPER', 'async original-player restoration');
assert(!call('InteractionAck', '(sssb)', [generation, request.token, 'INTERACTIVE', true])[0], 'late ack rejected');
call('InteractionClose', '(s)', [generation]);
print('PASS: real D-Bus interaction generation, pending read, prepare, acknowledgements and restoration');
