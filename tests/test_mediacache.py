"""Tests for apricus.mediacache (no display, no ffprobe required)."""

import json
import os
from unittest.mock import patch

from apricus import commons, mediacache


def _use_tmp_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(commons, "CONFIG_DIR", str(tmp_path))
    return tmp_path / mediacache.CACHE_FILENAME


def test_cache_hit_avoids_ffprobe(tmp_path, monkeypatch):
    cache_file = _use_tmp_cache(tmp_path, monkeypatch)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"fake")
    st = os.stat(video)
    cache_file.write_text(
        json.dumps(
            {
                str(video): {
                    "mtime": st.st_mtime,
                    "size": st.st_size,
                    "width": 1920,
                    "height": 1080,
                    "duration": 12.5,
                }
            }
        )
    )
    with patch.object(
        mediacache.subprocess, "check_output", side_effect=AssertionError("ffprobe called")
    ):
        assert mediacache.probe_video(str(video)) == (1920, 1080, 12.5)


def test_stale_fingerprint_reprobes(tmp_path, monkeypatch):
    cache_file = _use_tmp_cache(tmp_path, monkeypatch)
    video = tmp_path / "b.mp4"
    video.write_bytes(b"fake")
    cache_file.write_text(
        json.dumps({str(video): {"mtime": 0, "size": 0, "width": 1, "height": 1}})
    )
    fake_info = json.dumps(
        {"streams": [{"width": 640, "height": 480}], "format": {"duration": "3.0"}}
    )
    with patch.object(mediacache.subprocess, "check_output", return_value=fake_info):
        assert mediacache.probe_video(str(video)) == (640, 480, 3.0)
    assert json.loads(cache_file.read_text())[str(video)]["width"] == 640


def test_missing_file_never_raises(tmp_path, monkeypatch):
    _use_tmp_cache(tmp_path, monkeypatch)
    assert mediacache.probe_video(str(tmp_path / "nope.mp4")) == (None, None, None)


def test_corrupt_cache_is_ignored(tmp_path, monkeypatch):
    cache_file = _use_tmp_cache(tmp_path, monkeypatch)
    cache_file.write_text("not json{")
    video = tmp_path / "c.mp4"
    video.write_bytes(b"fake")
    fake_info = json.dumps(
        {"streams": [{"width": 320, "height": 240}], "format": {"duration": "1.5"}}
    )
    with patch.object(mediacache.subprocess, "check_output", return_value=fake_info):
        assert mediacache.probe_video(str(video)) == (320, 240, 1.5)
