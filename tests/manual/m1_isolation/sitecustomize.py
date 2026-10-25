# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Opt-in test bootstrap. Python normally swallows sitecustomize errors: don't."""

import os

if os.environ.get("APRICUS_M1_PREFLIGHT_ROOT"):
    try:
        from m1_bootstrap import activate

        activate()
    except BaseException as error:
        os.write(2, f"M1 isolation bootstrap failed closed: {error!r}\n".encode())
        os._exit(78)
