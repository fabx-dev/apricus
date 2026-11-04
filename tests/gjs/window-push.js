// GJS harness: Snap bridge-carried window push (no compositor, no D-Bus).
// Exercises the REAL WindowPush from windowPush.js with a stubbed bridge
// owner: register/state/unregister flows, UnknownMethod tolerance for older
// snaps, no-owner idleness and cleanup on stop. Run from the repo root:
// gjs -m tests/gjs/window-push.js
import {WindowPush, BRIDGE_NAME} from '../../gnome-extension/apricus-integration@fabx-dev/windowPush.js';

function assert(condition, message) {
    if (!condition)
        throw new Error(`ASSERT: ${message}`);
}

function makeBridge() {
    let ownerCb = null;
    let lostCb = null;
    return {
        owner: null,
        calls: [],
        nextReply: 'ok', // 'ok' | {error} | 'unknown-method'
        generation: 'g'.repeat(32),
        io: null,
        install() {
            const self = this;
            this.io = {
                bus: () => ({}),
                watch: (_bus, appeared, vanished) => {
                    ownerCb = appeared;
                    lostCb = vanished;
                    return 1;
                },
                unwatch: _id => {},
                later: (_s, cb) => 0,
                remove: _id => {},
                call: (_bus, _owner, method, _sig, args, cb) => {
                    self.calls.push({method, args});
                    if (self.nextReply === 'unknown-method') {
                        cb(new Error('GDBus.Error:org.freedesktop.DBus.Error.UnknownMethod: no such method'), null);
                    } else if (self.nextReply !== 'ok') {
                        cb(self.nextReply, null);
                    } else if (method === 'WindowRegister') {
                        cb(null, [self.generation, 90, 30]);
                    } else {
                        cb(null, []);
                    }
                },
            };
            return this.io;
        },
        appear(owner) {
            this.owner = owner;
            ownerCb(null, null, owner);
        },
        vanish() {
            this.owner = null;
            lostCb();
        },
    };
}

// 1. No bridge owner: notify() stays idle, nothing sent.
{
    const fake = makeBridge();
    let frame = {seq: 1, fullscreen: true, maximized: false};
    const push = new WindowPush(() => frame, fake.install());
    push.notify();
    assert(fake.calls.length === 0, 'silent without a bridge owner');
    push.stop();
}

// 2. Register then state on each tracker emission, in order.
{
    const fake = makeBridge();
    let frame = {seq: 1, fullscreen: true, maximized: false};
    const push = new WindowPush(() => frame, fake.install());
    fake.appear(':1.40');
    assert(fake.calls.length === 1 && fake.calls[0].method === 'WindowRegister',
        'register on appear');
    assert(JSON.stringify(fake.calls[0].args) === '[1,1,true,false]',
        'register carries version, seq and flags');
    frame = {seq: 2, fullscreen: false, maximized: true};
    push.notify();
    assert(fake.calls.length === 2 && fake.calls[1].method === 'WindowState',
        'state follows emissions');
    assert(fake.calls[1].args[0] === fake.generation, 'state keyed by generation');
    assert(fake.calls[1].args[1] === 2, 'sequence advances');
    push.stop();
    const last = fake.calls[fake.calls.length - 1];
    assert(last.method === 'WindowUnregister', 'disable unregisters');
    assert(last.args[0] === fake.generation, 'unregister keyed by generation');
}

// 3. Older Snap without window push: UnknownMethod quiets this channel only.
{
    const fake = makeBridge();
    fake.nextReply = 'unknown-method';
    let frame = {seq: 1, fullscreen: false, maximized: true};
    const push = new WindowPush(() => frame, fake.install());
    fake.appear(':1.41');
    assert(fake.calls.length === 1, 'register attempted once');
    const before = fake.calls.length;
    push.notify();
    push.notify();
    assert(fake.calls.length === before, 'no retry storm against old snap');
    push.stop();
    assert(!fake.calls.some(c => c.method === 'WindowUnregister'),
        'no unregister without a generation');
}

// 4. Owner loss resets the generation; re-appear re-registers.
{
    const fake = makeBridge();
    let frame = {seq: 9, fullscreen: true, maximized: true};
    const push = new WindowPush(() => frame, fake.install());
    fake.appear(':1.42');
    assert(fake.calls.filter(c => c.method === 'WindowRegister').length === 1,
        'first registration');
    fake.vanish();
    fake.appear(':1.43');
    assert(fake.calls.filter(c => c.method === 'WindowRegister').length === 2,
        're-registers on new owner');
    push.stop();
}

// 5. A broken frame source never breaks the push object.
{
    const fake = makeBridge();
    const push = new WindowPush(() => { throw new Error('no state'); }, fake.install());
    fake.appear(':1.44');
    assert(fake.calls.length === 0, 'broken snapshot sends nothing');
    push.stop();
    assert(BRIDGE_NAME === 'io.github.fabxdev.Apricus.GnomeBridge', 'bridge name pinned');
}

print('PASS: snap bridge-carried window push registers, streams, tolerates old snaps, cleans up');
