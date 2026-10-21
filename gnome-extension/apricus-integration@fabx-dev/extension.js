// Apricus Wallpaper Integration — GNOME Shell side.
//
// Keeps Apricus live-wallpaper windows below DING desktop icons and
// ordinary application windows on GNOME Wayland.
//
// The player marks its wallpaper windows with WM_WINDOW_ROLE
// "apricus-wallpaper" before mapping them; this extension lowers every
// window carrying that role whenever stacking may have changed. It never
// touches DING or any other window: lowering only our own windows is
// idempotent and converges, so there is no stacking duel.
//
// Why lowering, not background-layer adoption: Apricus renders through
// VLC/XWayland, and X11 window actors stop presenting damage once
// reparented out of the normal window stack on Mutter 50 (verified live:
// adopted actors freeze, released actors play). Lowering in place keeps
// damage flowing while the compositor enforces the order. This mirrors
// the approach reported for GNOME 51 in
// https://github.com/achu94/gnome-wallpaper-engine/issues/24
// (DESKTOP type + lower), adapted to Apricus's role-identified windows.

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
const CODE_REVISION = 2;

const WINDOW_MATCH_RETRIES = 10;
const WINDOW_MATCH_INTERVAL_MS = 100;

export default class ApricusIntegrationExtension extends Extension {
    enable() {
        this._destroyed = false;
        // MetaWindow -> { unmanagedId, raisedId, focusId }
        this._adopted = new Map();
        // MetaWindow -> { attempts, sourceId }
        this._pending = new Map();
        this._restackedId = 0;
        this._workspaceChangedId = 0;
        this._overviewHiddenId = 0;
        this._windowCreatedId = 0;
        this._inLower = false;

        console.warn(`[${this.uuid}] enabled, code revision ${CODE_REVISION}`);

        this._injectionManager = new InjectionManager();
        this._installOverviewHiding();

        this._windowCreatedId = global.display.connect(
            'window-created', (_display, window) => this._watchWindow(window));

        // Any restack (DING lowering itself, new windows, focus changes)
        // may have moved us above the desktop icons: lowering our own
        // windows is a no-op when already at the bottom, so this converges
        // instead of competing.
        this._restackedId = global.display.connect('restacked', () => {
            this._lowerAll();
        });
        this._workspaceChangedId = global.workspace_manager.connect(
            'active-workspace-changed', () => {
                this._lowerAll();
            });
        this._overviewHiddenId = Main.overview.connect('hidden', () => {
            this._lowerAll();
        });

        // The player may already run (started before the extension was
        // enabled, or re-enabled after unlock): adopt matching windows now.
        let seen = 0, matched = 0;
        for (const actor of global.get_window_actors()) {
            const window = actor ? actor.get_meta_window() : null;
            if (window) {
                seen++;
                let role = null, title = null;
                try { role = window.get_role(); title = window.get_title(); } catch (_e) { /* ignore */ }
                // TEMPORARY DIAGNOSTIC for the enumeration mismatch.
                console.warn(`[${this.uuid}] scan sees "${title}" role=${role}`);
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
        if (this._restackedId) {
            global.display.disconnect(this._restackedId);
            this._restackedId = 0;
        }
        if (this._workspaceChangedId) {
            global.workspace_manager.disconnect(this._workspaceChangedId);
            this._workspaceChangedId = 0;
        }
        if (this._overviewHiddenId) {
            Main.overview.disconnect(this._overviewHiddenId);
            this._overviewHiddenId = 0;
        }

        // Nothing was reparented: just disconnect per-window signals.
        for (const [window] of this._adopted)
            this._forget(window, true);
        this._adopted.clear();

        if (this._injectionManager) {
            this._injectionManager.clear();
            this._injectionManager = null;
        }
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

        const record = {window, actor, unmanagedId: 0, raisedId: 0, focusId: 0};
        this._adopted.set(window, record);
        this._lowerOne(window);

        record.unmanagedId = window.connect('unmanaged', () => {
            this._forget(window);
        });

        // A wallpaper must never hold focus or rise in the stack, yet its
        // surface covers the desktop, so desktop clicks land on it. Undo
        // both effects whenever they happen.
        record.raisedId = window.connect_after('raised', () => {
            this._lowerOne(window);
        });
        record.focusId = window.connect('focus', () => {
            this._refocusNormalWindow(window);
        });

        this._refocusNormalWindow(window);

        console.warn(`[${this.uuid}] adopted "${window.get_title() ?? ''}" below the desktop icons`);
        return true;
    }

    _lowerOne(window) {
        if (this._destroyed || this._inLower)
            return;
        try {
            this._inLower = true;
            // Meta-level order first (keeps X stacking sane) ...
            window.lower();
            // ... then enforce the paint order at the actor level. The
            // compositor does not reliably order an XWayland desktop window
            // below DING's native Wayland one (verified live on Mutter 50),
            // so push our actor just above the background group, below
            // everything else. Actor moves never emit window restacks, so
            // this converges instead of competing.
            const record = this._adopted.get(window);
            const actor = record ? record.actor : window.get_compositor_private();
            const bgGroup = Main.layoutManager._backgroundGroup ?? null;
            if (actor && bgGroup && actor.get_parent() === bgGroup.get_parent()) {
                actor.lower_bottom();
                bgGroup.lower_bottom();
            }
        } catch (_e) {
            // Best effort: a dying window cannot be restacked.
        } finally {
            this._inLower = false;
        }
    }

    _lowerAll() {
        if (this._destroyed || this._inLower)
            return;
        for (const window of this._adopted.keys())
            this._lowerOne(window);
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

    _forget(window, silent = false) {
        const record = this._adopted.get(window);
        if (!record)
            return;
        this._adopted.delete(window);
        for (const key of ['unmanagedId', 'raisedId', 'focusId']) {
            if (record[key]) {
                try {
                    window.disconnect(record[key]);
                } catch (_e) {
                    // Window already gone.
                }
                record[key] = 0;
            }
        }
        if (!silent)
            console.warn(`[${this.uuid}] released "${window.get_title() ?? ''}"`);
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
