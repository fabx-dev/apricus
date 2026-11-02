"""Server-owned, transient web interaction transaction (no policy overrides)."""

import secrets

WALLPAPER = "WALLPAPER"
ENTERING = "ENTERING"
INTERACTIVE = "INTERACTIVE"
EXITING = "EXITING"


class WebInteraction:
    def __init__(self, eligible, player_call, publish, later, remove):
        self.eligible = eligible
        self.player_call = player_call
        self.publish = publish
        self.later = later
        self.remove = remove
        self.state = WALLPAPER
        self.token = ""
        self.pid = 0
        self.title = ""
        self.reason = "integration-unavailable"
        self.ready = False
        self.timer = None
        self.deferred = {}
        self.serial = 0
        self.blocked_pid = 0

    def status(self):
        reason = self.eligible() or ("" if self.ready else "integration-unavailable")
        return {
            "state": self.state,
            "available": self.ready and not reason and self.state == WALLPAPER,
            "reason": reason or self.reason,
        }

    def request(self):
        return {
            "version": 1,
            "state": self.state,
            "token": self.token,
            "pid": self.pid,
            "title": self.title,
        }

    def integration(self, ready):
        self.ready = bool(ready)
        if not ready:
            self.end("integration-lost")

    def _cancel_timer(self):
        if self.timer is not None:
            self.remove(self.timer)
            self.timer = None

    def _deadline(self, callback):
        self._cancel_timer()
        self.timer = self.later(4000, callback)

    def begin(self, pid):
        if self.state != WALLPAPER:
            return False
        if not self.ready or self.eligible():
            self.reason = self.eligible() or "integration-unavailable"
            return False
        self.token = token = secrets.token_hex(16)
        self.serial += 1
        self.pid = pid
        self.title = f"Apricus Web [{token}]"
        self.state = ENTERING
        self.reason = ""
        self._deadline(lambda: self._enter_timeout(token))

        def prepared(value, error):
            if self.token != token or self.state != ENTERING:
                return
            if (
                error
                or not isinstance(value, dict)
                or value.get("pid") != pid
                or value.get("title") != self.title
            ):
                self.end("prepare-failed")
            elif self.eligible():
                self.end("policy-changed")
            else:
                self.publish(self.request())

        self.player_call("web_interaction_prepare", [token, pid], prepared)
        return True

    def _enter_timeout(self, token):
        self.timer = None
        if token == self.token and self.state == ENTERING:
            self.end("enter-timeout")
        return False

    def acknowledge(self, token, phase, ok):
        if token != self.token or not token:
            return False
        if phase == INTERACTIVE and self.state == ENTERING:
            if not ok or self.eligible():
                self.end("entry-rejected")
                return False
            self._cancel_timer()
            self.state = INTERACTIVE
            return True
        if phase == WALLPAPER and self.state in (ENTERING, INTERACTIVE):
            # Done/shortcut/local Shell teardown: Shell has already rolled back.
            self.state = EXITING
            self._restore(token, ok)
            return True
        if phase == WALLPAPER and self.state == EXITING:
            self._restore(token, ok)
            return True
        return False

    def end(self, reason, after=None, key="policy"):
        if self.state == WALLPAPER:
            if after:
                after()
            return False
        if after:
            self.deferred[key] = after
        if self.state == EXITING:
            return True
        self.reason = reason
        self.state = EXITING
        token = self.token
        self.publish(self.request())
        self._deadline(lambda: self._exit_timeout(token))
        return True

    def _exit_timeout(self, token):
        self.timer = None
        if token == self.token and self.state == EXITING:
            self._restore(token, False)
        return False

    def _restore(self, token, safe):
        self._cancel_timer()
        # Retire this transaction before asynchronous restoration. Late Shell
        # acknowledgements cannot cause a second restoration or a fresh entry.
        self.token = ""
        serial = self.serial
        pid = self.pid

        def restored(value, error):
            if self.state != EXITING or self.token or self.serial != serial:
                return
            self._cancel_timer()
            if error or value is not True:
                self.reason = "restore-failed"
                self.blocked_pid = pid
            elif not safe:
                self.reason = "surface-hidden"
                self.blocked_pid = pid
            self.state = WALLPAPER
            self.pid = 0
            self.title = ""
            self.publish(self.request())
            pending, self.deferred = self.deferred, {}
            if "quit" in pending:
                pending["quit"]()
            else:
                for action in pending.values():
                    action()

        self._deadline(lambda: restored(None, "restore-timeout") or False)
        self.player_call("web_interaction_restore", [token, bool(safe), self.pid], restored)


class InteractionMailbox:
    """One authenticated outstanding Shell read, awakened only by changes."""

    def __init__(self):
        self.revision = 1
        self.payload = {"version": 1, "state": WALLPAPER, "token": "", "pid": 0, "title": ""}
        self.waiter = None

    def update(self, payload):
        self.payload = dict(payload)
        self.revision += 1
        self._deliver()

    def watch(self, revision, deliver, reject):
        if self.waiter:
            self.waiter[1]("replaced")
        self.waiter = (deliver, reject)
        if revision != self.revision:
            self._deliver()

    def _deliver(self):
        if self.waiter:
            (deliver, _), self.waiter = self.waiter, None
            deliver(self.revision, self.payload)

    def close(self):
        if self.waiter:
            (_, reject), self.waiter = self.waiter, None
            reject("closed")
