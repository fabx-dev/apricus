// Runs the production controller/client with deterministic Shell/transport stubs.
import GLib from 'gi://GLib';
import {InteractionClient, validRequest} from '../../gnome-extension/apricus-integration@fabx-dev/interactionClient.js';

function assert(value, message) { if (!value) throw new Error(message); }
let id = 0;
function events(object = {}) {
    object.handlers = new Map();
    object.connect = (signal, callback) => { object.handlers.set(++id, [signal, callback]); return id; };
    object.disconnect = key => object.handlers.delete(key);
    return object;
}
function group() {
    return {add_child(actor) { actor.parent = this; }, remove_child(actor) { actor.parent = null; },
        set_child_above_sibling() {}};
}
const token = 'a'.repeat(32);
const request = {version: 1, state: 'ENTERING', token, pid: 42, title: `Apricus Web [${token}]`};
assert(validRequest(request), 'valid server descriptor');
assert(!validRequest({...request, pid: 0}), 'reject invalid pid');
assert(!validRequest({...request, title: 'arbitrary'}), 'reject generic window title');
const bg = group();
const rect = {x: 0, y: 0, width: 1280, height: 720};
const actor = {parent: bg, get_parent() { return this.parent; }, hide() { this.hidden = true; }};
const window = events({
    minimized: false, fullscreen: false, above: false, type: 1,
    get_pid: () => 42, get_title: () => request.title, get_role: () => 'apricus-wallpaper',
    get_window_type() { return this.type; }, get_frame_rect: () => rect, is_above() { return this.above; },
    set_type(value) { this.type = value; }, make_fullscreen() { this.fullscreen = true; },
    unmake_fullscreen() { this.fullscreen = false; }, make_above() { this.above = true; },
    unmake_above() { this.above = false; }, move_resize_frame(...args) { this.restoredRect = args; },
    get_monitor: () => 0, activate() { global.display.focus_window = this; },
    is_ancestor_of_transient: () => false,
});
const record = {window, actor};
const global = {display: events({
    grab_accelerator: () => 99, ungrab_accelerator() { this.ungrabbed = true; },
    focus_default_window() { this.focus_window = null; },
}), window_group: group(), workspace_manager: events(), get_current_time: () => 1};
const Main = {
    overview: events({visible: false, animationInProgress: false}),
    layoutManager: events({monitors: [{x: 0, y: 0}], addTopChrome(button, flags) {
        assert(flags.trackFullscreen === false, 'Done must remain above fullscreen');
    }}),
    ctrlAltTabManager: {addGroup() {}, removeGroup() {}}, wm: {allowKeybinding() {}},
};
const Meta = {WindowType: {NORMAL: 0, DESKTOP: 1}, KeyBindingFlags: {NONE: 0}, external_binding_name_for_action: () => 'test'};
const Shell = {ActionMode: {ALL: 1, NONE: 0}};
const St = {Button: class {
    constructor() { events(this); }
    set_accessible_name() {} set_position() {} destroy() { this.destroyed = true; }
}};
const [, bytes] = GLib.file_get_contents(`${GLib.get_current_dir()}/gnome-extension/apricus-integration@fabx-dev/interactionWindow.js`);
let source = new TextDecoder().decode(bytes).replace(/^import .*;$/gm, '');
source = source.replace('export class InteractionWindow', 'globalThis.TestController = class InteractionWindow');
eval(source);
const ext = {_adopted: new Map([[window, record]]), _bgGroup: bg, gettext: text => text, _pinOnTop() {}};
const controller = new globalThis.TestController(ext);
delete globalThis.TestController;
const acknowledgements = [];
controller.ack = (...args) => { acknowledgements.push(args.slice(0, 3)); args[3]?.(true); };
controller.enter({...request, pid: 43});
assert(!controller.session && acknowledgements.at(-1)[2] === false, 'reject non-player window');
Main.overview.animationInProgress = true;
controller.enter(request);
assert(!controller.session, 'reject Overview transition');
Main.overview.animationInProgress = false;
for (let cycle = 0; cycle < 3; cycle++) {
    controller.enter(request);
    const session = controller.session;
    assert(session && record.interactive && window.type === 0 && window.above, 'promotion');
    controller.exit('stale', true, 'b'.repeat(32));
    assert(controller.session === session, 'ignore stale exit');
    controller.exit('done');
    controller.exit('duplicate');
    assert(!controller.session && !record.interactive && window.type === 1 && !window.above && !window.fullscreen, 'restore');
    assert(actor.parent === bg && session.done.destroyed && global.display.ungrabbed, 'native cleanup');
    assert(window.handlers.size === 0 && global.display.handlers.size === 0, 'disconnect handlers');
}
controller.enter(request);
window.set_type = () => { throw new Error('simulated Mutter failure'); };
controller.exit('failure');
assert(record.quarantined && actor.hidden && acknowledgements.at(-1)[2] === false, 'fail closed');
controller.exit('new controller cannot certify old promotion', true, token);
assert(acknowledgements.at(-1)[2] === false, 'missing snapshot cannot certify NORMAL window');

// Actual asynchronous client: single held read, epoch/generation guards, no retry loop.
let appeared, vanished;
const calls = [];
const io = {
    watch(a, v) { appeared = a; vanished = v; return 1; }, unwatch() {},
    cancellable: () => ({cancel() {}}),
    call(owner, method, signature, args, timeout, cancel, callback) { calls.push({owner, method, args, callback}); },
};
const handled = [];
const client = new InteractionClient({enter: r => handled.push(r), exit: r => handled.push(r)}, io);
appeared(':1.5');
assert(calls.at(-1).method === 'InteractionOpen', 'open authenticated generation');
calls.at(-1).callback(null, ['c'.repeat(32)]);
assert(calls.at(-1).method === 'InteractionWatch', 'single outstanding watch');
const firstRead = calls.at(-1);
firstRead.callback(null, [1, JSON.stringify(request)]);
assert(handled.at(-1).token === token && calls.at(-1).method === 'InteractionWatch', 'deliver and rearm');
const lateRead = calls.at(-1);
vanished();
const length = handled.length;
lateRead.callback(null, [2, JSON.stringify(request)]);
assert(handled.length === length, 'discard old owner callback');
client.stop();
print('PASS: web interaction identity, rollback, native cleanup, generation and late responses');
