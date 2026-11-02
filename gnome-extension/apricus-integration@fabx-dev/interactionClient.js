// Server-owned interaction requests carried as replies to one pending Shell read.
// No Snap -> Shell call/signal permission, polling, or worker process required.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

const NAME = 'io.github.fabxdev.Apricus.GnomeBridge';
const PATH = '/io/github/fabxdev/Apricus/GnomeBridge';
const IFACE = `${NAME}1`;

export function validRequest(request) {
    return request?.version === 1 &&
        ['WALLPAPER', 'ENTERING', 'INTERACTIVE', 'EXITING'].includes(request.state) &&
        (request.state === 'WALLPAPER' || (
            /^[0-9a-f]{32}$/.test(request.token) &&
            Number.isInteger(request.pid) && request.pid > 0 &&
            request.title === `Apricus Web [${request.token}]`));
}

export class InteractionClient {
    constructor(controller, io = null) {
        this.controller = controller;
        this.io = io ?? {
            bus: Gio.DBus.session,
            watch: (appeared, vanished) => Gio.bus_watch_name_on_connection(
                Gio.DBus.session, NAME, Gio.BusNameWatcherFlags.NONE,
                (_bus, _name, owner) => appeared(owner), vanished),
            unwatch: id => Gio.bus_unwatch_name(id),
            cancellable: () => new Gio.Cancellable(),
            call: (owner, method, signature, args, timeout, cancel, callback) => {
                Gio.DBus.session.call(owner, PATH, IFACE, method, new GLib.Variant(signature, args),
                    null, Gio.DBusCallFlags.NONE, timeout, cancel, (bus, result) => {
                        try { callback(null, bus.call_finish(result).deepUnpack()); }
                        catch (error) { callback(error, null); }
                    });
            },
        };
        this.owner = null;
        this.epoch = 0;
        this.revision = 0;
        this.closed = false;
        this.watchId = this.io.watch(owner => {
            this._lost();
            this.owner = owner;
            const epoch = this.epoch;
            this.cancel = this.io.cancellable();
            this.io.call(owner, 'InteractionOpen', '()', [], 3000, this.cancel, (error, reply) => {
                if (this.closed || epoch !== this.epoch) return;
                if (error || !/^[0-9a-f]{32}$/.test(reply?.[0])) {
                    this._lost();
                    return;
                }
                this.generation = reply[0];
                this._read();
            });
        }, () => this._lost());
    }

    _lost() {
        if (this.owner && this.generation) {
            this.io.call(this.owner, 'InteractionClose', '(s)', [this.generation], 2000, null, () => {});
        }
        this.generation = null;
        this.epoch++;
        this.cancel?.cancel();
        this.cancel = null;
        this.owner = null;
        this.revision = 0;
        this.controller.exit('bridge-lost', false);
    }

    _read() {
        if (this.closed || !this.owner)
            return;
        const epoch = this.epoch;
        this.cancel = this.io.cancellable();
        // One outstanding read, no periodic timeout/retry loop. Owner loss and
        // disable cancel it. A transport failure leaves interaction disabled.
        this.io.call(this.owner, 'InteractionWatch', '(su)', [this.generation, this.revision],
            0x7fffffff, this.cancel, (error, reply) => {
                if (this.closed || epoch !== this.epoch)
                    return;
                if (error) {
                    this._lost();
                    return;
                }
                try {
                    const [revision, text] = reply;
                    const request = JSON.parse(text);
                    if (!Number.isInteger(revision) || revision <= this.revision || !validRequest(request))
                        throw new Error('invalid interaction request');
                    this.revision = revision;
                    // Re-arm before handling: Done/abort can then wake the
                    // pending read even while entry acknowledgement is in flight.
                    this._read();
                    if (request.state === 'ENTERING')
                        this.controller.enter(request);
                    else if (request.state === 'EXITING')
                        this.controller.exit('server', true, request.token);
                    else if (request.state === 'WALLPAPER')
                        this.controller.exit('idle', false);
                } catch (_error) {
                    this._lost();
                }
            });
    }

    ack(token, phase, ok, callback = () => {}) {
        if (!this.owner || this.closed) {
            callback(false);
            return;
        }
        const epoch = this.epoch;
        this.io.call(this.owner, 'InteractionAck', '(sssb)', [this.generation, token, phase, ok],
            3000, null, (error, reply) => {
                if (!this.closed && epoch === this.epoch)
                    callback(!error && reply?.[0] === true);
            });
    }

    stop() {
        this.controller.exit('disable', true);
        this.closed = true;
        this._lost();
        this.io.unwatch(this.watchId);
    }
}
