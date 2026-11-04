# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Cancellable subprocesses owned by a player (never kill unrelated FFmpeg)."""

import subprocess
import threading


class ChildJobs:
    def __init__(self):
        self.lock = threading.Lock()
        self.children = set()
        self.closed = False

    def run(self, args, timeout=60, **kwargs):
        with self.lock:
            if self.closed:
                raise OSError("Player jobs are closed")
            child = subprocess.Popen(args, **kwargs)
            self.children.add(child)
        try:
            stdout, stderr = child.communicate(timeout=timeout)
            return subprocess.CompletedProcess(args, child.returncode, stdout, stderr)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            raise
        finally:
            with self.lock:
                self.children.discard(child)

    def close(self):
        with self.lock:
            self.closed = True
            children = list(self.children)
        for child in children:
            try:
                child.terminate()
                child.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=1)
            except ProcessLookupError:
                pass
