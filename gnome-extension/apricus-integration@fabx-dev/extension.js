// Apricus Wallpaper Integration — GNOME Shell side.
//
// Hosts Apricus live-wallpaper windows (owned by the Apricus player process)
// in GNOME Shell's background layer, strictly below desktop-icon windows
// (DING) and ordinary application windows.
//
// The player marks its wallpaper windows with WM_WINDOW_ROLE
// "apricus-wallpaper" before mapping them; this extension adopts every
// window carrying that role by reparenting its compositor actor into
// Main.layoutManager._backgroundGroup. Identification is role-based on
// purpose: the settings window and file choosers never carry the role,
// so they can never be adopted by mistake.
//
// This approach is adapted from wallpaperEngineWayland (GPL-2.0-or-later,
// https://github.com/diaslucass/wallpaperEngineWayland): same background-
// group adoption, same Overview/Alt-Tab hiding, same re-pin on background
// recreation. Unlike that project the renderer process is NOT spawned here:
// Apricus owns playback (VLC/XWayland), this extension only places actors.

import GLib from 'gi://GLib';
import Meta from 'gi://Meta';
import Shell from 'gi://Shell';

import {Extension, InjectionManager} from 'resource:///org/gnome/shell/extensions/extension.js';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Workspace} from 'resource:///org/gnome/shell/ui/workspace.js';
import {WorkspaceThumbnail} from 'resource:///org/gnome/shell/ui/workspaceThumbnail.js';

// Must match WALLPAPER_WINDOW_ROLE in src/apricus/gnome_shell.py and the
// role set by the player before mapping (Gtk.Window.set_role).
const WALLPAPER_ROLE = 'apricus-wallpaper';

// Bumped on every code change; logged from enable() so the journal shows
// which revision the running shell actually loaded.
const CODE_REVISION = 1;

const WINDOW_MATCH_RETRIES = 10;
const WINDOW_MATCH_INTERVAL_MS = 100;

export default class ApricusIntegrationExtension extends Extension {
    enable() {
        this._destroyed = false;
        // MetaWindow -> { actor, unmanagedId, raisedId, focusId, visibleId }
        this._adopted = new Map();
        // MetaWindow -> { attempts, sourceId }
        this._pending = new Map();
        this._bgChildAddedId = 0;
        this._windowCreatedId = 0;

        console.warn(`[${this.uuid}] enabled, code revision ${CODE_REVISION}`);

        this._bgGroup = Main.layoutManager._backgroundGroup ?? null;
        if (!this._bgGroup) {
            console.error(`[${this.uuid}] no background group on this Shell version; staying inert`);
            return;
        }

        this._injectionManager = new InjectionManager();
        this._installOverviewHiding();

        this._windowCreatedId = global.display.connect(
            'window-created', (_display, window) => this._watchWindow(window));

        // The player may already run (started before the extension was
        // enabled, or re-enabled after unlock): adopt matching windows now.
        let seen = 0, matched = 0;
        for (const actor of global.get_window_actors()) {
            const window = actor ? actor.get_meta_window() : null;
            if (window) {
                seen++;
                if (this._isWallpaperWindow(window))
                    matched++;
                this._watchWindow(window);
            }
        }
        console.warn(`[${this.uuid}] startup scan: ${seen} windows, ${matched} wallpaper role match(es)`);
    }

    disable() {
        this._destroyed = true;

        for (const [, retry] of this._pending)
            GLib.source_remove(retry.sourceId);
        this._pending.clear();

        if (this._windowCreatedId) {
            global.display.disconnect(this._windowCreatedId);
            this._windowCreatedId = 0;
        }

        if (this._dbgTimer) {
            GLib.source_remove(this._dbgTimer);
            this._dbgTimer = 0;
        }

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
            this._release(record, true);
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

        // The player owns geometry (one window per monitor, moved/resized on
        // monitor changes): never move_resize here.
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
                    if (child !== record.actor)
                        group.set_child_above_sibling(record.actor, null);
                }
            });
        }

        const record = {window, actor, unmanagedId: 0, raisedId: 0, focusId: 0, visibleId: 0, destroyId: 0};

        // Mutter hides a window's actor when its MetaWindow is not on the
        // active workspace; reparenting alone does not survive a workspace
        // switch. Deliberately NOT window.stick(): that makes the workspace
        // slide animation track the window as a normal sticky window while
        // its actor lives elsewhere. Force visibility instead.
        // TEMPORARY DIAGNOSTIC (freeze investigation): event counters with a
        // periodic summary. Removed once the stall mechanism is identified.
        record.dbg = {raised: 0, focus: 0, hidden: 0};
        if (!this._dbgTimer) {
            this._dbgCount = 0;
            this._dbgTimer = GLib.timeout_add_seconds(GLib.PRIORITY_DEFAULT, 10, () => {
                if (this._destroyed) {
                    this._dbgTimer = 0;
                    return GLib.SOURCE_REMOVE;
                }
                this._dbgCount++;
                for (const [w, r] of this._adopted) {
                    console.warn(`[${this.uuid}] dbg t=${this._dbgCount * 10}s ` +
                        `raised=${r.dbg.raised} focus=${r.dbg.focus} hidden=${r.dbg.hidden} ` +
                        `actorVisible=${r.actor.visible} inBg=${r.actor.get_parent() === this._bgGroup}`);
                }
                return GLib.SOURCE_CONTINUE;
            });
        }

        record.visibleId = actor.connect('notify::visible', () => {
            if (!actor.visible) {
                record.dbg.hidden++;
                actor.show();
            }
        });
        if (!actor.visible)
            actor.show();

        record.unmanagedId = window.connect('unmanaged', () => {
            this._forget(window);
        });

        // The actor dies with the player process (restart, mode switch).
        // Drop the record at destroy-time: touching the actor afterwards
        // (e.g. from 'unmanaged') logs disposed-object warnings, and its
        // signal handlers die with it, so there is nothing to disconnect.
        record.destroyId = actor.connect('destroy', () => {
            this._adopted.delete(window);
        });

        // A wallpaper must never hold focus or rise in the stack, yet its
        // surface covers the desktop, so desktop clicks land on it. Undo
        // both effects whenever they happen.
        record.raisedId = window.connect_after('raised', () => {
            record.dbg.raised++;
            window.lower();
        });
        record.focusId = window.connect('focus', () => {
            record.dbg.focus++;
            this._refocusNormalWindow(window);
        });

        this._adopted.set(window, record);
        this._refocusNormalWindow(window);

        console.warn(`[${this.uuid}] adopted "${window.get_title() ?? ''}" into the background layer`);
        return true;
    }

    _pinOnTop(actor) {
        try {
            this._bgGroup.set_child_above_sibling(actor, null);
        } catch (_e) {
            // Best effort; the child-added handler keeps re-pinning.
        }
    }

    _refocusNormalWindow(rendererWindow) {
        try {
            const tabList = global.display
                .get_tab_list(Meta.TabList.NORMAL, null)
                .filter(w => w !== rendererWindow && !this._adopted.has(w));
            if (tabList.length > 0)
                tabList[0].activate(global.get_current_time());
        } catch (_e) {
            // Never break the shell's focus flow from a wallpaper handler.
        }
    }

    _forget(window) {
        const record = this._adopted.get(window);
        if (!record)
            return;
        this._adopted.delete(window);
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
        if (record.visibleId || record.destroyId) {
            try {
                if (record.visibleId)
                    record.actor.disconnect(record.visibleId);
                if (record.destroyId)
                    record.actor.disconnect(record.destroyId);
            } catch (_e) {
                // Actor already disposed (e.g. from 'unmanaged').
            }
            record.visibleId = 0;
            record.destroyId = 0;
        }
    }

    _release(record, toWindowGroup) {
        this._disconnectRecord(record);
        const actor = record.actor;
        try {
            if (toWindowGroup && actor && actor.get_parent() === this._bgGroup) {
                this._bgGroup.remove_child(actor);
                global.window_group.add_child(actor);
            }
        } catch (_e) {
            // Shell is shutting down; the actor dies with it.
        }
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
