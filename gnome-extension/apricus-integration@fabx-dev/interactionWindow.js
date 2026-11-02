// Changes only an already adopted window of the server's current WebPlayer.
import Clutter from 'gi://Clutter';
import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';
import St from 'gi://St';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';

export class InteractionWindow {
    constructor(extension) {
        this.extension = extension;
        this.session = null;
        this.ack = () => {};
    }

    enter(request) {
        if (this.session) {
            if (this.session.token === request.token)
                this.ack(request.token, 'INTERACTIVE', true);
            else
                this.ack(request.token, 'INTERACTIVE', false);
            return;
        }
        const record = [...this.extension._adopted.values()].find(r =>
            r.window.get_pid() === request.pid && r.window.get_title() === request.title &&
            r.window.get_role() === 'apricus-wallpaper');
        if (!record || record.window.minimized || Main.overview.visible || Main.overview.animationInProgress) {
            this.ack(request.token, 'INTERACTIVE', false);
            return;
        }
        const window = record.window;
        const session = {
            token: request.token, record, type: window.get_window_type(), rect: window.get_frame_rect(),
            fullscreen: window.fullscreen, above: window.is_above(), connections: [],
            action: 0, done: null, groupAdded: false,
        };
        this.session = session;
        record.interactive = true;
        const connect = (object, signal, callback) => session.connections.push([object, object.connect(signal, callback)]);
        try {
            const actor = record.actor;
            actor.get_parent()?.remove_child(actor);
            global.window_group.add_child(actor);
            window.set_type(Meta.WindowType.NORMAL);
            window.make_fullscreen();
            window.make_above();
            global.window_group.set_child_above_sibling(actor, null);

            const label = this.extension.gettext('Done — Return to desktop');
            session.done = new St.Button({label, style_class: 'button', reactive: true, can_focus: true});
            session.done.set_accessible_name(label);
            const monitor = Main.layoutManager.monitors[window.get_monitor()];
            session.done.set_position(monitor.x + 20, monitor.y + 40);
            // true would HIDE this chrome in fullscreen (prototype regression).
            Main.layoutManager.addTopChrome(session.done, {affectsStruts: false, trackFullscreen: false});
            session.done.connect('clicked', () => this.exit('done'));
            Main.ctrlAltTabManager.addGroup(session.done, label, 'web-browser-symbolic');
            session.groupAdded = true;

            session.action = global.display.grab_accelerator('<Super><Shift>Escape', Meta.KeyBindingFlags.NONE);
            if (!session.action)
                throw new Error('exit shortcut is unavailable');
            session.binding = Meta.external_binding_name_for_action(session.action);
            Main.wm.allowKeybinding(session.binding, Shell.ActionMode.ALL);
            connect(global.display, 'accelerator-activated', (_display, action) => {
                if (action === session.action) this.exit('shortcut');
            });
            connect(Main.overview, 'showing', () => this.exit('overview'));
            connect(global.workspace_manager, 'active-workspace-changed', () => this.exit('workspace'));
            connect(Main.layoutManager, 'monitors-changed', () => this.exit('monitors'));
            connect(window, 'unmanaged', () => this.exit('unmanaged'));
            connect(window, 'notify::minimized', () => {
                if (window.minimized) this.exit('minimized');
            });
            window.activate(global.get_current_time());
            connect(global.display, 'notify::focus-window', () => {
                const focused = global.display.focus_window;
                if (focused && focused !== window && !window.is_ancestor_of_transient(focused))
                    this.exit('focus-left');
            });
            this.ack(request.token, 'INTERACTIVE', true, accepted => {
                if (!accepted && this.session === session)
                    this.exit('entry-rejected', false);
            });
        } catch (error) {
            console.warn(`[ApricusInteraction] entry failed: ${error.message}`);
            this.ack(request.token, 'INTERACTIVE', false);
            this.exit('entry-failed');
        }
    }

    exit(reason, notify = true, token = null) {
        const session = this.session;
        if (token && session && token !== session.token)
            return;
        if (!session) {
            if (token && notify) this.ack(token, 'WALLPAPER', true);
            return;
        }
        this.session = null; // Reentrant/later callbacks cannot resurrect it.
        let safe = true;
        const attempt = fn => {
            try { fn(); }
            catch (_error) { safe = false; }
        };
        for (const [object, id] of session.connections)
            attempt(() => object.disconnect(id));
        if (session.action) {
            attempt(() => global.display.ungrab_accelerator(session.action));
            attempt(() => Main.wm.allowKeybinding(session.binding, Shell.ActionMode.NONE));
        }
        if (session.groupAdded)
            attempt(() => Main.ctrlAltTabManager.removeGroup(session.done));
        attempt(() => session.done?.destroy());
        const {window, actor} = session.record;
        attempt(() => { if (!session.above) window.unmake_above(); });
        attempt(() => { if (!session.fullscreen && window.fullscreen) window.unmake_fullscreen(); });
        attempt(() => window.set_type(session.type));
        attempt(() => window.move_resize_frame(false, session.rect.x, session.rect.y,
            session.rect.width, session.rect.height));
        attempt(() => {
            if (actor.get_parent() !== this.extension._bgGroup) {
                actor.get_parent()?.remove_child(actor);
                this.extension._bgGroup.add_child(actor);
            }
            if (window.minimized) window.unminimize(); // Our wallpaper only, never another app.
        });
        session.record.interactive = false;
        if (safe) {
            this.extension._pinOnTop(actor);
        } else {
            // Do not let the normal visible guard undo fail-closed hiding.
            session.record.quarantined = true;
            attempt(() => actor.hide());
        }
        attempt(() => {
            if (global.display.focus_window === window)
                global.display.focus_default_window(global.get_current_time());
        });
        if (notify)
            this.ack(session.token, 'WALLPAPER', safe);
        console.debug(`[ApricusInteraction] returned (${reason}, safe=${safe})`);
    }
}
