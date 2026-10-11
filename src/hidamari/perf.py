import logging
import os
import time

from hidamari.commons import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

# Captured at import time. `hidamari.perf` must be imported before any other
# `hidamari` module so T0 is the true process start (see `__main__.py`).
# Forkserver children inherit HIDAMARI_T0 from the server so their marks are
# on the same login-timescale clock (see server.HidamariServer).
try:
    _T0 = float(os.environ.get("HIDAMARI_T0", "0")) or time.monotonic()
except ValueError:
    _T0 = time.monotonic()


def start_time():
    return _T0


def mark(label):
    """Log elapsed time since process start. Always INFO: startup timing is
    a baseline metric (Fase 1b), not debug noise."""
    logger.info("[Perf] %s: %.2fs after start", label, time.monotonic() - _T0)


def elapsed():
    return time.monotonic() - _T0
