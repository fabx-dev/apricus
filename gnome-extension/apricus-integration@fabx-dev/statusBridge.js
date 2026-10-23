// Copyright (C) 2026 fabx-dev
// SPDX-License-Identifier: GPL-3.0-or-later
// Status only. No player commands or Snap → Shell signals.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const BUS_NAME = 'io.github.fabxdev.Apricus.GnomeBridge';
const PATH = '/io/github/fabxdev/Apricus/GnomeBridge';
const INTERFACE = `${BUS_NAME}1`;

const runtime = {
    bus: () => Gio.DBus.session,
    watch: (bus, appeared, vanished) => Gio.bus_watch_name_on_connection(
        bus, BUS_NAME, Gio.BusNameWatcherFlags.NONE, appeared, vanished),
    unwatch: id => Gio.bus_unwatch_name(id),
    later: (seconds, callback) => GLib.timeout_add_seconds(
        GLib.PRIORITY_DEFAULT, seconds, () => { callback(); return GLib.SOURCE_REMOVE; }),
    idle: callback => GLib.idle_add(GLib.PRIORITY_DEFAULT_IDLE,
        () => { callback(); return GLib.SOURCE_REMOVE; }),
    remove: id => GLib.source_remove(id),
    call: (bus, owner, method, signature, args, callback) => {
        bus.call(owner, PATH, INTERFACE, method, new GLib.Variant(signature, args),
            null, Gio.DBusCallFlags.NONE, 3000, null, (connection, result) => {
                let reply;
                try { reply = connection.call_finish(result).deepUnpack(); }
                catch (error) { callback(error, null); return; }
                callback(null, reply);
            });
    },
};

export class StatusBridge {
    constructor(snapshot, io = runtime) {
        this._snapshot = snapshot;
        this._io = io;
        this._owner = null;
        this._generation = null;
        this._epoch = 0;
        this._sequence = 0;
        this._inflight = false;
        this._stopped = false;
        this._timer = 0;
        this._idle = 0;
        this._attempts = 0;
        this._last = null;
        this._bus = io.bus();
        this._watch = io.watch(this._bus,
            (_bus, _name, owner) => {
                if (this._stopped)
                    return;
                this._reset();
                this._owner = owner;
                this._attempts = 0;
                this._flush(true);
            },
            () => { this._reset(); this._owner = null; });
    }

    _cancelTimer() {
        if (this._timer)
            this._io.remove(this._timer);
        this._timer = 0;
    }

    _reset() {
        this._epoch++;
        this._cancelTimer();
        this._generation = null;
        this._inflight = false;
        this._last = null;
    }

    changed() {
        if (this._stopped || this._idle)
            return;
        this._idle = this._io.idle(() => {
            this._idle = 0;
            this._flush(false);
        });
    }

    _flush(force) {
        if (this._stopped || !this._owner || this._inflight)
            return;
        let snapshot;
        try { snapshot = this._snapshot(); }
        catch (error) {
            // A broken integration must not keep renewing a green lease.
            this._cancelTimer();
            console.warn(`[ApricusBridge] snapshot failed: ${error.message}`);
            return;
        }
        const key = JSON.stringify(snapshot);
        if (!force && key === this._last)
            return;
        this._cancelTimer();
        const registering = !this._generation;
        const method = registering ? 'Register' : 'Update';
        this._sequence = registering ? 1 : this._sequence + 1;
        const args = registering
            ? [1, 1, ...snapshot]
            : [this._generation, this._sequence, ...snapshot];
        const epoch = this._epoch;
        const owner = this._owner;
        this._inflight = true;
        this._io.call(this._bus, owner, method, registering ? '(uubbu)' : '(subbu)',
            args, (error, reply) => {
                if (epoch !== this._epoch)
                    return;
                this._inflight = false;
                if (!error && registering) {
                    if (!reply || typeof reply[0] !== 'string' || reply[0].length !== 32 ||
                        reply[1] !== 90 || reply[2] !== 30) {
                        error = new Error('unsupported bridge reply');
                    } else {
                        this._generation = reply[0];
                    }
                }
                if (this._stopped) {
                    this._unregister();
                    return;
                }
                if (error) {
                    this._generation = null;
                    console.warn(`[ApricusBridge] ${method} failed: ${error.message}`);
                    // Bounded recovery only; name ownership changes reset it.
                    if (this._attempts < 3) {
                        const delay = 5 * 2 ** this._attempts++;
                        this._timer = this._io.later(delay, () => {
                            this._timer = 0;
                            this._flush(true);
                        });
                    }
                    return;
                }
                this._last = key;
                this._attempts = 0;
                if (registering)
                    console.warn(`[ApricusBridge] registered with ${owner}`);
                this._timer = this._io.later(30, () => {
                    this._timer = 0;
                    this._flush(true);
                });
                // Coalesce any changes that arrived during the async call.
                this.changed();
            });
    }

    _unregister() {
        if (!this._owner || !this._generation || this._inflight)
            return;
        const generation = this._generation;
        this._generation = null;
        this._io.call(this._bus, this._owner, 'Unregister', '(su)',
            [generation, ++this._sequence], error => {
                if (error)
                    console.warn(`[ApricusBridge] disable unconfirmed: ${error.message}`);
            });
    }

    stop() {
        this._stopped = true;
        this._cancelTimer();
        if (this._idle)
            this._io.remove(this._idle);
        this._idle = 0;
        this._io.unwatch(this._watch);
        // If Register/Update is in flight, its callback unregisters after ack.
        // A lost reply remains bounded by the server lease, not permanent ACTIVE.
        this._unregister();
    }
}
