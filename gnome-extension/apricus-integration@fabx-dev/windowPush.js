// Copyright (C) 2026 fabx-dev
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Window-state push for strict Snap confinement.
//
// The Snap cannot subscribe to this extension's own D-Bus signals: its
// AppArmor mediation denies arbitrary session-bus names. The only sanctioned
// inbound path is the reverse direction already proven by the status bridge:
// the unconfined Shell calls methods on a Snap-owned well-known name.
//
// WindowPush therefore delivers tracker frames as method calls on the SAME
// Snap-owned bridge name -- no new slot, no new permission, no manifest
// change. The protocol is additive: WindowRegister/WindowState/
// WindowUnregister are new methods; Register/Update/Unregister are untouched.
// An older Snap that lacks the new methods rejects them cleanly
// (UnknownMethod) and the status bridge keeps working.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const BRIDGE_NAME = 'io.github.fabxdev.Apricus.GnomeBridge';
const PATH = '/io/github/fabxdev/Apricus/GnomeBridge';
const INTERFACE = `${BRIDGE_NAME}1`;

const runtime = {
    bus: () => Gio.DBus.session,
    watch: (bus, appeared, vanished) => Gio.bus_watch_name_on_connection(
        bus, BRIDGE_NAME, Gio.BusNameWatcherFlags.NONE, appeared, vanished),
    unwatch: id => Gio.bus_unwatch_name(id),
    later: (seconds, callback) => GLib.timeout_add_seconds(
        GLib.PRIORITY_DEFAULT, seconds, () => { callback(); return GLib.SOURCE_REMOVE; }),
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

export class WindowPush {
    // getFrame(): {seq, fullscreen, maximized} | null. Frames are already
    // debounced and deduplicated by DesktopStateTracker; every call to
    // notify() after a tracker emission sends at most one frame.
    constructor(getFrame, io = runtime) {
        this._getFrame = getFrame;
        this._io = io;
        this._owner = null;
        this._generation = null;
        this._epoch = 0;
        this._sequence = 0;
        this._inflight = false;
        this._stopped = false;
        this._timer = 0;
        this._attempts = 0;
        this._unsupported = false;
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
        this._unsupported = false;
    }

    // Called on every tracker emission; sends immediately (no extra
    // debounce: the tracker already coalesced the storm).
    notify() {
        if (this._stopped || this._inflight)
            return;
        this._flush(false);
    }

    _flush(registering) {
        if (this._stopped || !this._owner || this._inflight || this._unsupported)
            return;
        let frame;
        try { frame = this._getFrame(); }
        catch (error) {
            console.warn(`[ApricusPush] window frame failed: ${error.message}`);
            return;
        }
        if (!frame)
            return;
        this._cancelTimer();
        const isRegistering = registering || !this._generation;
        const method = isRegistering ? 'WindowRegister' : 'WindowState';
        const seq = Math.max(1, Math.floor(frame.seq) || 1);
        const args = isRegistering
            ? [1, seq, !!frame.fullscreen, !!frame.maximized]
            : [this._generation, seq, !!frame.fullscreen, !!frame.maximized];
        const epoch = this._epoch;
        const owner = this._owner;
        this._inflight = true;
        this._io.call(this._bus, owner, method,
            isRegistering ? '(uubb)' : '(subb)', args, (error, reply) => {
                if (epoch !== this._epoch)
                    return;
                this._inflight = false;
                if (!error && isRegistering) {
                    if (!reply || typeof reply[0] !== 'string' || reply[0].length !== 32 ||
                        reply[1] !== 90 || reply[2] !== 30) {
                        error = new Error('unsupported window bridge reply');
                    } else {
                        this._generation = reply[0];
                    }
                }
                if (this._stopped) {
                    this._unregister();
                    return;
                }
                if (error) {
                    const message = String((error && error.message) || error);
                    if (message.includes('UnknownMethod') ||
                        message.includes('org.freedesktop.DBus.Error.UnknownMethod')) {
                        // Older Snap without window push: stay quiet for this
                        // owner epoch. The status bridge is unaffected.
                        this._unsupported = true;
                        console.warn('[ApricusPush] snap bridge has no window push; continuing without it');
                        return;
                    }
                    this._generation = null;
                    console.warn(`[ApricusPush] ${method} failed: ${message}`);
                    if (this._attempts < 3) {
                        const delay = 5 * 2 ** this._attempts++;
                        this._timer = this._io.later(delay, () => {
                            this._timer = 0;
                            this._flush(true);
                        });
                    }
                    return;
                }
                this._attempts = 0;
                if (isRegistering)
                    console.warn(`[ApricusPush] window push registered with ${owner}`);
            });
    }

    _unregister() {
        if (!this._owner || !this._generation || this._inflight)
            return;
        const generation = this._generation;
        this._generation = null;
        this._io.call(this._bus, this._owner, 'WindowUnregister', '(su)',
            [generation, ++this._sequence], error => {
                if (error)
                    console.warn(`[ApricusPush] window unregister unconfirmed: ${error.message}`);
            });
    }

    stop() {
        this._stopped = true;
        this._cancelTimer();
        this._io.unwatch(this._watch);
        if (this._inflight) {
            // Its callback unregisters after ack; a lost reply stays bounded
            // by the Snap-side lease, never a permanent assertion.
        } else {
            this._unregister();
        }
    }
}
