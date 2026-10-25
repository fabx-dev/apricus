"""Native test-supervisor recovery tests: no live app or GNOME mutations."""

import importlib.util
import json
from pathlib import Path


def load_session():
    path = Path(__file__).parent / "manual/m1_native/session.py"
    spec = importlib.util.spec_from_file_location("m1_native_session_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recovery_preserves_test_data_restores_original_and_never_reinstalls(tmp_path, monkeypatch):
    module = load_session()
    root = tmp_path / "evidence"
    (root / "backup/config").mkdir(parents=True)
    (root / "backup/config/config.json").write_text("original")
    config = tmp_path / "native-config"
    config.mkdir()
    (config / "config.json").write_text("test-state")
    module.write(
        root / "manifest.json",
        dict(paths=[dict(label="config", path=str(config), existed=True)], settings={}),
    )
    module.write(root / "state.json", dict(processes=[], restored=False))
    monkeypatch.setattr(module, "settings", lambda: {})
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)

    def no_external_command(*args, **kwargs):
        raise AssertionError("No Snap or GNOME command allowed in this recovery test")

    monkeypatch.setattr(module.subprocess, "run", no_external_command)
    module.recover(root)
    module.recover(root)
    assert (config / "config.json").read_text() == "original"
    assert (root / "after-test-config/config.json").read_text() == "test-state"
    state = json.loads((root / "state.json").read_text())
    assert state["restored"] is True
    assert state["snap_reinstalled"] is False


def test_recovery_keeps_previously_absent_app_directory_absent(tmp_path, monkeypatch):
    module = load_session()
    root = tmp_path / "evidence"
    root.mkdir()
    created = tmp_path / "new-app-cache"
    created.mkdir()
    (created / "generated.png").write_bytes(b"fixture")
    module.write(
        root / "manifest.json",
        dict(paths=[dict(label="cache", path=str(created), existed=False)], settings={}),
    )
    module.write(root / "state.json", dict(processes=[], restored=False))
    monkeypatch.setattr(module, "settings", lambda: {})
    monkeypatch.setattr(module.time, "sleep", lambda seconds: None)
    module.recover(root)
    assert not created.exists()
    assert (root / "after-test-cache/generated.png").read_bytes() == b"fixture"
