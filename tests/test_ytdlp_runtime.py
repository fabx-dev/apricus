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
    monkeypatch.setattr("shutil.which", lambda cmd: None)
    options = yt_utils.base_options()
    assert options["noplaylist"] is True
    assert options["socket_timeout"] == 15
    # Empty runtime selection must not override yt-dlp's PATH discovery.
    assert "js_runtimes" not in options
    assert options["remote_components"] == []
    assert options["extractor_args"]["youtube"]["player_client"] == [
        "web_embedded",
        "tv_simply",
        "ios",
    ]
    assert "quiet" not in options
    assert yt_utils.base_options(quiet=True)["quiet"] is True
    monkeypatch.setattr("os.path.isfile", lambda path: path == "/app/bin/deno")
    opts_deno = yt_utils.base_options()
    assert opts_deno["js_runtimes"] == {"deno": {"path": "/app/bin/deno"}}
    assert opts_deno["extractor_args"]["youtube"]["player_client"] == ["web_embedded"]


def test_js_runtime_options_prefers_bundled_deno(monkeypatch):
    monkeypatch.setattr("os.path.isfile", lambda path: path == "/app/bin/deno")
    assert yt_utils.js_runtime_options() == {"deno": {"path": "/app/bin/deno"}}


def test_js_runtime_options_without_deno(monkeypatch):
    monkeypatch.setattr("os.path.isfile", lambda path: False)
    assert yt_utils.js_runtime_options() == {}


def test_get_formats_passes_shared_options(monkeypatch, tmp_path):
    _FakeYDL.seen.clear()
    monkeypatch.setattr(yt_utils, "_meta_cache_path", lambda: str(tmp_path / "cache.json"))
    monkeypatch.setattr(yt_utils.youtube_dl, "YoutubeDL", _FakeYDL)
    formats = yt_utils.get_formats("https://example.com/v")
    assert len(formats) == 1
    params = _FakeYDL.seen[-1]
    assert params["noplaylist"] is True
    assert params["remote_components"] == []


def test_get_formats_empty_raises(monkeypatch, tmp_path):
    class _Empty(_FakeYDL):
        def extract_info(self, url, download=False):
            return {}

    monkeypatch.setattr(yt_utils, "_meta_cache_path", lambda: str(tmp_path / "cache.json"))
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


def test_gtk4_callsite_uses_shared_options():
    """gtk4app.py must use base_options() for all YoutubeDL calls, verified
    by AST without importing Gtk in-process."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path("src/apricus/gui/gtk4app.py").read_text())
    found = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "YoutubeDL"
    ]
    assert found, "YoutubeDL call-site disappeared?"
    for call in found:
        src = ast.dump(call)
        assert "base_options" in src, f"call-site bypasses base_options: {src[:120]}"


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


def test_classify_missing_ytdlp_and_empty_format_errors():
    assert "not installed" in yt_utils.classify_stream_error("ERROR: No module named 'yt_dlp'")
    assert "update yt-dlp" in yt_utils.classify_stream_error(
        "ERROR: [youtube] abc123: No video formats found!"
    )
    assert "No playable formats" in yt_utils.classify_stream_error(
        "No playable formats found for this stream."
    )


def test_yt_dlp_version_probe_never_raises():
    version = yt_utils._yt_dlp_version()
    assert version is None or (len(version) == 3 and all(isinstance(n, int) for n in version))


def test_get_best_audio_without_audio_formats_raises_clear_error():
    from apricus.yt_utils import get_best_audio

    with pytest.raises(ValueError, match="[Aa]udio formats"):
        get_best_audio([_fmt("v", acodec="none", vcodec="h264")])
    with pytest.raises(ValueError, match="[Aa]udio formats"):
        get_best_audio([])


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


def _fmt(url, **kw):
    base = {"url": url, "vcodec": "h264", "acodec": "none"}
    base.update(kw)
    return base


def test_optimal_video_picks_closest_valid_height():
    from apricus.yt_utils import get_optimal_video

    formats = [_fmt("u720", width=1280, height=720), _fmt("u1080", width=1920, height=1080)]
    assert get_optimal_video(formats, 800) == ("u720", 1280, 720)
    assert get_optimal_video(formats, 1000) == ("u1080", 1920, 1080)


def test_optimal_video_missing_width_returns_none():
    from apricus.yt_utils import get_optimal_video

    (url, width, height) = get_optimal_video([_fmt("u", height=720)], 800)
    assert (url, width, height) == ("u", None, 720)


def test_optimal_video_missing_height_defers_to_known():
    from apricus.yt_utils import get_optimal_video

    formats = [_fmt("unknown"), _fmt("u720", width=1280, height=720)]
    assert get_optimal_video(formats, 800) == ("u720", 1280, 720)


def test_optimal_video_without_any_dims_returns_first_with_nones():
    from apricus.yt_utils import get_optimal_video

    assert get_optimal_video([_fmt("a"), _fmt("b")], 1080) == ("a", None, None)


def test_optimal_video_treats_none_and_nonnumeric_as_unknown():
    from apricus.yt_utils import get_optimal_video

    formats = [
        _fmt("bad", width="1280x", height="720p"),
        _fmt("none", width=None, height=None),
        _fmt("bool", width=True, height=False),
        _fmt("good", width=640, height=360),
    ]
    assert get_optimal_video(formats, 1080) == ("good", 640, 360)


def test_optimal_video_mixed_list_prefers_known_height():
    from apricus.yt_utils import get_optimal_video

    formats = [
        _fmt("nodims"),
        _fmt("u480", width=854, height=480),
        _fmt("u1080", width=1920, height=1080),
    ]
    assert get_optimal_video(formats, 1000) == ("u1080", 1920, 1080)
    assert get_optimal_video(formats, 400) == ("u480", 854, 480)


def test_optimal_video_empty_list_still_raises():
    import pytest

    from apricus.yt_utils import get_optimal_video

    with pytest.raises(ValueError):
        get_optimal_video([], 1080)


def test_optimal_video_generic_extractor_shape_no_keyerror():
    """Exact regression shape from the RC2 sandbox run: the generic
    extractor reports a single format with no codec or dimension keys."""
    from apricus.yt_utils import get_optimal_video

    bbb = (
        "https://test-videos.co.uk/vids/bigbuckbunny/mp4/h264/1080/Big_Buck_Bunny_1080_10s_30MB.mp4"
    )
    assert get_optimal_video([{"format_id": "mp4", "ext": "mp4", "url": bbb}], 1080) == (
        bbb,
        None,
        None,
    )


def test_best_selectors_share_dims_safety():
    from apricus.yt_utils import get_best, get_best_video

    assert get_best([_fmt("a", acodec="aac", quality=3)]) == ("a", None, None)
    assert get_best_video([_fmt("v", width=1280, height=720)]) == ("v", 1280, 720)
