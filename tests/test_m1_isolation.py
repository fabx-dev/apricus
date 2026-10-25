# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later
"""Fail-closed tests for the manual adapter, never start a desktop application."""

import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

WORKTREE = Path(__file__).resolve().parents[1]
ADAPTER = WORKTREE / "tests/manual/m1_isolation"
PYTHON = WORKTREE / ".venv/bin/python"


@pytest.fixture
def private_root():
    if not PYTHON.exists():
        pytest.skip("The manual adapter requires this worktree's independent .venv")
    (WORKTREE / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="m1p-", dir=WORKTREE / "build") as directory:
        root = Path(directory)
        for part in ("p/cache", "p/data", "p/t", "evidence"):
            (root / part).mkdir(parents=True, exist_ok=True)
        (root / "marker.json").write_text("{}")
        yield root


def environment(root):
    # Import the definition without activating the hook or importing Apricus.
    import ast

    tree = ast.parse((ADAPTER / "m1_bootstrap.py").read_text())
    protected = next(
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "PROTECTED" for target in node.targets
        )
    )
    return {
        **os.environ,
        "APRICUS_M1_PREFLIGHT_ROOT": str(root),
        "APRICUS_M1_PHASE": "preflight",
        "APRICUS_M1_HOST_ENV": json.dumps({key: os.environ.get(key) for key in protected}),
        "PYTHONPATH": os.pathsep.join((str(ADAPTER), str(WORKTREE / "src"))),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "XDG_CACHE_HOME": str(root / "p/cache"),
        "XDG_DATA_HOME": str(root / "p/data"),
        "TMPDIR": str(root / "p/t"),
    }


@pytest.mark.parametrize("failure", ["phase", "context", "permissions", "marker"])
def test_sitecustomize_fails_closed_before_application_import(private_root, failure):
    env = environment(private_root)
    if failure == "phase":
        env["APRICUS_M1_PHASE"] = "runtime"
    elif failure == "context":
        expected = json.loads(env["APRICUS_M1_HOST_ENV"])
        expected["HOME"] = "/wrong-host-context"
        env["APRICUS_M1_HOST_ENV"] = json.dumps(expected)
    elif failure == "permissions":
        private_root.chmod(0o755)
    else:
        (private_root / "marker.json").unlink()
    result = subprocess.run(
        [str(PYTHON), "-c", 'print("MUST_NOT_EXECUTE")'],
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 78
    assert "MUST_NOT_EXECUTE" not in result.stdout
    assert "failed closed" in result.stderr


def test_launcher_has_no_runtime_command():
    if not PYTHON.exists():
        pytest.skip("Manual adapter .venv absent")
    result = subprocess.run(
        [str(PYTHON), str(ADAPTER / "launcher.py"), "--run"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "Preflight only" in result.stderr


@pytest.mark.parametrize(
    "log,expected",
    [
        ("normal probe output", True),
        ("dconf-CRITICAL **: unable to create file", False),
        ("dconf will not work properly.", False),
    ],
)
def test_preflight_rejects_native_dconf_failure_even_with_matching_values(log, expected):
    import importlib.util
    import sys

    # These manual modules have no activation/launch side effects on import.
    old_path = sys.path[:]
    try:
        sys.path.insert(0, str(ADAPTER))
        spec = importlib.util.spec_from_file_location(
            "m1_diagnostic_launcher", ADAPTER / "launcher.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        assert module.native_dconf_clean(log) is expected
    finally:
        sys.path[:] = old_path


def manual_module(name):
    import importlib.util

    spec = importlib.util.spec_from_file_location(name, ADAPTER / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dconf_changed_identity_fails_closed(monkeypatch):
    module = manual_module("landlock_guard")
    monkeypatch.setattr(module, "dconf_identity", lambda: {"inode": 2})
    with pytest.raises(RuntimeError, match="identity/permissions changed"):
        module.check_dconf({"inode": 1})


def test_dconf_unapproved_runtime_fails_closed(monkeypatch):
    module = manual_module("landlock_guard")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/unexpected/runtime")
    with pytest.raises(RuntimeError, match="approved only"):
        module.dconf_identity()


def test_future_native_logging_lifetime_without_loading_vlc(tmp_path):
    import ctypes

    module = manual_module("vlc_diagnostics")
    calls = []

    class FakeInstance:
        def log_set_file(self, stream):
            assert ctypes.cast(stream, ctypes.c_void_p).value
            calls.append("set")

        def log_unset(self):
            calls.append("unset")

    logger = module.NativeLog(
        FakeInstance(), tmp_path, file_pointer_type=ctypes.POINTER(ctypes.c_char)
    )
    assert logger.path.exists()
    logger.close()
    logger.close()
    assert calls == ["set", "unset"]


def test_native_snapshot_allocates_required_media_stats(tmp_path):
    module = manual_module("vlc_diagnostics")
    vlc = pytest.importorskip("vlc")

    class FakeMedia:
        def get_stats(self, stats):
            assert isinstance(stats, vlc.MediaStats)
            stats.decoded_video = 7
            return 1

    record = json.loads(module.snapshot(tmp_path, media=FakeMedia()).read_text())
    assert record["media_stats_available"] is True
    assert record["media_stats"]["decoded_video"] == 7


def test_diagnostic_failure_does_not_block_player_cleanup(tmp_path, monkeypatch):
    from types import SimpleNamespace

    module = manual_module("vlc_diagnostics")
    calls = []
    widget = SimpleNamespace(
        player=None, _m1_native_log=SimpleNamespace(close=lambda: calls.append("close"))
    )

    def fail(*args, **kwargs):
        raise TypeError("snapshot failed")

    monkeypatch.setattr(module, "snapshot", fail)
    module.cleanup(widget, tmp_path, lambda obj: calls.append("production_cleanup"))
    assert calls == ["close", "production_cleanup"]
    assert widget._m1_native_log is None
