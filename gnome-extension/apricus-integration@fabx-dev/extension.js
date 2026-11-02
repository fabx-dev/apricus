// Apricus Wallpaper Integration — GNOME Shell side.
//
// Hosts Apricus live-wallpaper windows (owned by the Apricus player
// process) in GNOME Shell's background layer, strictly below desktop-icon
// windows (DING) and application windows.
//
// The player marks its wallpaper windows with WM_WINDOW_ROLE
// "apricus-wallpaper" before mapping them; this extension adopts every
// window carrying that role by reparenting its compositor actor into
// Main.layoutManager._backgroundGroup. Identification is role-based on
// purpose: the settings window and file choosers never carry the role,
// so they can never be adopted by mistake.
//
// Two hard-won requirements shape this design (both verified live on
// GNOME 50.1 / Mutter 18 with DING):
// 1. The player must render into the X pixmap (VLC `--vout=xcb`).
//    GPU-direct outputs bypass the pixmap via overlay planes that no
//    stacking order can place below DING, and screenshots/XDamage only
//    ever see black.
// 2. With pixmap rendering, in-place lowering cannot order an XWayland
//    desktop window below DING's native Wayland one, so the actor must
//    live in the background layer. The layer is correct; the renderer
//    must feed it.
// This approach is adapted from wallpaperEngineWayland (GPL-2.0-or-later,
// https://github.com/diaslucass/wallpaperEngineWayland): same background-
// group adoption, same Overview/Alt-Tab hiding, same re-pin on background
// recreation. Unlike that project the renderer process is NOT spawned
// here: Apricus owns playback (VLC/XWayland), this extension only places
// actors.

import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import {PACKAGE_VERSION as SHELL_VERSION} from 'resource:///org/gnome/shell/misc/config.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Workspace} from 'resource:///org/gnome/shell/ui/workspace.js';
import {WorkspaceThumbnail} from 'resource:///org/gnome/shell/ui/workspaceThumbnail.js';
import {StatusBridge} from './statusBridge.js';
import {WindowPush} from './windowPush.js';
import {DesktopStateTracker, StatePublisher} from './state.js';
import {InteractionClient} from './interactionClient.js';
import {InteractionWindow} from './interactionWindow.js';

// Must match WALLPAPER_WINDOW_ROLE in src/apricus/gnome_shell.py and the
// role set by the player before mapping (Gtk.Window.set_role).
const WALLPAPER_ROLE = 'apricus-wallpaper';

// Bumped on every code change; logged from enable() so the journal shows
// which revision the running shell actually loaded.
const CODE_REVISION = 8;

const WINDOW_MATCH_RETRIES = 10;
const WINDOW_MATCH_INTERVAL_MS = 100;

export default class ApricusIntegrationExtension extends Extension {
    enable() {
        this._destroyed = false;
        // MetaWindow -> { actor, unmanagedId, raisedId, focusId, visibleId, destroyId }
        this._adopted = new Map();
        // MetaWindow -> { attempts, sourceId }
        this._pending = new Map();
        this._bgChildAddedId = 0;
        this._windowCreatedId = 0;
        this._restackedId = 0;
        this._workspaceChangedId = 0;
        this._overviewHiddenId = 0;
        this._operational = false;
        // Loaded is not the same as operational; zero adopted windows is valid.
        try {
            this._statusBridge = new StatusBridge(() => {
                const state = Main.extensionManager.lookup(this.uuid)?.state;
                // Shell ExtensionState.ENABLED == 1. Do not renew a green
                // lease if Shell marked the extension erroneous or its
                // background/injection setup is no longer present.
                const operational = this._operational && state === 1 &&
                    this._bgGroup === Main.layoutManager._backgroundGroup &&
                    Boolean(this._bgGroup && this._injectionManager);
                return [!this._destroyed, operational, this._adopted.size];
            });
        } catch (error) {
            console.warn(`[ApricusBridge] unavailable: ${error.message}`);
        }

        console.warn(`[${this.uuid}] enabled, code revision ${CODE_REVISION}`);

        this._bgGroup = Main.layoutManager._backgroundGroup ?? null;
        if (!this._bgGroup) {
            console.error(`[${this.uuid}] no background group on this Shell version; staying inert`);
            return;
        }

        this._windowCreatedId = global.display.connect(
            'window-created', (_display, window) => this._watchWindow(window));

        // Background recreation (settings, monitors, login transition) adds
        // new static actors on top of the group, which would bury the video:
        // re-pin on restacks, workspace switches and overview hides too.
        this._restackedId = global.display.connect('restacked', () => this._repinAll());
        this._workspaceChangedId = global.workspace_manager.connect(
            'active-workspace-changed', () => this._repinAll());
        this._overviewHiddenId = Main.overview.connect('hidden', () => this._repinAll());

        // Capture the actor list BEFORE installing the Overview-hiding
        // overrides below: get_window_actors() is filtered by them, so a
        // scan issued afterwards could never see pre-existing wallpaper
        // windows and they would stay unadopted above DING after a reload.
        const preexistingActors = global.get_window_actors();

        this._injectionManager = new InjectionManager();
        this._installOverviewHiding();

        // Compositor truth sensor (M3): tracks fullscreen/maximized per
        // window and publishes it on D-Bus. No playback logic here; the
        // server decides. Best effort: a failed sensor must never break
        // adoption or stacking.
        try {
            const shellVersion = (typeof SHELL_VERSION !== 'undefined' && SHELL_VERSION) || '?';
            const extensionVersion = Number(this.metadata?.version ?? 1) || 1;
            this._stateTracker = new DesktopStateTracker(
                () => this._readDesktopState(),
                {
                    codeRevision: CODE_REVISION,
                    shellVersion,
                    extensionVersion,
                    // State changes detected by the tracker are published on
                    // D-Bus here: without this wiring the tracker would
                    // compute truth nobody ever receives.
                    emit: payload => {
                        this._statePublisher?.publish(payload);
                        // Strict Snap confinement cannot subscribe to our
                        // signals; the same frame is pushed as a method call
                        // to the Snap-owned bridge (best effort, silent when
                        // no Snap bridge owner exists).
                        try { this._windowPush?.notify(); }
                        catch (error) {
                            console.warn(`[ApricusPush] snap push failed: ${error.message}`);
                        }
                    },
                    subscribeDisplay: (name, callback) => global.display.connect(name, callback),
                    unsubscribeDisplay: id => global.display.disconnect(id),
                    subscribeWorkspace: (name, callback) => global.workspace_manager.connect(name, callback),
                    unsubscribeWorkspace: id => global.workspace_manager.disconnect(id),
                    subscribeLayout: (name, callback) => Main.layoutManager.connect(name, callback),
                    unsubscribeLayout: id => Main.layoutManager.disconnect(id),
                    subscribeWindow: (ref, name, callback) => ref.connect(name, callback),
                    unsubscribeWindow: (ref, _name, id) => ref.disconnect(id),
                });
            this._statePublisher = new StatePublisher(this._stateTracker, {
                codeRevision: CODE_REVISION,
                shellVersion,
                extensionVersion,
            });
            this._stateTracker.watchBus();
            this._statePublisher.start();
            // Snap-bridge window push: same frames as method calls to the
            // Snap-owned bridge name. Absent Snap GUI: the watch never fires
            // and this stays idle without touching adoption or stacking.
            try {
                this._windowPush = new WindowPush(() => {
                    const state = this._stateTracker?.getState();
                    return state ? {
                        seq: state.seq,
                        fullscreen: state.fullscreen,
                        maximized: state.maximized,
                    } : null;
                });
            } catch (error) {
                console.warn(`[ApricusPush] snap push unavailable: ${error.message}`);
                this._windowPush = null;
            }
        } catch (error) {
            console.warn(`[ApricusPush] unavailable: ${error.message}`);
            this._stateTracker = null;
            this._statePublisher = null;
        }

        // The player may already run (started before the extension was
        // enabled, or re-enabled after unlock): adopt matching windows now.
        let seen = 0, matched = 0;
        for (const actor of preexistingActors) {
            const window = actor ? actor.get_meta_window() : null;
            if (window) {
                seen++;
                if (this._isWallpaperWindow(window))
                    matched++;
                this._watchWindow(window);
            }
        }
        console.warn(`[${this.uuid}] startup scan: ${seen} windows, ${matched} wallpaper role match(es)`);
        this._operational = true;
        this._interactionWindow = new InteractionWindow(this);
        this._interactionClient = new InteractionClient(this._interactionWindow);
        this._interactionWindow.ack = (...args) => this._interactionClient.ack(...args);
        this._statusBridge?.changed();
    }

    disable() {
        this._interactionClient?.stop();
        this._interactionClient = null;
        this._interactionWindow = null;
        this._stateTracker?.stop();
        this._stateTracker = null;
        this._statePublisher?.stop();
        this._statePublisher = null;
        this._windowPush?.stop();
        this._windowPush = null;
        this._statusBridge?.stop();
        this._statusBridge = null;
        this._operational = false;
        this._destroyed = true;

        for (const [, retry] of this._pending)
            GLib.source_remove(retry.sourceId);
        this._pending.clear();

        for (const id of [this._windowCreatedId, this._restackedId,
                          this._workspaceChangedId, this._overviewHiddenId]) {
            // Each lives on a different object; disconnect defensively.
            try {
                if (id === this._windowCreatedId || id === this._restackedId)
                    global.display.disconnect(id);
                else if (id === this._workspaceChangedId)
                    global.workspace_manager.disconnect(id);
                else if (id)
                    Main.overview.disconnect(id);
            } catch (_e) {
                // Already gone during Shell shutdown.
            }
        }
        this._windowCreatedId = this._restackedId = 0;
        this._workspaceChangedId = this._overviewHiddenId = 0;

        if (this._bgChildAddedId && this._bgGroup) {
            try {
                this._bgGroup.disconnect(this._bgChildAddedId);
            } catch (_e) {
                // Group already gone during Shell shutdown.
            }
            this._bgChildAddedId = 0;
        }

        // Hand every actor back to the normal window stack so Mutter can
        // manage and destroy it from its expected parent.
        for (const [, record] of this._adopted)
            this._release(record);
        this._adopted.clear();

        if (this._injectionManager) {
            this._injectionManager.clear();
            this._injectionManager = null;
        }
        this._bgGroup = null;
    }

    // ------------------------------------------------------------ matching

    _isWallpaperWindow(window) {
        try {
            if (window == null || typeof window.get_role !== 'function')
                return false;
            // Adopted windows keep matching; identity beats every heuristic.
            if (this._adopted.has(window))
                return true;
            return window.get_role() === WALLPAPER_ROLE;
        } catch (_e) {
            return false;
        }
    }

    _watchWindow(window) {
        if (this._destroyed || this._adopted.has(window) || this._pending.has(window))
            return;
        if (!this._isWallpaperWindow(window))
            return;
        if (this._tryAdopt(window))
            return;
        // The actor may not exist yet right after window-created; poll
        // briefly, then give up on this window (a later map creates a new
        // MetaWindow, which re-triggers window-created).
        const retry = {attempts: 0, sourceId: 0};
        retry.sourceId = GLib.timeout_add(GLib.PRIORITY_DEFAULT, WINDOW_MATCH_INTERVAL_MS, () => {
            retry.attempts++;
            if (this._tryAdopt(window) || retry.attempts >= WINDOW_MATCH_RETRIES) {
                this._pending.delete(window);
                return GLib.SOURCE_REMOVE;
            }
            return GLib.SOURCE_CONTINUE;
        });
        this._pending.set(window, retry);
    }

    // ------------------------------------------------------------ adoption

    _tryAdopt(window) {
        if (this._destroyed || this._adopted.has(window))
            return true;
        if (!this._isWallpaperWindow(window))
            return false;
        const actor = window.get_compositor_private();
        if (!actor)
            return false;

        window.lower();

        // The player owns geometry (one window per monitor, moved/resized
        // on monitor changes): never move_resize here.
        const parent = actor.get_parent();
        if (parent !== this._bgGroup) {
            if (parent)
                parent.remove_child(actor);
            this._bgGroup.add_child(actor);
        }
        this._pinOnTop(actor);

        if (!this._bgChildAddedId) {
            // The shell recreates its static background actors on settings,
            // monitor or session changes, appending them on top of the group
            // — which would bury the video under the static wallpaper.
            this._bgChildAddedId = this._bgGroup.connect('child-added', (group, child) => {
                for (const [, record] of this._adopted) {
                    if (child !== record.actor && !record.interactive && !record.quarantined)
                        group.set_child_above_sibling(record.actor, null);
                }
            });
        }

        const record = {window, actor, unmanagedId: 0, raisedId: 0, focusId: 0, visibleId: 0, destroyId: 0};

        // Mutter hides a window's actor when its MetaWindow isn't on the
        // active workspace; reparenting alone does not survive a workspace
        // switch. Deliberately NOT window.stick(): that marks the *window*
        // sticky, which makes GNOME's workspace-switch slide animation try
        // to clone/track it as a normal sticky window — but its actor no
        // longer lives where that code expects, and it crashes _syncStacking.
        // Instead fight the symptom directly: whenever something hides the
        // actor, show it again immediately.
        record.visibleId = actor.connect('notify::visible', () => {
            if (!actor.visible && !record.interactive && !record.quarantined) {
                try {
                    actor.show();
                } catch (_e) {
                    // Actor dying; the destroy handler drops the record.
                }
            }
        });
        if (!actor.visible) {
            try {
                actor.show();
            } catch (_e) {
                // Actor dying; the destroy handler drops the record.
            }
        }

        record.unmanagedId = window.connect('unmanaged', () => {
            this._forget(window);
        });

        // The actor dies with the player process (restart, mode switch).
        // Drop the record at destroy-time: touching the actor afterwards
        // logs disposed-object warnings, and its handlers die with it.
        record.destroyId = actor.connect('destroy', () => {
            this._adopted.delete(window);
            this._statusBridge?.changed();
        });

        // A wallpaper must never hold focus or rise in the stack, yet its
        // surface covers the desktop, so desktop clicks land on it. Undo
        // both effects whenever they happen.
        record.raisedId = window.connect_after('raised', () => {
            if (!record.interactive)
                window.lower();
        });
        record.focusId = window.connect('focus', () => {
            this._refocusNormalWindow(window);
        });

        this._adopted.set(window, record);
        this._refocusNormalWindow(window);

        console.warn(`[${this.uuid}] adopted "${window.get_title() ?? ''}" into the background layer`);
        this._statusBridge?.changed();
        return true;
    }

    _pinOnTop(actor) {
        if ([...this._adopted.values()].some(record => record.actor === actor &&
            (record.interactive || record.quarantined)))
            return;
        try {
            this._bgGroup.set_child_above_sibling(actor, null);
        } catch (_e) {
            // Best effort; the child-added handler keeps re-pinning.
        }
    }

    _repinAll() {
        if (this._destroyed)
            return;
        for (const [, record] of this._adopted)
            this._pinOnTop(record.actor);
    }

    _refocusNormalWindow(rendererWindow) {
        try {
            if (global.display.focus_window !== rendererWindow || this._adopted.get(rendererWindow)?.interactive)
                return;
            const workspace = global.workspace_manager.get_active_workspace();
            const tabList = global.display
                .get_tab_list(Meta.TabList.NORMAL, null)
                .filter(w => w !== rendererWindow && !this._adopted.has(w) && !w.minimized &&
                    w.located_on_workspace(workspace) && w.showing_on_its_workspace());
            if (tabList.length > 0)
                tabList[0].focus(global.get_current_time());
        } catch (_e) {
            // Never break the shell's focus flow from a wallpaper handler.
        }
    }

    _forget(window) {
        const record = this._adopted.get(window);
        if (!record)
            return;
        this._adopted.delete(window);
        this._statusBridge?.changed();
        this._disconnectRecord(record);
    }

    _disconnectRecord(record) {
        for (const key of ['unmanagedId', 'raisedId', 'focusId']) {
            if (record[key]) {
                try {
                    record.window.disconnect(record[key]);
                } catch (_e) {
                    // Window already gone.
                }
                record[key] = 0;
            }
        }
        for (const key of ['visibleId', 'destroyId']) {
            if (record[key]) {
                try {
                    record.actor.disconnect(record[key]);
                } catch (_e) {
                    // Actor already disposed (e.g. from 'unmanaged').
                }
                record[key] = 0;
            }
        }
    }

    _release(record) {
        this._disconnectRecord(record);
        const actor = record.actor;
        try {
            if (actor && this._bgGroup && actor.get_parent() === this._bgGroup) {
                this._bgGroup.remove_child(actor);
                global.window_group.add_child(actor);
            }
        } catch (_e) {
            // Shell is shutting down; the actor dies with it.
        }
    }

    // ------------------------------------------------- window-state sensor

    _readDesktopState() {
        // Plain-data snapshot for the push sensor: counts and indices only,
        // never window identities. Wallpaper windows are excluded (they are
        // also hidden from get_window_actors() by the Overview overrides).
        // Windows the compositor cannot describe are skipped, never fatal.
        const windows = [];
        let activeWorkspace = 0;
        try {
            activeWorkspace = global.workspace_manager.get_active_workspace_index();
        } catch (_e) {
            // Stay at workspace 0 rather than breaking the Shell.
        }
        let activeWs = null;
        try {
            activeWs = global.workspace_manager.get_active_workspace();
        } catch (_e) {
            activeWs = null;
        }
        for (const actor of global.get_window_actors()) {
            const window = actor ? actor.get_meta_window() : null;
            if (!window || this._isWallpaperWindow(window))
                continue;
            try {
                windows.push({
                    ref: window,
                    fullscreen: !!window.fullscreen,
                    maximizedHorizontally: !!window.maximized_horizontally,
                    maximizedVertically: !!window.maximized_vertically,
                    monitor: window.get_monitor(),
                    minimized: !!window.minimized,
                    onActiveWorkspace: activeWs ? !!window.located_on_workspace(activeWs) : true,
                    wallpaper: false,
                });
            } catch (_e) {
                // Skip windows the compositor cannot describe right now.
            }
        }
        let monitors = [];
        try {
            const primary = Main.layoutManager.primaryIndex;
            monitors = Main.layoutManager.monitors.map((monitor, index) => ({
                index,
                primary: index === primary,
                x: monitor.x,
                y: monitor.y,
                width: monitor.width,
                height: monitor.height,
            }));
        } catch (_e) {
            // Monitors stay empty rather than breaking the Shell.
        }
        let nWorkspaces = 1;
        try {
            nWorkspaces = global.workspace_manager.get_n_workspaces();
        } catch (_e) {
            // Stay at a single workspace.
        }
        return {windows, activeWorkspace, nWorkspaces, monitors};
    }

    // ---------------------------------------------------- overview hiding

    _installOverviewHiding() {
        // Matchers run inside the shell's own UI code: never throw there.
        const isWallpaper = window => {
            try {
                if (window && typeof window.get_meta_window === 'function')
                    window = window.get_meta_window();
                return this._isWallpaperWindow(window);
            } catch (_e) {
                return false;
            }
        };

        this._injectionManager.overrideMethod(Workspace.prototype, '_isOverviewWindow',
            originalMethod => function (window) {
                return isWallpaper(window) ? false : originalMethod.apply(this, [window]);
            });

        this._injectionManager.overrideMethod(WorkspaceThumbnail.prototype, '_isOverviewWindow',
            originalMethod => function (window) {
                return isWallpaper(window) ? false : originalMethod.apply(this, [window]);
            });

        this._injectionManager.overrideMethod(Meta.Display.prototype, 'get_tab_list',
            originalMethod => function (type, workspace) {
                return originalMethod.apply(this, [type, workspace])
                    .filter(window => !isWallpaper(window));
            });

        this._injectionManager.overrideMethod(Shell.Global.prototype, 'get_window_actors',
            originalMethod => function () {
                return originalMethod.call(this)
                    .filter(actor => !isWallpaper(actor.get_meta_window()));
            });

        this._injectionManager.overrideMethod(Shell.WindowTracker.prototype, 'get_window_app',
            originalMethod => function (window) {
                return isWallpaper(window) ? null : originalMethod.apply(this, [window]);
            });

        this._injectionManager.overrideMethod(Shell.App.prototype, 'get_windows',
            originalMethod => function () {
                return originalMethod.call(this).filter(window => !isWallpaper(window));
            });

        this._injectionManager.overrideMethod(Shell.App.prototype, 'get_n_windows',
            _originalMethod => function () {
                return this.get_windows().length;
            });

        this._injectionManager.overrideMethod(Shell.AppSystem.prototype, 'get_running',
            originalMethod => function () {
                return originalMethod.call(this).filter(app => app.get_n_windows() > 0);
            });
    }
}
