import logging
import multiprocessing as mp
import os
import random
import signal
import time
from multiprocessing import Process

import setproctitle
from gi.repository import GLib
from pydbus import SessionBus

from apricus import perf
from apricus.commons import (
    CONFIG_KEY_DATA_SOURCE,
    CONFIG_KEY_MODE,
    CONFIG_KEY_MUTE,
    CONFIG_KEY_MUTE_WHEN_MAXIMIZED,
    CONFIG_KEY_PAUSE_WHEN_MAXIMIZED,
    CONFIG_KEY_PLAYLIST,
    CONFIG_KEY_POLICY_PAUSE,
    CONFIG_KEY_POLICY_VERSION,
    CONFIG_KEY_STATIC_WALLPAPER,
    CONFIG_KEY_STOPPED,
    CONFIG_KEY_SYSTRAY,
    CONFIG_KEY_VOLUME,
    DBUS_NAME_PLAYER,
    DBUS_NAME_SERVER,
    LOGGER_NAME,
    MODE_NULL,
    MODE_PLAYLIST,
    MODE_STREAM,
    MODE_VIDEO,
    MODE_WEBPAGE,
    resolve_video_dir,
)
from apricus.monitor import Monitors
from apricus.policy import PAUSED, POLICY_VERSION, STOPPED, PlaybackPolicyEngine
from apricus.stop_support import finish_process, stopped_background
from apricus.utils import ConfigUtil, EndSessionHandler, _authorize, get_video_paths, is_snap

# Window-state sources whose snapshots arrive already debounced by the
# Shell (no second engine delay): the native extension push and the
# Snap bridge-carried push (method calls, same settled frames).
BRIDGE_PUSH_SOURCE = "snap-bridge-push"
_SETTLED_PUSH_SOURCES = frozenset({"extension-push", BRIDGE_PUSH_SOURCE})

# NOTE: menu and the player mains pin Gtk 3 namespaces at import time
# (gi forbids two Gtk majors in one process). They must never be imported
# at server top-level: the forkserver image inherits the parent's loaded
# namespaces, so any Gtk 3 import here would break GTK4 children even
# though show_gui() imports gtk4app lazily. Each target below imports its
# real main *inside* the child process instead.


def _gui_gtk4_target(version, pkgdatadir, localedir):
    from apricus.gui.gtk4app import main as gui_target

    gui_target(version, pkgdatadir, localedir)


def _video_player_target(ready=None, initially_paused=False):
    os.setsid()
    from apricus.player.video_player import main as video_player_main

    video_player_main(ready=ready, initially_paused=initially_paused)


def _web_player_target(ready=None, initially_paused=False):
    os.setsid()
    from apricus.player.web_player import main as web_player_main

    web_player_main(ready=ready, initially_paused=initially_paused)


def _systray_target(mode, localedir):
    from apricus.menu import show_systray_icon

    show_systray_icon(mode, localedir)


loop = GLib.MainLoop()
logger = logging.getLogger(LOGGER_NAME)


class ApricusServer:
    """
    <node>
    <interface name='io.github.fabx_dev.apricus.server'>
        <method name='null'/>
        <method name='video'>
            <arg type='s' name='video_path' direction='in'/>
            <arg type='s' name='monitor' direction='in'/>
        </method>
        <method name='stream'>
            <arg type='s' name='stream_url' direction='in'/>
            <arg type='s' name='monitor' direction='in'/>
        </method>
        <method name='webpage'>
            <arg type='s' name='webpage_url' direction='in'/>
            <arg type='s' name='monitor' direction='in'/>
        </method>
        <method name='playlist'>
            <arg type='as' name='video_paths' direction='in'/>
            <arg type='s' name='monitor' direction='in'/>
        </method>
        <method name='pause_playback'/>
        <method name='start_playback'/>
        <method name='stop_playback'/>
        <method name='resume_playback'/>
        <method name="reload"/>
        <method name="respawn_player"/>
        <method name="feeling_lucky"/>
        <method name='show_gui'/>
        <method name='quit'/>
        <property name="mode" type="s" access="read"/>
        <property name="volume" type="i" access="readwrite"/>
        <property name="is_mute" type="b" access="readwrite"/>
        <property name="is_playing" type="b" access="read"/>
        <property name="playback_state" type="s" access="read"/>
        <property name="engine_snapshot" type="a{sv}" access="read"/>
        <property name="is_paused_by_user" type="b" access="readwrite"/>
        <property name="is_static_wallpaper" type="b" access="readwrite"/>
        <property name="is_pause_when_maximized" type="b" access="readwrite"/>
        <property name="is_mute_when_maximized" type="b" access="readwrite"/>
        <property name="playback_error" type="s" access="read"/>
    </interface>
    </node>
    """

    def __init__(self, version, pkgdatadir, localedir, args):
        setproctitle.setproctitle("apricus-server")

        # Share the login-timescale T0 with all forkserver children.
        os.environ["APRICUS_T0"] = repr(perf.start_time())

        self.version = version
        self.pkgdatadir = pkgdatadir
        self.localedir = localedir
        self.args = args
        self._prev_mode = None
        self._player_count = 0

        # Processes
        # Switch to `forkserver` since v3.2 for performance. BTW `fork` didn't work (it crashes).
        # Ref: https://bnikolic.co.uk/blog/python/parallelism/2019/11/13/python-forkserver-preload.html
        mp.set_start_method("forkserver")
        self.gui_process = None
        self.sys_icon_process = None
        self.player_process = None

        signal.signal(signal.SIGINT, lambda *_: self.quit())
        signal.signal(signal.SIGTERM, lambda *_: self.quit())
        # No SIGSEGV handler: after a real segfault the interpreter state is
        # undefined and quit() (D-Bus + joins) is not reentrant. Crashes in
        # native code are diagnosed via faulthandler in the player.
        # Monitoring EndSession (OS reboot, shutdown, etc.)
        EndSessionHandler(self.quit)

        # Configuration
        if args.reset:
            ConfigUtil().generate_template()
        self._load_config()
        perf.mark("server: config loaded")

        # Central playback policy (M2). The engine owns manual latches and
        # automatic reasons; every verb below routes through it. Latches are
        # restored from the persisted config so STOP/PAUSE survive restarts.
        self.policy = PlaybackPolicyEngine()
        self.policy.load_latches(
            manual_stop=bool(self.config.get(CONFIG_KEY_STOPPED, False)),
            manual_pause=bool(self.config.get(CONFIG_KEY_POLICY_PAUSE, False)),
            manual_mute=bool(self.config.get(CONFIG_KEY_MUTE, False)),
        )
        self._last_applied = None
        self._auto_fed = {}

        # Player process
        self.reload()
        perf.mark("server: player spawn requested")

        # Environment sensors (moved here from the player so there is a
        # single decider; best-effort in headless/test contexts).
        self._start_environment_sensors()

        # Show main GUI
        if not args.background:
            self.show_gui()

        logger.info("[Server] Started")

    def _load_config(self):
        self.config = ConfigUtil().load()

    def _save_config(self, *, manual_stop=None, manual_pause=None):
        if manual_stop is None and manual_pause is None:
            ConfigUtil().save(self.config)
        else:
            ConfigUtil().save(self.config, manual_stop=manual_stop, manual_pause=manual_pause)

    # -- central policy plumbing (M2) -------------------------------------
    def _sync_policy_persist(self, *, stop_to=None, pause_to="keep"):
        """Write the engine latch state into the config and persist it.

        ``stop_to``/``pause_to`` carry explicit tri-state intent from the
        calling verb (True/False) or preserve the on-disk latch (None, the
        M1 rule for concurrent non-owner writers).
        """
        latches = self.policy.latches()
        pause = latches["manual_pause"] if pause_to == "keep" else bool(pause_to)
        self.config[CONFIG_KEY_POLICY_PAUSE] = pause
        self.config[CONFIG_KEY_POLICY_VERSION] = POLICY_VERSION
        self.config[CONFIG_KEY_MUTE] = latches["manual_mute"]
        if stop_to is None:
            self._save_config(manual_pause=pause if pause_to != "keep" else None)
        else:
            self.config[CONFIG_KEY_STOPPED] = bool(stop_to)
            self._save_config(manual_stop=bool(stop_to), manual_pause=pause)

    def _apply_policy_output(self, output, *, fresh=False):
        """Actuate an engine output. Idempotent: re-applying the same
        (desired, wanted_mute) pair is a no-op (no double fades). A mute-only
        change never restarts playback, it only flips the mute switch.
        With fresh=True the player was created seconds ago and already
        auto-started, so PLAY never re-issues start: only the mirror, an
        optional pause, and the mute switch are applied."""
        if self._last_applied is None:
            last_desired, last_mute = None, None
        else:
            last_desired, last_mute = self._last_applied
        if (output.desired, output.wanted_mute) == (last_desired, last_mute):
            return False
        if output.desired == STOPPED:
            self._terminate_player()
            self._apply_stopped_background()
            self._last_applied = (output.desired, output.wanted_mute)
            return True
        ready = getattr(self, "_player_ready_event", None)
        if ready is not None and not ready.is_set():
            return False
        player = get_instance(DBUS_NAME_PLAYER)
        if player is None:
            return False
        try:
            if output.desired != last_desired:
                if output.desired == PAUSED:
                    try:
                        player.is_paused_by_user = True
                    except (GLib.Error, AttributeError):
                        pass
                    player.pause_playback()
                elif not fresh:
                    try:
                        player.is_paused_by_user = False
                    except (GLib.Error, AttributeError):
                        pass
                    player.start_playback()
                else:
                    try:
                        player.is_paused_by_user = False
                    except (GLib.Error, AttributeError):
                        pass
            # Keep a successful playback actuation if only the mute step
            # fails; readiness reconciliation must not restart its fade.
            self._last_applied = (output.desired, last_mute)
            if output.wanted_mute != last_mute:
                if hasattr(player, "is_mute"):
                    player.is_mute = output.wanted_mute
                elif hasattr(player, "set_is_mute"):
                    player.set_is_mute(output.wanted_mute)
        except (GLib.Error, AttributeError):
            logger.debug("[Policy] player actuation skipped (player gone?)")
            return False
        self._last_applied = (output.desired, output.wanted_mute)
        return True

    def _reconcile_new_player(self, proc, ready, initially_paused, deadline):
        """Wait for activation, not just process creation or name publication.

        This bounded readiness check never sleeps or caches an unexecuted
        decision. A replaced/stopped/dead process invalidates its callback.
        """
        if self.player_process is not proc or not proc.is_alive():
            return False
        if ready.is_set():
            output = self.policy.compute()  # Intent may have changed meanwhile.
            self._apply_policy_output(output, fresh=not initially_paused)
            if self._last_applied == (output.desired, output.wanted_mute):
                return False
        if time.monotonic() >= deadline:
            logger.warning("[Policy] player readiness/actuation timed out: pid=%s", proc.pid)
            return False
        return True

    def _terminate_player(self):
        """Bounded teardown of the player process/session (M1 semantics)."""
        proc = getattr(self, "player_process", None)
        if proc is None:
            return
        group = None
        try:
            if os.getpgid(proc.pid) == proc.pid:
                group = proc.pid
        except ProcessLookupError:
            pass
        if proc.is_alive():
            proc.terminate()
        proc.join(timeout=0.75)
        finish_process(proc, group)
        self.player_process = None

    def reason_active_changed(self, reason, active):
        """Feed an automatic reason (internal; M3 will also call this for
        push reasons). Debounced: only settled outputs actuate."""
        output, act_now = self.policy.reason_active_changed(reason, active)
        if act_now:
            self._apply_policy_output(output)
            return
        generation = self.policy.snapshot()["generation"]
        GLib.timeout_add(
            int(self.policy._debounce_s * 1000),
            self._apply_policy_pending,
            generation,
        )

    def _apply_policy_pending(self, generation):
        if generation != self.policy.snapshot()["generation"]:
            return False
        output = self.policy.pop_due()
        if output is not None:
            self._apply_policy_output(output)
        return False

    @property
    def engine_snapshot(self):
        """Read-only policy state for GUI diagnostics and tests."""
        return self.policy.snapshot()

    def _start_environment_sensors(self):
        """Own the lock/maximized sensors (moved from the player). Best
        effort: without a session bus or window backend there are simply
        no automatic reasons, and manual control is unaffected."""
        self._active_handler = None
        self._window_state = None
        self._window_source = "none"
        self._bridge_push = {"generation": None, "seq": 0}
        self._push_watcher = None
        try:
            from apricus.utils import ActiveHandler

            self._active_handler = ActiveHandler(self._on_lock_changed)
        except Exception as e:  # noqa: BLE001 - headless/test safe
            logger.debug(f"[Policy] lock sensor unavailable: {e}")
        try:
            from apricus.utils import is_gnome, is_wayland

            pushable = bool(is_wayland() and is_gnome())
        except Exception:  # noqa: BLE001 - headless/test safe
            pushable = False
        if pushable:
            self._watch_push_owner()
        if not (pushable and self._start_push_backend()):
            self._start_fallback_backend()
        # Strict Snap cannot subscribe to the extension's signals, so the
        # server itself owns the Snap-declared bridge name (same slot, same
        # app label: no manifest or permission change) and receives the
        # settled frames as inbound method calls. Unlike the earlier GUI
        # hosted bridge, this survives the control center being closed.
        self._bridge = None
        try:
            from apricus import gnome_bridge
            from apricus.utils import is_snap as _is_snap

            if _is_snap() and pushable:
                self._bridge = gnome_bridge.start(
                    self._on_bridge_changed,
                    self._on_bridge_window_frame,
                    self._on_bridge_window_lost,
                )
        except Exception as e:  # noqa: BLE001 - push stays unavailable, never fatal
            logger.debug(f"[Policy] snap bridge unavailable: {e}")
            self._bridge = None

    def _start_push_backend(self):
        """Prefer the extension push channel (no polling while healthy)."""
        from apricus.backends import ExtensionPushWindowStateBackend

        initial = []
        try:
            backend = ExtensionPushWindowStateBackend.try_probe(initial.append)
        except Exception as e:  # noqa: BLE001 - probe must never raise
            logger.debug(f"[Policy] push probe failed: {e}")
            return False
        if backend is None:
            return False
        try:
            if not backend.start():
                backend.stop()
                return False
        except Exception as e:  # noqa: BLE001 - headless/test safe
            logger.debug(f"[Policy] push start failed: {e}")
            return False
        self._window_state = backend
        self._window_source = backend.name
        self.policy.note_push_alive(True)
        backend.on_window_state_changed = self._on_window_state_changed
        if initial:
            self._on_window_state_changed(initial[-1])
        logger.info("[Policy] window-state source: extension push")
        return True

    def _start_fallback_backend(self):
        """Poll/Wnck/Null ladder when push is unavailable. Never raises."""
        from apricus.backends import create_window_state_backend

        try:
            backend = create_window_state_backend(self._on_window_state_changed)
        except Exception as e:  # noqa: BLE001 - headless/test safe
            logger.debug(f"[Policy] fallback backend unavailable: {e}")
            return False
        if backend is None:
            self.policy.note_push_alive(False)
            return False
        try:
            backend.start()
        except Exception as e:  # noqa: BLE001 - headless/test safe
            logger.debug(f"[Policy] fallback backend failed to start: {e}")
            self.policy.note_push_alive(False)
            return False
        self._window_state = backend
        self._window_source = getattr(backend, "name", "unknown")
        self.policy.note_push_alive(False)
        logger.info("[Policy] window-state source: %s", self._window_source)
        return True

    def _stop_window_state(self):
        backend, self._window_state = self._window_state, None
        if backend is not None:
            try:
                backend.stop()
            except Exception:  # noqa: BLE001 - best effort
                logger.debug("[Policy] window-state backend stop failed")
        self._window_source = "none"

    def _watch_push_owner(self):
        """Reconcile when the extension name appears or disappears.

        Event-driven only: no polling is introduced here. Appearing owners
        trigger a GetState reconcile; vanishing owners freeze the last
        known state and fall back (never auto-resume from comms loss).
        """
        from apricus.backends import ExtensionPushWindowStateBackend

        try:
            bus = SessionBus()
            bus.get("org.freedesktop.DBus", "/org/freedesktop/DBus")

            def _changed(_sender, _object, _iface, _signal, params):
                try:
                    name, _old, new = params[0], params[1], params[2]
                except (TypeError, IndexError, KeyError):
                    return
                if name != ExtensionPushWindowStateBackend.BUS_NAME:
                    return
                if new:
                    self._on_push_appeared()
                else:
                    self._on_push_vanished()

            self._push_watcher = bus.subscribe(
                sender="org.freedesktop.DBus",
                iface="org.freedesktop.DBus",
                signal="NameOwnerChanged",
                object="/org/freedesktop/DBus",
                signal_fired=_changed,
            )
        except Exception as e:  # noqa: BLE001 - headless/test safe
            logger.debug(f"[Policy] push owner watch unavailable: {e}")
            self._push_watcher = None

    def _on_push_appeared(self):
        from apricus.utils import is_gnome, is_wayland

        if not (is_wayland() and is_gnome()):
            return
        if self._window_source == "extension-push":
            return
        self._stop_window_state()
        if not self._start_push_backend():
            self._start_fallback_backend()

    def _on_push_vanished(self):
        if self._window_source != "extension-push":
            return
        self._stop_window_state()
        self.policy.note_push_alive(False)
        self._start_fallback_backend()

    def window_push_frame(self, generation, seq, fullscreen, maximized, dbus_context=None):
        """Snap bridge-carried window state (M3 under strict confinement).

        The Snap cannot subscribe to the extension's signals, so the Shell
        pushes the same settled frames as method calls to the Snap-owned
        bridge; the GUI forwards them here over Snap IPC. Same-UID callers
        only (like every other mutating verb). Epochs rebasline; duplicates
        and obsolete sequences are dropped; loss freezes (never resumes).
        """
        _authorize("window_push_frame", dbus_context)
        if type(generation) is not str or len(generation) != 32:
            raise ValueError("window_push_frame: invalid generation")
        if type(fullscreen) is not bool or type(maximized) is not bool:
            raise ValueError("window_push_frame: flags must be bools")
        try:
            seq = int(seq)
        except (TypeError, ValueError):
            raise ValueError("window_push_frame: invalid sequence") from None
        from apricus.utils import is_snap

        if not is_snap():
            logger.debug("[Policy] bridge window frame ignored (not a Snap)")
            return False
        state = getattr(self, "_bridge_push", None) or {"generation": None, "seq": 0}
        if state.get("generation") != generation:
            state = {"generation": generation, "seq": 0}
            self._bridge_push = state
            self._on_bridge_push_appeared()
        if seq <= state["seq"]:
            return False
        state["seq"] = seq
        self._on_window_state_changed(
            {
                "is_any_maximized": maximized,
                "is_any_fullscreen": fullscreen,
            }
        )
        return True

    def window_push_lost(self, generation, dbus_context=None):
        """End of a bridge window-push generation: freeze, never resume."""
        _authorize("window_push_lost", dbus_context)
        state = getattr(self, "_bridge_push", None) or {}
        if state.get("generation") != generation:
            return False
        self._bridge_push = {"generation": None, "seq": 0}
        self._on_bridge_push_vanished()
        return True

    def _on_bridge_push_appeared(self):
        if getattr(self, "_window_source", "none") == BRIDGE_PUSH_SOURCE:
            return
        self._stop_window_state()
        self._window_source = BRIDGE_PUSH_SOURCE
        self.policy.note_push_alive(True)
        logger.info("[Policy] window-state source: snap bridge push")

    def _on_bridge_push_vanished(self):
        if getattr(self, "_window_source", "none") != BRIDGE_PUSH_SOURCE:
            return
        self._stop_window_state()
        self.policy.note_push_alive(False)
        self._start_fallback_backend()

    def _on_bridge_changed(self):
        logger.debug("[Policy] snap bridge registration changed")

    def _on_bridge_window_frame(self, fullscreen, maximized, seq, generation):
        """Direct in-process feed from the server-owned bridge (no GUI hop)."""
        try:
            self.window_push_frame(generation, seq, fullscreen, maximized)
        except Exception as e:  # noqa: BLE001 - a bad frame must not break the bridge
            logger.debug(f"[Policy] bridge frame dropped: {e}")

    def _on_bridge_window_lost(self, generation):
        try:
            self.window_push_lost(generation)
        except Exception as e:  # noqa: BLE001 - loss handling must not raise
            logger.debug(f"[Policy] bridge loss handling skipped: {e}")

    def gnome_bridge_status(self):
        """Bridge registration snapshot for the Snap control center.

        Snap IPC only (allowlisted method): the GUI no longer owns the
        bridge, so it reads status here instead of a local object.
        """
        try:
            from apricus import gnome_bridge

            status = gnome_bridge.status()
        except Exception:  # noqa: BLE001 - diagnostics must never raise
            return None
        if status is None:
            return None
        state, detail = status
        return [state, dict(detail)]

    def _on_lock_changed(self, active):
        self.reason_active_changed("screen_locked", bool(active))

    def _on_window_state_changed(self, state):
        self._last_window_state = dict(state)
        is_max = bool(state.get("is_any_maximized", False))
        is_full = bool(state.get("is_any_fullscreen", False))
        pause = self.config.get(CONFIG_KEY_PAUSE_WHEN_MAXIMIZED, True)
        mute = self.config.get(CONFIG_KEY_MUTE_WHEN_MAXIMIZED, False)
        updates = {
            "fullscreen": pause and is_full,
            "maximized": pause and is_max,
            "maximized_mute": not pause and mute and (is_max or is_full),
        }
        if getattr(self, "_window_source", None) in _SETTLED_PUSH_SOURCES:
            # The Shell already coalesced this snapshot. Do not add another
            # 250 ms; update all reasons atomically to avoid transient resumes.
            if all(self._auto_fed.get(k) == v for k, v in updates.items()):
                return
            self._auto_fed.update(updates)
            output, act_now = self.policy.reasons_active_changed(updates, settled=True)
            if act_now:
                self._apply_policy_output(output)
        else:
            for reason, active in updates.items():
                self._feed_auto(reason, active)

    def _refresh_window_preferences(self):
        state = getattr(self, "_last_window_state", None)
        # Do not interpret stale cached state as a fresh sample on source loss.
        if state is not None and getattr(self, "_window_source", None) not in (
            None,
            "none",
            "null",
        ):
            self._on_window_state_changed(state)

    def _feed_auto(self, reason, active):
        if self._auto_fed.get(reason) == bool(active):
            return
        self._auto_fed[reason] = bool(active)
        self.reason_active_changed(reason, bool(active))

    def _setup_player(self, mode, data_source=None, monitor=None, force_respawn=False):
        """Setup and run player"""
        # The GUI shares this file: reload so we never persist a stale
        # in-memory copy over a newer on-disk config (and vice versa).
        self._load_config()
        logger.info(f"[Mode] {mode}")
        self.config[CONFIG_KEY_MODE] = mode

        # Set data source if specified
        if data_source is not None:
            if monitor and monitor != "all":
                self.config[CONFIG_KEY_DATA_SOURCE][monitor] = data_source
            else:
                if mode in (MODE_VIDEO, MODE_STREAM, MODE_WEBPAGE):
                    # "All monitors": refresh every stored key plus every
                    # live one, not just Default. Stale per-monitor entries
                    # would otherwise shadow Default in the player's
                    # key→model→Default priority and the wallpaper would
                    # never change. (Mirrors playlist() and the GTK3 panel.)
                    # Note: the server (X11) and the player may enumerate
                    # monitors under different names, so live keys alone are
                    # not enough — stored keys must go too.
                    data = self.config[CONFIG_KEY_DATA_SOURCE]
                    for key in list(data):
                        data[key] = data_source
                    for key in Monitors().get_monitors():
                        data[key] = data_source
            # always update default source
            self.config[CONFIG_KEY_DATA_SOURCE]["Default"] = data_source

        # Persist before spawning: the player process reads the config from
        # disk, so runtime mode switches via D-Bus must not rely on callers
        # saving first (the GUI used to mask this by saving itself).
        self._save_config()

        if self.config.get(CONFIG_KEY_STOPPED, False):
            # Changes/reload/respawn while stopped update selection, never play.
            self._apply_stopped_background()
            self._refresh_systray(mode)
            self._last_applied = (STOPPED, self.policy.compute().wanted_mute)
            return

        # Pre-apply the new video's cached static frame so the switch shows
        # the incoming wallpaper instead of the previous one. Best-effort:
        # cache misses are refreshed by the new player as usual.
        proc = getattr(self, "player_process", None)
        if proc and proc.is_alive():
            self._preview_static(mode, data_source)

        # Reuse the live player when staying inside the VLC family: no
        # unmap/respawn gap, the windows (and their covers) survive.
        # Forced respawns always recreate the player so a fresh VLC
        # instance picks up changed options.
        if not force_respawn and self._try_switch_media(mode):
            perf.mark(f"server: player switched in place [{mode}]")
            self._refresh_systray(mode)
            return

        # Quit current (keep its wallpaper: the new player overwrites it)
        # then create a new player
        if proc and proc.is_alive():
            self._quit_player(restore_wallpaper=False)

        # Terminate old player process and wait for it to finish
        if self.player_process:
            self.player_process.terminate()
            self.player_process.join(timeout=5)  # Wait up to 5 seconds
            if self.player_process.is_alive():
                logger.warning("[Server] Player process didn't terminate, killing it")
                self.player_process.kill()
                self.player_process.join(timeout=2)
            self.player_process = None

        ready = mp.Event()
        initially_paused = self.policy.compute().desired == PAUSED
        if mode in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]:
            self.player_process = Process(
                name=f"apricus-player-{self._player_count}",
                target=_video_player_target,
                args=(ready, initially_paused),
            )
        elif mode == MODE_WEBPAGE:
            self.player_process = Process(
                name=f"apricus-player-{self._player_count}",
                target=_web_player_target,
                args=(ready, initially_paused),
            )
        elif mode == MODE_NULL:
            pass
        else:
            raise ValueError("[Server] Unknown mode")
        if self.player_process is not None:
            # Deduplication belongs to this process instance, not its predecessor.
            self._last_applied = None
            self._player_ready_event = ready
            self.player_process.start()
            self._player_count += 1
            perf.mark(f"server: player process started [{mode}]")
            logger.info(f"[Server] player pid={self.player_process.pid}")

        # Refresh systray icon if the mode changed
        self._refresh_systray(mode)

        if self.player_process is not None:
            args = (self.player_process, ready, initially_paused, time.monotonic() + 30)
            if self._reconcile_new_player(*args):
                GLib.timeout_add(50, self._reconcile_new_player, *args)

    def _refresh_systray(self, mode):
        if self.config[CONFIG_KEY_SYSTRAY]:
            if self._prev_mode != self.mode:
                if self.sys_icon_process:
                    self.sys_icon_process.terminate()
                    self.sys_icon_process.join(timeout=3)
                    if self.sys_icon_process.is_alive():
                        self.sys_icon_process.kill()
                        self.sys_icon_process.join(timeout=1)
                self.sys_icon_process = Process(
                    name="apricus-systray",
                    target=_systray_target,
                    args=(mode, self.localedir),
                )
                self.sys_icon_process.start()
            self._prev_mode = self.mode

    def _try_switch_media(self, mode):
        """Hot-switch inside the live VLC player (no unmap/respawn gap).

        Works when both sides are the VLC family (video/stream/playlist);
        webpage runs a different player class and always respawns.
        Returns True when the switch was handed off.
        """
        if mode not in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]:
            return False
        if not (self.player_process and self.player_process.is_alive()):
            return False
        try:
            player = get_instance(DBUS_NAME_PLAYER)
            if (
                player is None
                or not hasattr(player, "switch_media")
                or player.mode not in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]
            ):
                return False
            player.switch_media(mode)
            return True
        except (GLib.Error, AttributeError):
            return False

    def _apply_stopped_background(self):
        # With the option OFF leave the existing desktop background untouched.
        if not self.config.get(CONFIG_KEY_STATIC_WALLPAPER, True):
            return
        from apricus.backends import create_wallpaper_backend

        backend = create_wallpaper_backend()
        if backend.supports_static:
            backend.apply_static(stopped_background(self.config))

    def stop_playback(self, dbus_context=None):
        """Persistent manual STOP. Destroy only the player, never server/GUI.

        Unlike PAUSE this reclaims the entire VLC/WebKit process, including
        decoder allocations. RESUME creates fresh handles; none are reused.
        """
        _authorize("stop_playback", dbus_context)
        self._load_config()
        self.policy.request_manual_stop()
        self._sync_policy_persist(stop_to=True)
        self._terminate_player()
        # Old process/async callbacks are gone before changing the background.
        self._load_config()
        self._last_applied = None
        self._apply_policy_output(self.policy.compute())

    def resume_playback(self, dbus_context=None):
        """Only this explicit operation clears manual STOP (and any stale
        manual pause, so a fresh start never resumes paused). Repeat is a
        no-op."""
        _authorize("resume_playback", dbus_context)
        self._load_config()
        if not self.config.get(CONFIG_KEY_STOPPED, False):
            return
        self.policy.request_manual_start()
        self._sync_policy_persist(stop_to=False)
        self.reload()

    @staticmethod
    def _preview_static(mode, data_source):
        video = None
        if mode == MODE_VIDEO and data_source:
            video = data_source
        elif mode == MODE_STREAM and data_source:
            video = data_source
        elif mode == MODE_PLAYLIST:
            playlist = ConfigUtil().load().get(CONFIG_KEY_PLAYLIST) or {}
            for queue in [playlist.get("Default")] + [
                q for k, q in playlist.items() if k != "Default"
            ]:
                if isinstance(queue, list) and queue:
                    video = queue[0]
                    break
        if not video:
            return
        try:
            player = get_instance(DBUS_NAME_PLAYER)
            if player and hasattr(player, "preview_static"):
                player.preview_static(video)
        except (GLib.Error, AttributeError):
            pass

    @staticmethod
    def _quit_player(restore_wallpaper=True):
        """Quit current player. Internal respawns pass restore_wallpaper=False
        so the desktop keeps the current static frame instead of flashing
        the pre-Apricus wallpaper between switches."""
        try:
            player = get_instance(DBUS_NAME_PLAYER)
            if player and hasattr(player, "quit_player"):
                player.quit_player(restore_wallpaper)
        except (GLib.Error, AttributeError):
            pass

    def video(self, video_path=None, monitor="", dbus_context=None):
        _authorize("video", dbus_context)
        if video_path and (video_path.startswith("-") or not os.path.isfile(video_path)):
            logger.warning(f"[Server] Rejected video path: {video_path}")
            return
        self._setup_player(MODE_VIDEO, video_path, monitor)

    def stream(self, stream_url=None, monitor="", dbus_context=None):
        _authorize("stream", dbus_context)
        self._setup_player(MODE_STREAM, stream_url, monitor)

    def webpage(self, webpage_url=None, monitor="", dbus_context=None):
        _authorize("webpage", dbus_context)
        self._setup_player(MODE_WEBPAGE, webpage_url, monitor)

    def playlist(self, video_paths=None, monitor=None, dbus_context=None):
        """Play a queue of videos, looping. Muted by design (PipeWire safety)."""
        _authorize("playlist", dbus_context)
        self._load_config()
        video_paths = [p for p in (video_paths or []) if p]
        if not video_paths:
            logger.warning("[Server] Empty playlist, ignoring")
            return
        if len(video_paths) > 500:
            logger.warning(f"[Server] Playlist capped at 500 items ({len(video_paths)} given)")
            video_paths = video_paths[:500]
        playlist = self.config.setdefault(CONFIG_KEY_PLAYLIST, {})
        if monitor and monitor != "all":
            playlist[monitor] = list(video_paths)
        else:
            for key in list(playlist):
                playlist[key] = list(video_paths)
            playlist["Default"] = list(video_paths)
        self._save_config()
        self._setup_player(MODE_PLAYLIST)

    def pause_playback(self, dbus_context=None):
        _authorize("pause_playback", dbus_context)
        if self.policy.latches()["manual_stop"]:
            return  # M1 semantics: no player exists while stopped.
        out, _ = self.policy.request_manual_pause()
        self._sync_policy_persist(pause_to=True)
        self._apply_policy_output(out)

    def start_playback(self, dbus_context=None):
        _authorize("start_playback", dbus_context)
        if self.policy.latches()["manual_stop"]:
            return  # M1 semantics: explicit resume_playback required.
        out, _ = self.policy.request_manual_resume()
        # Explicit Play must clear the disk latch, not preserve its old value.
        self._sync_policy_persist(pause_to=False)
        self._apply_policy_output(out)

    def reload(self, dbus_context=None):
        _authorize("reload", dbus_context)
        if self.config[CONFIG_KEY_MODE] == MODE_VIDEO:
            self.video()
        elif self.config[CONFIG_KEY_MODE] == MODE_STREAM:
            self.stream()
        elif self.config[CONFIG_KEY_MODE] == MODE_WEBPAGE:
            self.webpage()
        elif self.config[CONFIG_KEY_MODE] == MODE_PLAYLIST:
            self._setup_player(MODE_PLAYLIST)
        elif self.config[CONFIG_KEY_MODE] == MODE_NULL:
            pass
        else:
            raise ValueError("[Server] Unknown mode")

    def respawn_player(self, dbus_context=None):
        """Terminate the live player and start a fresh one, same mode/sources.

        Unlike reload(), this always recreates the player process (no
        in-place media switch), so a new VLC instance picks up changed
        options. Volume, mute, playlists and per-monitor assignments are
        preserved from the config; the playlist position restarts from
        the queue head.
        """
        _authorize("player.respawn", dbus_context)
        self._load_config()
        if self.config.get(CONFIG_KEY_STOPPED, False):
            return False
        mode = self.config.get(CONFIG_KEY_MODE, MODE_NULL)
        if mode not in (MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST, MODE_WEBPAGE):
            logger.warning(f"[Server] respawn_player: unknown mode {mode!r}")
            return False
        if mode == MODE_NULL:
            return False
        self._setup_player(mode, force_respawn=True)
        return True

    def feeling_lucky(self, dbus_context=None):
        """Random play a video from the directory"""
        _authorize("feeling_lucky", dbus_context)
        self._load_config()
        monitors = Monitors().get_monitors()
        video_dir = resolve_video_dir(self.config)
        for monitor in monitors:
            file_list = get_video_paths(video_dir)
            # Remove current data source from the random selection
            current = self.config[CONFIG_KEY_DATA_SOURCE].get(monitor)
            if current in file_list:
                file_list.remove(current)
            if not file_list:
                logger.warning(f"[Server] No videos available for monitor {monitor}, skipping")
                continue
            video_path = random.choice(file_list)
            self.config[CONFIG_KEY_MODE] = MODE_VIDEO
            self.config[CONFIG_KEY_DATA_SOURCE][monitor] = video_path
            self._save_config()
            self.video(video_path)

    def show_gui(self, dbus_context=None):
        """Show main GUI (singleton: reuse the live one if present)."""
        _authorize("show_gui", dbus_context)
        if self.gui_process and self.gui_process.is_alive():
            logger.debug("[Server] GUI already running")
            return
        # The GTK4 panel uses the "spawn" start method (fresh interpreter):
        # the forkserver image may legitimately hold Gdk 3.0 by the time the
        # GUI starts (player/systray are Gtk3), and gi forbids a second major
        # in a forked child.
        gui_ctx = mp.get_context("spawn")
        self.gui_process = gui_ctx.Process(
            name="apricus-gui",
            target=_gui_gtk4_target,
            args=(
                self.version,
                self.pkgdatadir,
                self.localedir,
            ),
        )
        self.gui_process.start()

    def quit(self, dbus_context=None):
        _authorize("quit", dbus_context)
        try:
            from apricus import gnome_bridge

            gnome_bridge.stop()
        except Exception:  # noqa: BLE001 - shutdown continues regardless
            pass
        try:
            self._quit_player()
        except GLib.Error:
            pass

        # Quit all processes with proper cleanup
        for process in [self.player_process, self.gui_process, self.sys_icon_process]:
            if process and process.is_alive():
                process.terminate()
                process.join(timeout=3)
                if process.is_alive():
                    logger.warning(f"[Server] Process {process.name} didn't terminate, killing it")
                    process.kill()
                    process.join(timeout=1)

        loop.quit()
        logger.info("[Server] Stopped")

    @property
    def mode(self):
        return self.config[CONFIG_KEY_MODE]

    @property
    def volume(self):
        return self.config[CONFIG_KEY_VOLUME]

    @volume.setter
    def volume(self, volume):
        self.config[CONFIG_KEY_VOLUME] = volume
        self._save_config()
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.volume = volume

    @property
    def is_mute(self):
        return self.config[CONFIG_KEY_MUTE]

    @is_mute.setter
    def is_mute(self, is_mute):
        out, _ = self.policy.set_manual_mute(bool(is_mute))
        self._sync_policy_persist()
        self._apply_policy_output(out)

    @property
    def is_playing(self):
        if self.config.get(CONFIG_KEY_STOPPED, False):
            return False
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            return player.is_playing
        return False

    @property
    def playback_state(self):
        if self.config.get(CONFIG_KEY_STOPPED, False):
            return "stopped"
        player = get_instance(DBUS_NAME_PLAYER)
        if player is None:
            return "unavailable"
        try:
            return player.playback_state
        except (GLib.Error, AttributeError):
            return "playing" if player.is_playing else "paused"

    @property
    def is_paused_by_user(self):
        return self.policy.latches()["manual_pause"]

    @is_paused_by_user.setter
    def is_paused_by_user(self, is_paused_by_user):
        if bool(is_paused_by_user):
            out, _ = self.policy.request_manual_pause()
        else:
            out, _ = self.policy.request_manual_resume()
        # The GUI's property write is an explicit manual command as well.
        self._sync_policy_persist(pause_to=bool(is_paused_by_user))
        self._apply_policy_output(out)

    @property
    def is_static_wallpaper(self):
        return self.config[CONFIG_KEY_STATIC_WALLPAPER]

    @is_static_wallpaper.setter
    def is_static_wallpaper(self, is_static_wallpaper):
        self.config[CONFIG_KEY_STATIC_WALLPAPER] = is_static_wallpaper
        self._save_config()
        if self.config.get(CONFIG_KEY_STOPPED, False):
            self._apply_stopped_background()
            return
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None:
            player.reload_config()
            if is_static_wallpaper:
                # Apply immediately when enabled mid-playback: the player
                # setup path only covers fresh (re)starts, so without this
                # the flag would sit idle until the next video change.
                try:
                    if player.mode in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]:
                        player.refresh_static()
                except (GLib.Error, AttributeError):
                    pass

    @property
    def is_pause_when_maximized(self):
        return self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED]

    @is_pause_when_maximized.setter
    def is_pause_when_maximized(self, is_pause_when_maximized):
        self.config[CONFIG_KEY_PAUSE_WHEN_MAXIMIZED] = is_pause_when_maximized
        self._save_config()
        # Window policy is server-owned. Reloading player config here would
        # overwrite its effective automatic mute with the persisted manual mute.
        self._refresh_window_preferences()

    @property
    def is_mute_when_maximized(self):
        return self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED]

    @is_mute_when_maximized.setter
    def is_mute_when_maximized(self, is_mute_when_maximized):
        self.config[CONFIG_KEY_MUTE_WHEN_MAXIMIZED] = is_mute_when_maximized
        self._save_config()
        self._refresh_window_preferences()

    @property
    def playback_error(self):
        player = get_instance(DBUS_NAME_PLAYER)
        if player is not None and player.mode in [MODE_VIDEO, MODE_STREAM, MODE_PLAYLIST]:
            try:
                return getattr(player, "last_error", "") or ""
            except (GLib.Error, AttributeError):
                return ""
        return ""


_bus_singleton = None


def get_instance(dbus_name):
    if is_snap():
        from apricus.snap_ipc import get_service

        return get_service(dbus_name)
    global _bus_singleton
    if _bus_singleton is None:
        _bus_singleton = SessionBus()
    try:
        instance = _bus_singleton.get(dbus_name)
    except GLib.Error:
        return None
    return instance


class _DBusServer:
    """Transport view: keep the server's logical/JSON snapshot unchanged."""

    dbus = ApricusServer.__doc__

    def __init__(self, server):
        object.__setattr__(self, "_server", server)

    def __getattr__(self, name):
        return getattr(self._server, name)

    def __setattr__(self, name, value):
        setattr(self._server, name, value)

    @property
    def engine_snapshot(self):
        # pydbus wraps this in GLib.Variant('a{sv}', ...): each 'v' must
        # already be a Variant, including an explicitly typed empty array.
        signatures = {
            "desired": "s",
            "wanted_mute": "b",
            "manual_stop": "b",
            "manual_pause": "b",
            "manual_mute": "b",
            "active_reasons": "as",
            "push_alive": "b",
            "generation": "t",
            "policy_version": "u",
            "debounced": "b",
            "window_state_source": "s",
        }
        snapshot = dict(self._server.engine_snapshot)
        snapshot["window_state_source"] = getattr(self._server, "_window_source", "none")
        return {key: GLib.Variant(signatures[key], value) for key, value in snapshot.items()}


def main(version, pkgdatadir, localedir, args):
    server = get_instance(DBUS_NAME_SERVER)
    if server is not None:
        server.show_gui()
    else:
        # Pause before launching
        if args.p:
            time.sleep(args.p)
        # Defer GUI spawn until the server is published on D-Bus / Snap IPC so
        # the GUI never races or blocks waiting for Introspect on cold start.
        want_gui = not args.background
        args.background = True
        server = ApricusServer(version, pkgdatadir, localedir, args)
        try:
            if is_snap():
                from apricus.snap_ipc import publish_service

                publish_service(DBUS_NAME_SERVER, server)
            else:
                global _bus_singleton
                if _bus_singleton is None:
                    _bus_singleton = SessionBus()
                _bus_singleton.publish(DBUS_NAME_SERVER, _DBusServer(server))
            if want_gui:
                server.show_gui()
            loop.run()
        except RuntimeError as e:
            raise Exception("Failed to create server") from e
