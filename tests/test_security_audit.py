# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Security audit regression tests (SEC-A through SEC-F)."""

import ast
import json
import pathlib
import re
import stat

from apricus import utils

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_sec_a_probes_refuse_intermediate_redirect_to_loopback_without_contact():
    """SEC-A: _check_direct_url and _check_web_url must validate every
    redirect hop with allow_redirects=False BEFORE contacting it."""
    import subprocess
    import sys

    code = """
import sys
sys.path.insert(0, "src")
import apricus.gui.gtk4app as appmod

class _Resp:
    def __init__(self, status, location="", ctype="text/html"):
        self.status_code = status
        self.headers = {"Content-Type": ctype}
        if location:
            self.headers["Location"] = location
    def close(self):
        pass

contacted = []
def fake_head(url, **kw):
    assert kw.get("allow_redirects") is False, kw
    contacted.append(("HEAD", url))
    return _Resp(302, location="http://127.0.0.1:8080/internal.mp4")

def fake_get(url, **kw):
    assert kw.get("allow_redirects") is False, kw
    contacted.append(("GET", url))
    return _Resp(301, location="http://192.168.1.1/admin")

appmod.requests.head = fake_head
appmod.requests.get = fake_get
panel = appmod.ControlPanel.__new__(appmod.ControlPanel)

res_direct = panel._check_direct_url("https://93.184.216.34/video.mp4")
assert res_direct == "fallback", res_direct
assert contacted == [("HEAD", "https://93.184.216.34/video.mp4")], contacted

contacted.clear()
res_web = panel._check_web_url("https://93.184.216.34/page")
assert res_web == "Redirects to a local network address.", res_web
assert contacted == [("GET", "https://93.184.216.34/page")], contacted
print("SEC-A-OK")
"""
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "SEC-A-OK" in proc.stdout, f"stdout={proc.stdout!r} stderr={proc.stderr!r}"


def test_sec_b_player_dbus_methods_require_authorization():
    """SEC-B: D-Bus methods on BasePlayer, VideoPlayer and WebPlayer must
    invoke _authorize()."""
    targets = {
        "src/apricus/player/base_player.py": {"quit_player"},
        "src/apricus/player/video_player.py": {
            "switch_media",
            "preview_static",
            "reload_config",
            "pause_playback",
            "start_playback",
            "quit_player",
        },
        "src/apricus/player/web_player.py": {
            "reload_config",
            "pause_playback",
            "start_playback",
        },
    }
    for rel_path, method_names in targets.items():
        tree = ast.parse((ROOT / rel_path).read_text())
        found = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in method_names:
                calls = [
                    c.func.id
                    for c in ast.walk(node)
                    if isinstance(c, ast.Call) and isinstance(c.func, ast.Name)
                ]
                found[node.name] = "_authorize" in calls
        for name in method_names:
            assert found.get(name), f"{rel_path}:{name} missing _authorize() call"


def test_sec_c_web_player_enforces_permission_deny_and_navigation_policy():
    """SEC-C: WebWindow must deny WebKit permission requests and guard
    remote navigation against file:// and non-public hosts."""
    src = (ROOT / "src/apricus/player/web_player.py").read_text()
    assert '"permission-request"' in src
    assert '"decide-policy"' in src
    assert "request.deny()" in src
    assert "NEW_WINDOW_ACTION" in src
    assert "public_http_url" in src


def test_sec_d_no_shell_true_in_subprocesses_and_vlc_no_interact():
    """SEC-D: No subprocess call in src/apricus/ may use shell=True, and
    VLCWidget must pass --no-interact."""
    offenders = []
    for py_file in (ROOT / "src/apricus").rglob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if (
                        kw.arg == "shell"
                        and isinstance(kw.value, ast.Constant)
                        and kw.value.value is True
                    ):
                        offenders.append(f"{py_file.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, f"shell=True found in: {offenders}"

    vp_src = (ROOT / "src/apricus/player/video_player.py").read_text()
    assert '"--no-interact"' in vp_src


def test_sec_e_config_and_backup_written_with_0600_permissions(tmp_path, monkeypatch):
    """SEC-E: ConfigUtil.save() must set 0700 on CONFIG_DIR and 0600 on
    config.json and config.json.bak."""
    cfg_dir = tmp_path / "apricus"
    cfg_path = cfg_dir / "config.json"
    monkeypatch.setattr(utils, "CONFIG_DIR", str(cfg_dir))
    monkeypatch.setattr(utils, "CONFIG_PATH", str(cfg_path))
    monkeypatch.setattr(utils, "ensure_template_monitors", lambda: None)

    cu = utils.ConfigUtil()
    cfg1 = dict(utils.CONFIG_TEMPLATE)
    cfg1["audio_volume"] = 40
    cu.save(cfg1)
    assert cfg_path.is_file()
    assert stat.S_IMODE(cfg_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600

    cfg2 = dict(cfg1)
    cfg2["audio_volume"] = 75
    cu.save(cfg2)
    bak_path = pathlib.Path(str(cfg_path) + ".bak")
    assert bak_path.is_file()
    assert stat.S_IMODE(bak_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg_path.stat().st_mode) == 0o600


def test_sec_f_supply_chain_pins_in_ci_and_flatpak_manifests():
    """SEC-F: All GitHub Actions must be pinned to 40-char commit SHAs and
    all Flatpak archive/file/git sources must have pinned sha256 or commit."""
    sha40 = re.compile(r"^[0-9a-f]{40}$")
    sha64 = re.compile(r"^[0-9a-f]{64}$")

    for wf in (ROOT / ".github/workflows").glob("*.yml"):
        for lineno, line in enumerate(wf.read_text().splitlines(), 1):
            m = re.search(r"^\s*-?\s*uses:\s*([^\s#]+)", line)
            if m:
                ref = m.group(1)
                assert "@" in ref, f"{wf.name}:{lineno} unpinned uses: {ref}"
                _, rev = ref.split("@", 1)
                assert sha40.match(rev), f"{wf.name}:{lineno} non-SHA uses pin: {ref}"

    def _walk_sources(obj, origin):
        if isinstance(obj, dict):
            if obj.get("type") in ("archive", "file"):
                digest = obj.get("sha256", "")
                assert sha64.match(digest), f"{origin} missing/invalid sha256: {obj}"
            elif obj.get("type") == "git":
                commit = obj.get("commit", "")
                assert sha40.match(commit), f"{origin} missing/invalid git commit: {obj}"
            for v in obj.values():
                _walk_sources(v, origin)
        elif isinstance(obj, list):
            for item in obj:
                _walk_sources(item, origin)

    for mf in (ROOT / "pkgs/flatpak").glob("*.json"):
        data = json.loads(mf.read_text())
        _walk_sources(data, mf.name)
