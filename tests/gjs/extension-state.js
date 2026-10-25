// GJS harness: M3 window-state push sensor (no compositor, no D-Bus daemon).
// Executes the REAL DesktopStateTracker/StatePublisher/summarize code from
// state.js with stubbed Shell reads, timers and bus. Run from the repo root:
// gjs -m tests/gjs/extension-state.js
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import {
    BUS_NAME,
    OBJECT_PATH,
    INTERFACE,
    VERSION,
    INTERFACE_XML,
    summarize,
    DesktopStateTracker,
    StatePublisher,
} from '../../gnome-extension/apricus-integration@fabx-dev/state.js';

function assert(condition, message) {
    if (!condition)
        throw new Error(`ASSERT: ${message}`);
}

// ------------------------------------------------------------------ fakes ---

function makeTimer() {
    let nextId = 1;
    const pending = new Map();
    return {
        calls: [],
        later(_seconds, callback) {
            const id = nextId++;
            pending.set(id, callback);
            return id;
        },
        remove(id) {
            pending.delete(id);
        },
        pendingCount() {
            return pending.size;
        },
        fireAll() {
            for (const [, callback] of [...pending])
                callback();
            pending.clear();
        },
    };
}

function makeBus() {
    return {
        owned: [],
        unowned: [],
        exported: [],
        unexported: [],
        emitted: [],
        ownName(name, acquired, _lost) {
            this.owned.push(name);
            if (acquired)
                acquired();
            return 1;
        },
        unownName(id) {
            this.unowned.push(id);
        },
        exportObject(xml, impl, path) {
            this.exported.push({xml, impl, path});
            return {impl, path};
        },
        unexportObject(handle) {
            this.unexported.push(handle);
        },
        emitSignal(path, iface, name, parameters) {
            this.emitted.push({path, iface, name, parameters});
        },
    };
}

// Fake windows: plain descriptors; refs are opaque tokens.
function makeEnv() {
    const wins = [];
    let nextRef = 1;
    const subs = new Map(); // "refId:name" -> [callbacks]
    const env = {
        wins,
        monitors: [{index: 0, primary: true, x: 0, y: 0, width: 1920, height: 1080}],
        activeWorkspace: 0,
        nWorkspaces: 1,
        windowSubs: [],
        windowUnsubs: [],
        busSubs: [],
        busUnsubs: [],
        addWindow(props) {
            const win = {ref: {id: nextRef++}, monitor: 0, minimized: false,
                onActiveWorkspace: true, wallpaper: false, ...props};
            wins.push(win);
            return win;
        },
        readState() {
            return {
                windows: wins.map(w => ({...w})),
                activeWorkspace: env.activeWorkspace,
                nWorkspaces: env.nWorkspaces,
                monitors: env.monitors.map(m => ({...m})),
            };
        },
        fireWindow(ref, name) {
            for (const [key, cbs] of subs) {
                const colon = key.indexOf(':');
                const id = key.slice(0, colon);
                const event = key.slice(colon + 1);
                if (Number(id) === ref.id && event === name) {
                    for (const cb of cbs)
                        cb();
                }
            }
        },
        subscribeWindow(ref, name, cb) {
            const key = `${ref.id}:${name}`;
            if (!subs.has(key))
                subs.set(key, []);
            subs.get(key).push(cb);
            env.windowSubs.push([ref.id, name]);
            return env.windowSubs.length;
        },
        unsubscribeWindow(ref, name, _id) {
            const key = `${ref.id}:${name}`;
            subs.delete(key);
            env.windowUnsubs.push([ref.id, name]);
        },
        subscribeBus(kind, name, cb) {
            env.busSubs.push([kind, name]);
            env[`_cb_${kind}_${name}`] = cb;
            return env.busSubs.length;
        },
        busUnsubs: [],
        unsubscribeBus(kind, id) {
            env.busUnsubs.push([kind, id]);
        },
        fireBus(kind, name) {
            const cb = env[`_cb_${kind}_${name}`];
            if (cb)
                cb();
        },
    };
    return env;
}

function makeTracker(env, timer) {
    const emitted = [];
    const tracker = new DesktopStateTracker(() => env.readState(), {
        codeRevision: 5,
        shellVersion: '50.1',
        extensionVersion: 1,
        emit: payload => emitted.push(payload),
        timer,
        subscribeDisplay: (name, cb) => env.subscribeBus('display', name, cb),
        unsubscribeDisplay: id => env.unsubscribeBus('display', id),
        subscribeWorkspace: (name, cb) => env.subscribeBus('workspace', name, cb),
        unsubscribeWorkspace: id => env.unsubscribeBus('workspace', id),
        subscribeLayout: (name, cb) => env.subscribeBus('layout', name, cb),
        unsubscribeLayout: id => env.unsubscribeBus('layout', id),
        subscribeWindow: (ref, name, cb) => env.subscribeWindow(ref, name, cb),
        unsubscribeWindow: (ref, name, id) => env.unsubscribeWindow(ref, name, id),
    });
    return {tracker, emitted};
}

// ------------------------------------------------------------------ tests ---

// 1. Pre-existing fullscreen window is reported on first flush.
{
    const env = makeEnv();
    const timer = makeTimer();
    const video = env.addWindow({fullscreen: true});
    void video;
    const plain = env.addWindow({});
    void plain;
    const {tracker, emitted} = makeTracker(env, timer);
    tracker.changed();
    assert(timer.pendingCount() === 1, 'debounce scheduled');
    assert(emitted.length === 0, 'no emission before debounce');
    timer.fireAll();
    assert(emitted.length === 1, 'exactly one emission after debounce');
    const [payload] = emitted;
    assert(payload.fullscreen === true, 'fullscreen detected');
    assert(payload.maximized === false, 'not maximized');
    assert(JSON.stringify(payload.fsMonitors) === '[0]', 'monitor index reported');
    assert(payload.seq === 1, 'sequence starts at 1');
    const forbidden = ['title', 'appid', 'app_id', 'pid', 'wm_class', 'get_', 'role', 'uuid'];
    const blob = JSON.stringify(payload);
    for (const word of forbidden)
        assert(!blob.includes(`"${word}"`), `payload must not carry ${word}`);
}

// 2. Rapid flicker coalesces into a single emission; seq is monotonic.
{
    const env = makeEnv();
    const timer = makeTimer();
    const win = env.addWindow({fullscreen: false});
    const {tracker, emitted} = makeTracker(env, timer);
    tracker.changed();
    for (let i = 0; i < 5; i++) {
        win.fullscreen = i % 2 === 0;
        env.fireWindow(win.ref, 'notify::fullscreen');
    }
    win.fullscreen = true;
    timer.fireAll();
    assert(emitted.length === 1, `flicker coalesced, got ${emitted.length}`);
    assert(emitted[0].fullscreen === true, 'latest truth wins');
    win.fullscreen = false;
    tracker.changed();
    timer.fireAll();
    assert(emitted.length === 2 && emitted[1].seq === 2, 'seq increments per emission');
}

// 3. Maximized requires BOTH directions; minimized/off-workspace ignored.
{
    const env = makeEnv();
    const timer = makeTimer();
    const half = env.addWindow({maximizedHorizontally: true});
    const {tracker, emitted} = makeTracker(env, timer);
    tracker.changed();
    timer.fireAll();
    assert(emitted[0].maximized === false, 'half-maximized is not maximized');
    half.maximizedVertically = true;
    tracker.changed();
    timer.fireAll();
    assert(emitted[1].maximized === true, 'fully maximized detected');
    assert(JSON.stringify(emitted[1].maxMonitors) === '[0]', 'max monitor reported');
    half.maximizedHorizontally = false;
    tracker.changed();
    timer.fireAll();
    assert(emitted[2].maximized === false, 'vertical-only is not maximized');
    const hidden = env.addWindow({fullscreen: true, minimized: true});
    const other = env.addWindow({fullscreen: true, onActiveWorkspace: false});
    void hidden;
    void other;
    const visible = env.addWindow({fullscreen: true, monitor: 2});
    tracker.changed();
    timer.fireAll();
    assert(emitted.length === 4, 'visible change still emitted');
    assert(emitted[3].fullscreen === true, 'visible fs window reported');
    assert(JSON.stringify(emitted[3].fsMonitors) === '[2]',
        'only the visible on-workspace window is counted');
}

// 4. Wallpaper windows are never subscribed nor reported.
{
    const env = makeEnv();
    const timer = makeTimer();
    env.addWindow({wallpaper: true, fullscreen: true});
    const {tracker, emitted} = makeTracker(env, timer);
    tracker.changed();
    timer.fireAll();
    assert(emitted[0].fullscreen === false, 'wallpaper window ignored');
    assert(!env.windowSubs.some(([, name]) => true) || env.windowSubs.length === 0,
        'no per-window subscriptions for wallpaper-only set');
}

// 5. Disable/enable: full cleanup, no duplicates on re-enable.
{
    const env = makeEnv();
    const timer = makeTimer();
    const win = env.addWindow({fullscreen: true});
    void win;
    const {tracker} = makeTracker(env, timer);
    tracker.watchBus();
    const busSubsBefore = env.busSubs.length;
    assert(busSubsBefore === 7, `all triggers subscribed, got ${busSubsBefore}`);
    tracker.changed();
    timer.fireAll();
    const winSubsBefore = env.windowSubs.length;
    assert(winSubsBefore === 5, `per-window notifies subscribed, got ${winSubsBefore}`);
    tracker.stop();
    assert(timer.pendingCount() === 0, 'debounce timer removed');
    assert(env.busUnsubs.length === busSubsBefore, 'every bus trigger disconnected');
    assert(env.windowUnsubs.length === winSubsBefore, 'every per-window notify disconnected');
    // A fresh enable cycle subscribes exactly one set again — no leftovers
    // doubling with the previous cycle, and the old tracker's callbacks
    // (if any leaked) are inert because stop() marked it stopped.
    const {tracker: tracker2} = makeTracker(env, timer);
    tracker2.watchBus();
    tracker2.changed();
    timer.fireAll();
    assert(env.busSubs.length === busSubsBefore * 2, 're-enable subscribes a fresh set');
    assert(env.windowSubs.length === winSubsBefore * 2, 'no double subscription per window');
}

// 6. Publisher: export/own on start, signal on publish, cleanup on stop.
{
    const env = makeEnv();
    const timer = makeTimer();
    env.addWindow({fullscreen: true});
    const bus = makeBus();
    const {tracker, emitted} = makeTracker(env, timer);
    const publisher = new StatePublisher(tracker, {
        codeRevision: 5, shellVersion: '50.1', extensionVersion: 1, bus,
    });
    publisher.start();
    assert(bus.owned.includes(BUS_NAME), 'well-known name owned');
    assert(bus.exported.length === 1 && bus.exported[0].path === OBJECT_PATH,
        'interface exported at the documented path');
    assert(bus.exported[0].xml.includes('DesktopStateChanged'), 'signal in XML');
    tracker.changed();
    timer.fireAll();
    assert(emitted.length === 1, 'tracker emitted');
    publisher.publish(emitted[0]);
    assert(bus.emitted.length === 1, 'signal emitted on the bus');
    const sig = bus.emitted[0];
    assert(sig.path === OBJECT_PATH && sig.iface === INTERFACE, 'signal address correct');
    assert(sig.parameters.get_type_string() === '(a{sv})', 'signal signature correct');
    // NOTE: container deepUnpack() is lossy for nested variants in this GJS
    // build (shows {}), while per-key lookup unpacks correctly. The wire
    // format itself is exact — verified key by key below.
    const packet = sig.parameters.get_child_value(0);
    assert(packet.get_type_string() === 'a{sv}', 'payload signature correct');
    const roundtrip = key => packet.lookup_value(key, null).deepUnpack();
    assert(roundtrip('fullscreen') === true, 'signal carries truth');
    assert(typeof roundtrip('seq') === 'number', 'signal carries seq');
    assert(Array.isArray(roundtrip('fs_monitors')), 'signal carries monitor list');
    publisher.stop();
    assert(bus.unexported.length === 1 && bus.unowned.length === 1, 'cleanup on stop');
}

// 7. Interface contract constants.
{
    assert(VERSION === 1, 'interface versioned');
    assert(BUS_NAME === 'org.gnome.Shell.Extensions.ApricusIntegration', 'bus name');
    assert(INTERFACE_XML.includes('version 1'), 'XML carries version comment');
    for (const method of ['GetState', 'Ping'])
        assert(INTERFACE_XML.includes(`name="${method}"`), `XML declares ${method}`);
    assert(Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML) !== null, 'XML parses');
}

// 8. summarize() is pure over plain data (no Shell needed).
{
    const out = summarize([
        {fullscreen: true, maximizedHorizontally: false, maximizedVertically: false,
            monitor: 1, minimized: false, onActiveWorkspace: true, wallpaper: false},
        {fullscreen: false, maximizedHorizontally: true, maximizedVertically: true,
            monitor: 2, minimized: false, onActiveWorkspace: true, wallpaper: false},
        {fullscreen: true, maximizedHorizontally: true, maximizedVertically: true,
            monitor: 0, minimized: true, onActiveWorkspace: true, wallpaper: false},
    ], 0);
    assert(out.fullscreen === true && out.maximized === true, 'flags computed');
    assert(JSON.stringify(out.fsMonitors) === '[1]', 'fs monitor index');
    assert(JSON.stringify(out.maxMonitors) === '[2]', 'max monitor index');
}

// 9. A new ordinary window must be watched even when aggregate truth deduplicates.
{
    const env = makeEnv();
    const timer = makeTimer();
    const {tracker, emitted} = makeTracker(env, timer);
    tracker.refresh();
    const win = env.addWindow({fullscreen: false});
    tracker.changed();
    timer.fireAll();
    assert(emitted.length === 1, 'unchanged aggregate not published');
    win.fullscreen = true;
    env.fireWindow(win.ref, 'notify::fullscreen');
    timer.fireAll();
    assert(emitted.length === 2 && emitted[1].fullscreen, 'new window notifications watched');
    tracker.stop();
}

// 10. Real GLib millisecond timer, no compositor/bus/session mutation.
// Several edges restart the trailing timer. Measure from the LAST edge,
// not from when this process was launched. Upper limit detects the old
// seconds timer; it is a regression bound, not a compositor latency claim.
{
    const env = makeEnv();
    const win = env.addWindow({fullscreen: false});
    const bus = makeBus();
    const times = [];
    let emissionsBeforeStop = 0;
    const loop = new GLib.MainLoop(null, false);
    let lastEdge = GLib.get_monotonic_time();
    let publisher;
    const tracker = new DesktopStateTracker(() => env.readState(), {
        emit: payload => {
            times.push(GLib.get_monotonic_time());
            publisher.publish(payload);
        },
    });
    publisher = new StatePublisher(tracker, {bus});
    publisher.start();
    win.fullscreen = true;
    tracker.changed();
    const edge = delay => GLib.timeout_add(GLib.PRIORITY_DEFAULT, delay, () => {
        lastEdge = GLib.get_monotonic_time();
        tracker.changed();
        return GLib.SOURCE_REMOVE;
    });
    edge(80);
    edge(160);
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 700, () => {
        emissionsBeforeStop = times.length;
        win.fullscreen = false;
        tracker.changed();
        tracker.stop();
        publisher.stop();
        tracker.changed();
        return GLib.SOURCE_REMOVE;
    });
    GLib.timeout_add(GLib.PRIORITY_DEFAULT, 1100, () => {
        loop.quit();
        return GLib.SOURCE_REMOVE;
    });
    loop.run();
    // Assert outside GLib callbacks: exceptions inside a source callback alone
    // can be logged by GJS without producing a nonzero process exit status.
    assert(emissionsBeforeStop === 1, `real timer coalesced to one publish: ${emissionsBeforeStop}`);
    const elapsed = (times[0] - lastEdge) / 1000;
    assert(elapsed >= 240 && elapsed < 500, `trailing debounce ms=${elapsed}`);
    assert(times.length === 1 && bus.emitted.length === 1, 'no callback/publication after stop');
    const packet = bus.emitted[0].parameters.get_child_value(0);
    assert(packet.lookup_value('fullscreen', null).deepUnpack(), 'published fullscreen');
    print(`PASS: real GLib trailing timer ${elapsed.toFixed(1)} ms; one signal`);
}

print('PASS: push sensor reports compositor truth, debounces, cleans up, contracts hold');
