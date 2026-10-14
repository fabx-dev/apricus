# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for the shared yt-dlp options (no display, no network)."""

import pytest

from apricus import yt_utils


class _FakeYDL:
    seen = []

    def __init__(self, params):
        type(self).seen.append(dict(params))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=False):
        return {"formats": [{"url": "http://x/v.mp4", "vcodec": "h264", "acodec": "aac"}]}


def test_base_options_shape(monkeypatch):
    monkeypatch.setattr("os.path.isfile", lambda path: False)
    options = yt_utils.base_options()
    assert options["noplaylist"] is True
    assert options["socket_timeout"] == 15
    # Empty runtime selection must not override yt-dlp's PATH discovery.
    assert "js_runtimes" not in options
    assert options["remote_components"] == []
    assert "quiet" not in options
    assert yt_utils.base_options(quiet=True)["quiet"] is True
    monkeypatch.setattr("os.path.isfile", lambda path: path == "/app/bin/deno")
    assert yt_utils.base_options()["js_runtimes"] == {"deno": {"path": "/app/bin/deno"}}


def test_js_runtime_options_prefers_bundled_deno(monkeypatch):
    monkeypatch.setattr("os.path.isfile", lambda path: path == "/app/bin/deno")
    assert yt_utils.js_runtime_options() == {"deno": {"path": "/app/bin/deno"}}


def test_js_runtime_options_without_deno(monkeypatch):
    monkeypatch.setattr("os.path.isfile", lambda path: False)
    assert yt_utils.js_runtime_options() == {}


def test_get_formats_passes_shared_options(monkeypatch):
    _FakeYDL.seen.clear()
    monkeypatch.setattr(yt_utils.youtube_dl, "YoutubeDL", _FakeYDL)
    formats = yt_utils.get_formats("https://example.com/v")
    assert len(formats) == 1
    params = _FakeYDL.seen[-1]
    assert params["noplaylist"] is True
    assert params["remote_components"] == []


def test_get_formats_empty_raises(monkeypatch):
    class _Empty(_FakeYDL):
        def extract_info(self, url, download=False):
            return {}

    monkeypatch.setattr(yt_utils.youtube_dl, "YoutubeDL", _Empty)
    with pytest.raises(ValueError):
        yt_utils.get_formats("https://example.com/v")


def test_get_formats_rejects_non_http_without_network(monkeypatch):
    """SSRF gate: ftp/file/credentialed URLs never reach YoutubeDL."""

    def _bomb(*args, **kwargs):
        raise AssertionError("YoutubeDL must not run for rejected URLs")

    monkeypatch.setattr(yt_utils.youtube_dl, "YoutubeDL", _bomb)
    for bad in (
        "ftp://example.com/v.mp4",
        "file:///etc/passwd",
        "http://user:pass@example.com/v",
        "https://user@example.com/v",
        "nota url",
        "",
        None,
    ):
        with pytest.raises(ValueError):
            yt_utils.get_formats(bad)


def test_get_formats_rejects_non_public_hosts_without_network(monkeypatch):
    """RC2-SEC-03: loopback/private literals never reach YoutubeDL
    (numeric IPs need no DNS, so this test uses no network)."""

    def _bomb(*args, **kwargs):
        raise AssertionError("YoutubeDL must not run for non-public hosts")

    monkeypatch.setattr(yt_utils.youtube_dl, "YoutubeDL", _bomb)
    for bad in (
        "http://127.0.0.1/v.mp4",
        "http://10.0.0.1/v.mp4",
        "http://192.168.1.1/v.mp4",
        "http://169.254.10.20/v.mp4",
    ):
        with pytest.raises(ValueError, match="[Ll]ocal network"):
            yt_utils.get_formats(bad)


def test_legacy_callsite_uses_shared_options():
    """control.py (GTK3 legacy) must use base_options(), verified by AST
    so the suite never imports Gtk 3.0 in-process."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/apricus/gui/control.py").read_text())
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "YoutubeDL"
    ]
    assert found, "legacy YoutubeDL call-site disappeared?"
    for call in found:
        src = ast.dump(call)
        assert "base_options" in src, f"legacy call-site bypasses base_options: {src[:120]}"


ERROR_CASES = [
    ("ERROR: Sign in to confirm you're not a bot", "bot verification"),
    ("ERROR: captcha challenge before playback", "bot verification"),
    ("ERROR: Private video", "login or is private"),
    ("ERROR: age-restricted video", "Age-restricted"),
    ("ERROR: geo-restricted content", "region"),
    ("ERROR: DRM protected formats", "DRM"),
    ("ERROR: 429 Too Many Requests", "Rate limited"),
    ("ERROR: 404 Not Found", "404"),
    ("ERROR: Unsupported URL", "not supported"),
    ("ERROR: PO Token missing, 403", "JavaScript runtime"),
    ("ERROR: Signature solving failed", "JavaScript runtime"),
]


@pytest.mark.parametrize("raw,expected", ERROR_CASES)
def test_error_classes_without_runtime_assumptions(raw, expected):
    assert expected in yt_utils.classify_stream_error(raw)


def test_classify_stream_error_handles_none_and_plain():
    assert yt_utils.classify_stream_error(None) == "Unknown stream error"
    assert yt_utils.classify_stream_error("") == "Unknown stream error"


def test_flatpak_bundles_pinned_deno():
    """The Flatpak must ship Deno at /app/bin/deno (pinned, both arches);
    never inside the generated pypi-deps.json."""
    import json
    import pathlib

    root = pathlib.Path("pkgs/flatpak")
    manifest = json.loads((root / "io.github.fabx_dev.Apricus.json").read_text())
    assert "deno.json" in manifest["modules"]
    deno = json.loads((root / "deno.json").read_text())
    assert deno["name"] == "deno"
    commands = deno["build-commands"]
    assert any("/app/bin/deno" in cmd for cmd in commands)
    arches = set()
    for source in deno["sources"]:
        assert source["type"] == "archive"
        assert source["url"].startswith("https://github.com/denoland/deno/releases/download/")
        assert len(source.get("sha256", "")) == 64, "pinned hash required"
        arches.update(source.get("only-arches", []))
    assert {"x86_64", "aarch64"} <= arches
    pypi = (root / "pypi-deps.json").read_text()
    assert "deno" not in pypi.lower(), "Deno must not live in generated pypi-deps"
