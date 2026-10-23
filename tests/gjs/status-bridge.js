// Run: gjs -m tests/gjs/status-bridge.js (no Shell, display, or real bus).
import {StatusBridge} from '../../gnome-extension/apricus-integration@fabx-dev/statusBridge.js';

function assert(value, message) {
    if (!value)
        throw new Error(message);
}

function fixture() {
    let nextId = 1;
    const timers = new Map();
    const calls = [];
    const snapshot = [true, true, 0];
    const io = {
        bus: () => ({}),
        watch: (_bus, appeared, vanished) => {
            io.appeared = appeared;
            io.vanished = vanished;
            return 1;
        },
        unwatch: () => { io.unwatched = true; },
        later: (seconds, callback) => {
            const id = nextId++;
            timers.set(id, {seconds, callback});
            return id;
        },
        idle: callback => io.later(0, callback),
        remove: id => timers.delete(id),
        call: (_bus, owner, method, signature, args, callback) => {
            calls.push({owner, method, signature, args, callback});
        },
    };
    const bridge = new StatusBridge(() => snapshot.slice(), io);
    function run(seconds) {
        for (const [id, item] of timers) {
            if (item.seconds === seconds) {
                timers.delete(id);
                item.callback();
                return;
            }
        }
        throw new Error(`No timer ${seconds}`);
    }
    function ack(index, error = null) {
        const call = calls[index];
        call.callback(error, call.method === 'Register' ? ['a'.repeat(32), 90, 30] : []);
    }
    return {io, bridge, snapshot, calls, timers, run, ack};
}

{
    const f = fixture();
    assert(f.calls.length === 0, 'No polling/registration while service absent');
    f.io.appeared(null, null, ':1.100');
    assert(f.calls[0].method === 'Register', 'Apricus starts after extension');
    assert(JSON.stringify(f.calls[0].args) === '[1,1,true,true,0]', 'Zero wallpaper is operational');
    f.ack(0);
    f.run(0);
    assert(f.calls.length === 1, 'No duplicate after registration');
    f.run(30);
    assert(f.calls[1].method === 'Update' && f.calls[1].args[1] === 2, 'Renew at 30 seconds');
    f.ack(1);
    f.run(0);
    f.snapshot[2] = 2;
    f.bridge.changed();
    f.bridge.changed();
    f.run(0);
    assert(f.calls.length === 3 && f.calls[2].args[4] === 2, 'Coalesced adoption update');
    f.ack(2);
    f.run(0);
    f.io.vanished();
    assert(f.timers.size === 0, 'No renew after owner loss');
    f.io.appeared(null, null, ':1.101');
    assert(f.calls[3].method === 'Register' && f.calls[3].owner === ':1.101', 'Server restart');
    f.ack(3);
    f.bridge.stop();
    assert(f.calls[4].method === 'Unregister', 'Disable unregisters');
    assert(f.io.unwatched && f.timers.size === 0, 'No timer or watcher after disable');
}

{
    const f = fixture();
    f.io.appeared(null, null, ':1.100');
    f.bridge.stop();
    f.ack(0);
    assert(f.calls[1].method === 'Unregister', 'Disable during Register waits for ack then unregisters');
    assert(f.timers.size === 0, 'No renew after stop during register');
}

{
    const f = fixture();
    f.io.appeared(null, null, ':1.100');
    f.io.vanished();
    f.io.appeared(null, null, ':1.101');
    f.ack(0);
    assert(f.timers.size === 0, 'Old owner reply cannot renew new registration');
    f.ack(1);
    f.run(0);
    for (let index = 2; index <= 5; index++) {
        f.run(index === 2 ? 30 : 5 * 2 ** (index - 3));
        f.ack(index, new Error('service interrupted'));
    }
    assert(f.timers.size === 0, 'Recovery attempts are bounded');
    f.bridge.stop();
}

{
    const f = fixture();
    f.io.appeared(null, null, ':1.100');
    f.ack(0);
    f.run(0);
    f.bridge._snapshot = () => { throw new Error('integration failure'); };
    f.run(30);
    assert(f.calls.length === 1 && f.timers.size === 0,
        'Broken snapshot does not renew a green lease');
    f.bridge.stop();
}

print('PASS: register, absent bridge, coalescing, renew, owner changes, stop races, bounded recovery, failed snapshot');
