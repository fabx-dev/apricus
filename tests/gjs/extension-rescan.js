// GJS harness: enable()-time re-adoption of pre-existing wallpaper windows.
// Executes the REAL enable()/disable()/_watchWindow()/_tryAdopt() code from
// extension.js with stubbed Shell globals (no compositor, no display).
// Run from the repo root: gjs -m tests/gjs/extension-rescan.js
import GLib from 'gi://GLib';

const SRC = `${GLib.get_current_dir()}/gnome-extension/apricus-integration@fabx-dev/extension.js`;

function assert(condition, message) {
    if (!condition)
        throw new Error(`ASSERT: ${message}`);
}

// ---------------------------------------------------------------- stubs ---

let nextId = 1;

const TestEnv = {
    actors: [],
    tabList: [],
    activated: [],
};

const Meta = {
    TabList: {NORMAL: 0},
    Display: {prototype: {
        get_tab_list(_type, _workspace) {
            return TestEnv.tabList.slice();
        },
    }},
};

class InjectionManager {
    constructor() {
        this._saved = [];
    }

    overrideMethod(obj, name, factory) {
        const orig = obj[name];
        this._saved.push([obj, name, orig]);
        obj[name] = factory(orig);
    }

    clear() {
        for (const [obj, name, orig] of this._saved.splice(0)) {
            if (orig === undefined)
                delete obj[name];
            else
                obj[name] = orig;
        }
    }
}

class Extension {
}

const Workspace = {prototype: {}};
const WorkspaceThumbnail = {prototype: {}};
const SHELL_VERSION = '50.1';

class StatusBridge {
    constructor(_snapshot) {
        this.stops = 0;
        this.changes = 0;
    }

    changed() {
        this.changes++;
    }

    stop() {
        this.stops++;
    }
}

class DesktopStateTracker {
    constructor(readState, options) {
        this.stops = 0;
        this.watches = 0;
        this.changes = 0;
        this.options = options || {};
        DesktopStateTracker.instances.push(this);
    }

    watchBus() {
        this.watches++;
    }

    changed() {
        this.changes++;
    }

    stop() {
        this.stops++;
    }
}
DesktopStateTracker.instances = [];

class StatePublisher {
    constructor(tracker, _options) {
        this.stops = 0;
        this.starts = 0;
        this.published = [];
        StatePublisher.instances.push(this);
    }

    start() {
        this.starts++;
    }

    publish(payload) {
        this.published.push(payload);
    }

    stop() {
        this.stops++;
    }
}
StatePublisher.instances = [];

function makeGroup(name) {
    const handlers = {};
    return {
        _name: name,
        children: [],
        add_child(actor) {
            if (actor._parent)
                actor._parent.remove_child(actor);
            actor._parent = this;
            this.children.push(actor);
            for (const id of Object.keys(handlers)) {
                const [event, cb] = handlers[id];
                if (event === 'child-added')
                    cb(this, actor);
            }
        },
        remove_child(actor) {
            this.children = this.children.filter(a => a !== actor);
            if (actor._parent === this)
                actor._parent = null;
        },
        set_child_above_sibling(actor, _sibling) {
            this.children = this.children.filter(a => a !== actor);
            this.children.push(actor);
        },
        connect(name, cb) {
            const id = nextId++;
            handlers[id] = [name, cb];
            return id;
        },
        disconnect(id) {
            delete handlers[id];
        },
    };
}

const windowGroup = makeGroup('window_group');
const bgGroup = makeGroup('background');

const Main = {
    layoutManager: {_backgroundGroup: bgGroup},
    overview: {connect: () => nextId++, disconnect() {}},
    extensionManager: {lookup: () => ({state: 1})},
};

const Shell = {
    Global: {prototype: {
        get_window_actors() {
            return TestEnv.actors.slice();
        },
    }},
    WindowTracker: {prototype: {}},
    App: {prototype: {}},
    AppSystem: {prototype: {}},
};

const displayHandlers = {};
const display = Object.create(Meta.Display.prototype);
display.connect = (name, cb) => {
    const id = nextId++;
    displayHandlers[id] = [name, cb];
    return id;
};
display.disconnect = id => {
    delete displayHandlers[id];
};

function emitWindowCreated(win) {
    for (const id of Object.keys(displayHandlers)) {
        const [name, cb] = displayHandlers[id];
        if (name === 'window-created')
            cb(display, win);
    }
}

const global = Object.create(Shell.Global.prototype);
global.display = display;
global.workspace_manager = {connect: () => nextId++, disconnect() {}};
global.window_group = windowGroup;
global.get_current_time = () => 424242;

function makeWindow({role = null, title = 'test'} = {}) {
    return {
        _role: role,
        _title: title,
        _actor: null,
        _handlers: {},
        _lowered: 0,
        get_role() {
            return this._role;
        },
        get_title() {
            return this._title;
        },
        get_compositor_private() {
            return this._actor;
        },
        lower() {
            this._lowered++;
        },
        connect(name, cb) {
            const id = nextId++;
            this._handlers[id] = [name, cb];
            return id;
        },
        connect_after(name, cb) {
            const id = nextId++;
            this._handlers[id] = [name, cb];
            return id;
        },
        disconnect(id) {
            delete this._handlers[id];
        },
        activate(_time) {
            TestEnv.activated.push(this);
        },
    };
}

function makeActor(win) {
    const actor = {
        _window: win,
        _parent: null,
        _handlers: {},
        visible: true,
        get_meta_window() {
            return this._window;
        },
        get_parent() {
            return this._parent;
        },
        connect(name, cb) {
            const id = nextId++;
            this._handlers[id] = [name, cb];
            return id;
        },
        disconnect(id) {
            delete this._handlers[id];
        },
        show() {
            this.visible = true;
        },
    };
    win._actor = actor;
    return actor;
}

// ------------------------------------------------- load real code ---

function loadExtensionClass() {
    const [, bytes] = GLib.file_get_contents(SRC);
    let src = new TextDecoder().decode(bytes);
    for (const line of [
        "import GLib from 'gi://GLib';",
        "import Meta from 'gi://Meta';",
        "import Shell from 'gi://Shell';",
        "import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';",
        "import {PACKAGE_VERSION as SHELL_VERSION} from 'resource:///org/gnome/shell/misc/config.js';",
        "import * as Main from 'resource:///org/gnome/shell/ui/main.js';",
        "import {Workspace} from 'resource:///org/gnome/shell/ui/workspace.js';",
        "import {WorkspaceThumbnail} from 'resource:///org/gnome/shell/ui/workspaceThumbnail.js';",
        "import {StatusBridge} from './statusBridge.js';",
        "import {DesktopStateTracker, StatePublisher} from './state.js';",
    ]) {
        if (!src.includes(line))
            throw new Error(`expected import line missing (harness out of date): ${line}`);
        src = src.replace(line, '');
    }
    src = src.replace('export default', 'globalThis.__ExtensionClass =');
    eval(src);
    const cls = globalThis.__ExtensionClass;
    delete globalThis.__ExtensionClass;
    return cls;
}

// ------------------------------------------------------------------ test ---

const ExtensionClass = loadExtensionClass();

// Pre-existing windows: one wallpaper window (already mapped, actor live)
// and one normal window. NO window-created events are emitted for them.
const videoWin = makeWindow({role: 'apricus-wallpaper', title: 'apricus'});
const videoActor = makeActor(videoWin);
windowGroup.add_child(videoActor);
const plainWin = makeWindow({role: null, title: 'terminal'});
const plainActor = makeActor(plainWin);
windowGroup.add_child(plainActor);
TestEnv.actors = [videoActor, plainActor];
TestEnv.tabList = [videoWin, plainWin];

const ext = new ExtensionClass();
ext.uuid = 'apricus-integration@fabx-dev';
ext.enable();

// 1. Pre-existing wallpaper window adopted on enable, without window-created.
// (Combined with check 3 below — the post-enable actor list is filtered —
// this proves the scan ran on the pre-captured unfiltered list: with the
// filtered list it could never have matched.)
assert(videoActor._parent === bgGroup,
    'pre-existing wallpaper window must be adopted into the background group');
assert(ext._adopted.size === 1, 'exactly one window adopted');
assert(ext._adopted.has(videoWin), 'adopted record tracks the wallpaper window');

// 2. Normal window untouched.
assert(plainActor._parent === windowGroup, 'non-wallpaper window must stay put');
assert(!ext._adopted.has(plainWin), 'non-wallpaper window must not be adopted');

// 3. Overview-hiding filter installed afterwards: post-enable global
// get_window_actors() hides the wallpaper actor — proving the scan could
// only have succeeded from the pre-captured (unfiltered) list.
const visibleAfter = global.get_window_actors();
assert(!visibleAfter.includes(videoActor), 'filter must hide wallpaper post-enable');
assert(visibleAfter.includes(plainActor), 'filter must keep normal windows');

// 4. Refocus used the (filtered) tab list: plain window activated, video not.
assert(TestEnv.activated.includes(plainWin), 'focus must move to a normal window');
assert(!TestEnv.activated.includes(videoWin), 'wallpaper window must never activate');

// 5. Late window-created for the same window: no double adoption.
const handlerCount = Object.keys(videoWin._handlers).length;
const addsBefore = bgGroup.children.length;
emitWindowCreated(videoWin);
assert(bgGroup.children.length === addsBefore, 'no duplicate reparenting');
assert(Object.keys(videoWin._handlers).length === handlerCount, 'no duplicate handlers');

// 6. Genuinely new wallpaper window adopts via window-created.
const videoWin2 = makeWindow({role: 'apricus-wallpaper', title: 'apricus-2'});
const videoActor2 = makeActor(videoWin2);
windowGroup.add_child(videoActor2);
TestEnv.actors.push(videoActor2);
emitWindowCreated(videoWin2);
assert(videoActor2._parent === bgGroup, 'new wallpaper window must adopt on window-created');
assert(ext._adopted.size === 2, 'both wallpaper windows tracked');

// 7. Push wiring: the tracker's change notifications must reach the
// publisher (this exact wiring was missing once; the sensor computed
// truth nobody ever published). Checked before disable(), while the
// wiring is live.
assert(DesktopStateTracker.instances.length === 1, 'one tracker per enable');
const wiredOptions = DesktopStateTracker.instances[0].options;
assert(typeof wiredOptions.emit === 'function',
    'tracker must be constructed with an emit callback');
assert(StatePublisher.instances.length === 1, 'one publisher per enable');
const wiredPublisher = StatePublisher.instances[0];
wiredOptions.emit({seq: 7, fullscreen: true, note: 'wiring-probe'});
assert(wiredPublisher.published.length === 1,
    'tracker emission must reach StatePublisher.publish');
assert(wiredPublisher.published[0].seq === 7, 'payload passes through unwrapped');

// 8. disable() hands every actor back and clears the overrides.
ext.disable();
assert(videoActor._parent === windowGroup, 'first actor released on disable');
assert(videoActor2._parent === windowGroup, 'second actor released on disable');
assert(ext._adopted.size === 0, 'adopted map cleared on disable');
assert(global.get_window_actors().includes(videoActor),
    'filter overrides must be removed on disable');

print('PASS: enable() re-adopts pre-existing wallpaper windows; no double adoption; disable releases; overrides restored');
