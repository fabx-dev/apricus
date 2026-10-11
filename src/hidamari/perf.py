import logging
import time

from hidamari.commons import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

# Captured at import time. `hidamari.perf` must be imported before any other
# `hidamari` module so T0 is the true process start (see `__main__.py`).
_T0 = time.monotonic()


def mark(label):
    """Log elapsed time since process start. Always INFO: startup timing is
    a baseline metric (Fase 1b), not debug noise."""
    logger.info("[Perf] %s: %.2fs after start", label, time.monotonic() - _T0)


def elapsed():
    return time.monotonic() - _T0
