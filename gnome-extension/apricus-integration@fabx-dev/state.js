// Copyright (C) 2026 fabx-dev
// SPDX-License-Identifier: GPL-3.0-or-later
//
// Compositor truth sensor: fullscreen/maximized/workspace/monitor state.
//
// Status only. No playback decisions, no playback calls, no window
// identities: the payload carries counts, monitor indices and the workspace
// index — never titles, app IDs, PIDs or window paths.
//
// The tracker is pure logic over an injected Shell access object, so the
// whole lifecycle (debounce, sequence numbers, subscribe/unsubscribe
// symmetry, privacy of the payload) is unit-testable headless. The
// extension wires the real compositor; the test harness wires fakes.
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';

export const BUS_NAME = 'org.gnome.Shell.Extensions.ApricusIntegration';
export const OBJECT_PATH = '/org/gnome/Shell/Extensions/ApricusIntegration';
export const INTERFACE = 'org.gnome.Shell.Extensions.ApricusIntegration';
export const VERSION = 1;

// Trailing-edge debounce: coalesces fullscreen/maximized storms (e.g. rapid
// window switching) into a single emission. The engine applies its own
// debounce on top; the combined budget is verified live (M3 target ≤500 ms).
export const DEBOUNCE_SECONDS = 0.25;

export const INTERFACE_XML = `<node><interface name="${INTERFACE}">
 <!-- version 1 -->
 <method name="GetState"><arg name="state" type="a{sv}" direction="out"/></method>
 <method name="Ping"><arg name="revision" type="u" direction="out"/></method>
 <signal name="DesktopStateChanged"><arg name="state" type="a{sv}"/></signal>
 <property name="Version" type="u" access="read"/>
 <property name="ShellVersion" type="s" access="read"/>
 <property name="ExtensionVersion" type="u" access="read"/>
 <property name="CodeRevision" type="u" access="read"/>
 <property name="FullscreenActive" type="b" access="read"/>
 <property name="MaximizedActive" type="b" access="read"/>
 <property name="FullscreenMonitors" type="au" access="read"/>
 <property name="MaximizedMonitors" type="au" access="read"/>
 <property name="ActiveWorkspace" type="u" access="read"/>
 <property name="NWorkspaces" type="u" access="read"/>
 <property name="Monitors" type="aa{sv}" access="read"/>
 <property name="PushAlive" type="b" access="read"/>
</interface></node>`;

// Window descriptor supplied by readState(). Plain data only; `ref` is an
// opaque handle for (un)subscription and is never serialized.
export function summarize(descriptors, activeWorkspace) {
    const fsMonitors = [];
    const maxMonitors = [];
    for (const window of descriptors) {
        if (window.wallpaper || window.minimized)
            continue;
        if (!window.onActiveWorkspace)
            continue;
        if (window.fullscreen)
            fsMonitors.push(window.monitor);
        if (window.maximizedHorizontally && window.maximizedVertically)
            maxMonitors.push(window.monitor);
    }
    fsMonitors.sort((a, b) => a - b);
    maxMonitors.sort((a, b) => a - b);
    return {
        fullscreen: fsMonitors.length > 0,
        maximized: maxMonitors.length > 0,
        fsMonitors,
        maxMonitors,
        workspace: activeWorkspace,
    };
}

function dictToVariant(payload) {
    const asVariantMonitors = payload.monitors.map(monitor => ({
        index: new GLib.Variant('u', monitor.index),
        primary: new GLib.Variant('b', monitor.primary),
        x: new GLib.Variant('i', monitor.x),
        y: new GLib.Variant('i', monitor.y),
        width: new GLib.Variant('i', monitor.width),
        height: new GLib.Variant('i', monitor.height),
    }));
    return {
        seq: new GLib.Variant('u', payload.seq),
        fullscreen: new GLib.Variant('b', payload.fullscreen),
        maximized: new GLib.Variant('b', payload.maximized),
        fs_monitors: new GLib.Variant('au', payload.fsMonitors),
        max_monitors: new GLib.Variant('au', payload.maxMonitors),
        workspace: new GLib.Variant('u', payload.workspace),
        n_workspaces: new GLib.Variant('u', payload.nWorkspaces),
        monitors: new GLib.Variant('aa{sv}', asVariantMonitors),
    };
}

const defaultTimer = {
    later: (seconds, callback) => GLib.timeout_add(
        GLib.PRIORITY_DEFAULT, Math.max(1, Math.ceil(seconds * 1000)),
        () => { callback(); return GLib.SOURCE_REMOVE; }),
    remove: id => GLib.source_remove(id),
};

const defaultBus = {
    ownName: (name, acquired, lost) => Gio.bus_own_name(
        Gio.BusType.SESSION, name, Gio.BusNameOwnerFlags.NONE,
        null, acquired, lost),
    unownName: id => Gio.bus_unown_name(id),
    exportObject: (xml, impl, path) => {
        const exported = Gio.DBusExportedObject.wrapJSObject(xml, impl);
        exported.export(Gio.DBus.session, path);
        return exported;
    },
    unexportObject: exported => exported.unexport(),
    emitSignal: (path, iface, name, parameters) =>
        Gio.DBus.session.emit_signal(null, path, iface, name, parameters),
};

export class DesktopStateTracker {
    // readState(): {windows, activeWorkspace, nWorkspaces, monitors}.
    // onEmit(payloadDict): receives the payload to publish.
    constructor(readState, options = {}) {
        this._readState = readState;
        this._codeRevision = options.codeRevision ?? 0;
        this._shellVersion = options.shellVersion ?? '?';
        this._extensionVersion = options.extensionVersion ?? 0;
        this._timer = options.timer ?? defaultTimer;
        this._emit = options.emit ?? (() => {});
        this._seq = 0;
        this._timerId = 0;
        this._stopped = false;
        this._lastKey = null;
        this._current = null;
        // Opaque window refs currently subscribed (Set of refs).
        this._watched = new Set();
        // ref -> [{name, id}] per-window subscriptions, for cleanup.
        this._winSubs = new Map();
        this._subscribeWindow = options.subscribeWindow ?? null;
        this._unsubscribeWindow = options.unsubscribeWindow ?? null;
        this._subscribeDisplay = options.subscribeDisplay ?? null;
        this._unsubscribeDisplay = options.unsubscribeDisplay ?? null;
        this._subscribeWorkspace = options.subscribeWorkspace ?? null;
        this._unsubscribeWorkspace = options.unsubscribeWorkspace ?? null;
        this._subscribeLayout = options.subscribeLayout ?? null;
        this._unsubscribeLayout = options.unsubscribeLayout ?? null;
        // [{unsub}] bus-level subscriptions, for cleanup.
        this._busSubs = [];
        this._watching = false;
    }

    // Subscribes display/workspace/monitor triggers. Safe to call once per
    // enable() cycle; the instance is discarded on disable().
    watchBus() {
        if (this._watching)
            return;
        this._watching = true;
        const on = (subscribe, unsubscribe, name) => {
            if (!subscribe)
                return;
            try {
                const id = subscribe(name, () => this.changed());
                if (id)
                    this._busSubs.push([unsubscribe, id]);
            } catch (error) {
                console.warn(`[ApricusPush] subscribe ${name} failed: ${error.message}`);
            }
        };
        for (const name of ['window-created', 'window-entered-monitor', 'window-left-monitor'])
            on(this._subscribeDisplay, this._unsubscribeDisplay, name);
        for (const name of ['active-workspace-changed', 'workspace-added', 'workspace-removed'])
            on(this._subscribeWorkspace, this._unsubscribeWorkspace, name);
        on(this._subscribeLayout, this._unsubscribeLayout, 'monitors-changed');
    }

    changed() {
        if (this._stopped)
            return;
        if (this._timerId)
            this._timer.remove(this._timerId);
        this._timerId = this._timer.later(DEBOUNCE_SECONDS, () => {
            this._timerId = 0;
            this._flush(false);
        });
    }

    _flush(force) {
        if (this._stopped)
            return;
        const snapshot = this._readState();
        // Window lifecycle must be tracked even when aggregate truth is unchanged.
        this._syncWatches(snapshot.windows);
        const summary = summarize(snapshot.windows, snapshot.activeWorkspace);
        const key = JSON.stringify([summary, snapshot.monitors]);
        if (!force && key === this._lastKey)
            return;
        this._lastKey = key;
        this._seq += 1;
        this._current = {
            seq: this._seq,
            ...summary,
            nWorkspaces: snapshot.nWorkspaces,
            monitors: snapshot.monitors,
        };
        this._emit(this._current);
    }

    _syncWatches(windows) {
        if (!this._subscribeWindow)
            return;
        const live = new Set();
        for (const window of windows) {
            if (window.wallpaper)
                continue;
            live.add(window.ref);
            if (!this._watched.has(window.ref)) {
                this._watched.add(window.ref);
                const subs = [];
                for (const name of [
                    'notify::fullscreen',
                    'notify::maximized-horizontally',
                    'notify::maximized-vertically',
                    'notify::minimized',
                    'unmanaged',
                    'workspace-changed',
                ]) {
                    try {
                        const id = this._subscribeWindow(window.ref, name, () => this.changed());
                        subs.push({name, id});
                    } catch (error) {
                        console.warn(`[ApricusPush] window subscribe failed: ${error.message}`);
                    }
                }
                this._winSubs.set(window.ref, subs);
            }
        }
        for (const ref of [...this._watched]) {
            if (!live.has(ref)) {
                this._watched.delete(ref);
                for (const {name, id} of this._winSubs.get(ref) || []) {
                    if (!this._unsubscribeWindow)
                        continue;
                    try {
                        this._unsubscribeWindow(ref, name, id);
                    } catch (error) {
                        console.warn(`[ApricusPush] window unsubscribe failed: ${error.message}`);
                    }
                }
                this._winSubs.delete(ref);
            }
        }
    }

    // Forces an immediate recompute (used by GetState-driven paths in tests;
    // the bus GetState below intentionally does NOT emit).
    refresh() {
        if (this._timerId) {
            this._timer.remove(this._timerId);
            this._timerId = 0;
        }
        this._flush(true);
    }

    getState() {
        const snapshot = this._readState();
        const summary = summarize(snapshot.windows, snapshot.activeWorkspace);
        return {
            seq: this._seq,
            ...summary,
            nWorkspaces: snapshot.nWorkspaces,
            monitors: snapshot.monitors,
        };
    }

    toVariantDict() {
        return dictToVariant(this.getState());
    }

    stop() {
        this._stopped = true;
        if (this._timerId) {
            this._timer.remove(this._timerId);
            this._timerId = 0;
        }
        for (const [unsubscribe, id] of this._busSubs.splice(0)) {
            if (!unsubscribe)
                continue;
            try {
                unsubscribe(id);
            } catch (error) {
                console.warn(`[ApricusPush] bus unsubscribe failed: ${error.message}`);
            }
        }
        for (const [ref, subs] of this._winSubs) {
            for (const {name, id} of subs) {
                if (!this._unsubscribeWindow)
                    continue;
                try {
                    this._unsubscribeWindow(ref, name, id);
                } catch (error) {
                    console.warn(`[ApricusPush] window unsubscribe failed: ${error.message}`);
                }
            }
        }
        this._winSubs.clear();
        this._watched.clear();
        this._watching = false;
    }
}

export class StatePublisher {
    constructor(tracker, options = {}) {
        this._tracker = tracker;
        this._codeRevision = options.codeRevision ?? 0;
        this._shellVersion = options.shellVersion ?? '?';
        this._extensionVersion = options.extensionVersion ?? 0;
        this._bus = options.bus ?? defaultBus;
        this._nameId = 0;
        this._exported = null;
    }

    start() {
        // Every D-Bus-facing value is an explicit GLib.Variant: no
        // marshalling guesses, including for the nested aa{sv} monitors.
        const impl = {
            GetState: () => this._tracker.toVariantDict(),
            Ping: () => this._codeRevision,
            Version: new GLib.Variant('u', VERSION),
            ShellVersion: new GLib.Variant('s', this._shellVersion),
            ExtensionVersion: new GLib.Variant('u', this._extensionVersion),
            CodeRevision: new GLib.Variant('u', this._codeRevision),
            FullscreenActive: new GLib.Variant('b', false),
            MaximizedActive: new GLib.Variant('b', false),
            FullscreenMonitors: new GLib.Variant('au', []),
            MaximizedMonitors: new GLib.Variant('au', []),
            ActiveWorkspace: new GLib.Variant('u', 0),
            NWorkspaces: new GLib.Variant('u', 0),
            Monitors: new GLib.Variant('aa{sv}', []),
            PushAlive: new GLib.Variant('b', true),
        };
        this._impl = impl;
        this._exported = this._bus.exportObject(INTERFACE_XML, impl, OBJECT_PATH);
        this._nameId = this._bus.ownName(
            BUS_NAME,
            () => {},
            () => console.warn('[ApricusPush] bus name lost'));
        this._refreshProperties();
    }

    _refreshProperties() {
        if (!this._impl)
            return;
        const state = this._tracker.getState();
        this._impl.FullscreenActive = new GLib.Variant('b', state.fullscreen);
        this._impl.MaximizedActive = new GLib.Variant('b', state.maximized);
        this._impl.FullscreenMonitors = new GLib.Variant('au', state.fsMonitors);
        this._impl.MaximizedMonitors = new GLib.Variant('au', state.maxMonitors);
        this._impl.ActiveWorkspace = new GLib.Variant('u', state.workspace);
        this._impl.NWorkspaces = new GLib.Variant('u', state.nWorkspaces);
        this._impl.Monitors = dictToVariant(state).monitors;
    }

    publish(state) {
        this._refreshProperties();
        this._bus.emitSignal(OBJECT_PATH, INTERFACE, 'DesktopStateChanged',
            new GLib.Variant('(a{sv})', [dictToVariant(state)]));
    }

    stop() {
        if (this._exported) {
            this._bus.unexportObject(this._exported);
            this._exported = null;
        }
        if (this._nameId) {
            this._bus.unownName(this._nameId);
            this._nameId = 0;
        }
        this._impl = null;
    }
}
