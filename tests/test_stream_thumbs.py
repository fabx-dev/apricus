# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Tests for apricus.stream_thumbs (headless: no display, no network)."""

import hashlib
import json
import logging
import os
import pathlib
import sys
import threading
import time

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf  # noqa: E402

from apricus import stream_thumbs as mod  # noqa: E402
from apricus.stream_thumbs import (  # noqa: E402
    ThumbCache,
    ThumbFetcher,
    cache_path_for,
    canonical_url,
    check_magic,
    decode_size,
    downscale_png,
    is_live_meta,
)

GIF_1X1 = (
    b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff"
    b"!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;"
)
HTML = b"<!DOCTYPE html><html><body>not an image</body></html>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"></svg>'
TEXT = b"plain text, definitely not an image"
ICO_HEAD = b"\x00\x00\x01\x00" + b"\x00" * 32
WEBP_HEAD = b"RIFF\x12\x34\x56\x78WEBP" + b"\x00" * 16
RIFF_NOT_WEBP = b"RIFF\x12\x34\x56\x78AVI " + b"\x00" * 16


def _png(w, h):
    pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pix.fill(0x22446600)
    ok, buf = pix.save_to_bufferv("png", [], [])
    assert ok
    return bytes(buf)


def _jpeg(w=32, h=16):
    pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, w, h)
    pix.fill(0x11223300)
    ok, buf = pix.save_to_bufferv("jpeg", [], [])
    assert ok
    return bytes(buf)


class FakeClock:
    def __init__(self, start=1_700_000_000.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def _patch_time(monkeypatch, clock):
    monkeypatch.setattr("apricus.stream_thumbs.time.time", clock)


def _url(i=0, query=""):
    return f"http://example.com/thumb{i}.png{query}"


def _leftover_files(cache_dir):
    bad = []
    for root, _dirs, files in os.walk(cache_dir):
        for name in files:
            if name.endswith((".png", ".tmp")):
                bad.append(os.path.join(root, name))
    return bad


# --- canonical_url -------------------------------------------------------


def test_canonical_strips_tracking_keeps_others():
    url = "HTTP://Example.COM:8080/img.png?b=2&utm_source=x&A=1&fbclid=zzz&utm_medium=y#frag"
    assert canonical_url(url) == "http://example.com:8080/img.png?b=2&A=1"


def test_canonical_removes_all_trackers():
    url = "https://h.test/p?v=9&utm_source=a&utm_medium=b&utm_campaign=c&utm_content=d&utm_term=e&si=f&fbclid=g&gclid=h"
    assert canonical_url(url) == "https://h.test/p?v=9"


def test_canonical_tracker_match_is_case_insensitive():
    assert canonical_url("https://h.test/p?UTM_SOURCE=x&v=1") == "https://h.test/p?v=1"


def test_canonical_strips_and_lowercases():
    assert canonical_url("  HTTPS://EXAMPLE.com/Path?q=1#sec  ") == "https://example.com/Path?q=1"


def test_canonical_non_string_raises():
    try:
        canonical_url(None)
    except TypeError:
        return
    raise AssertionError("expected TypeError")


# --- cache_path_for ------------------------------------------------------


def test_cache_path_format_and_deterministic(tmp_path):
    path = cache_path_for(_url(), str(tmp_path))
    assert os.path.dirname(path) == str(tmp_path)
    base = os.path.basename(path)
    assert base.endswith(".png") and len(base) == 16 + 4
    int(base[:-4], 16)
    assert cache_path_for(_url(), str(tmp_path)) == path
    canon = canonical_url(_url())
    expect = hashlib.sha256(canon.encode()).hexdigest()[:16] + ".png"
    assert base == expect


def test_cache_path_no_traversal(tmp_path):
    evil = "http://evil.test/../../etc/passwd?x=1#frag"
    path = cache_path_for(evil, str(tmp_path))
    assert os.path.dirname(path) == str(tmp_path)
    assert ".." not in os.path.basename(path) and "/" not in os.path.basename(path)


def test_cache_path_tracking_variants_collide(tmp_path):
    a = cache_path_for("http://example.com/i.png?utm_source=x", str(tmp_path))
    b = cache_path_for("http://example.com/i.png", str(tmp_path))
    assert a == b


# --- magic ---------------------------------------------------------------


def test_magic_table():
    assert check_magic(_png(8, 8)) is True
    assert check_magic(_jpeg()) is True
    assert check_magic(GIF_1X1) is True
    assert check_magic(ICO_HEAD) is True
    assert check_magic(WEBP_HEAD) is True
    assert check_magic(RIFF_NOT_WEBP) is False
    assert check_magic(SVG) is False
    assert check_magic(HTML) is False
    assert check_magic(TEXT) is False
    assert check_magic(b"") is False
    assert check_magic(b"\x89P") is False
    assert check_magic(None) is False
    assert check_magic("not-bytes") is False


def test_magic_keys():
    assert set(mod.MAGIC) == {"PNG", "JPEG", "GIF", "ICO", "WEBP"}


# --- decode_size / downscale_png -----------------------------------------


def test_decode_size_valid():
    assert decode_size(_png(64, 32)) == (64, 32)
    assert decode_size(_jpeg(32, 16)) == (32, 16)
    assert decode_size(GIF_1X1) == (1, 1)


def test_decode_size_rejects():
    assert decode_size(SVG) is None
    assert decode_size(HTML) is None
    assert decode_size(b"") is None
    assert decode_size(b"\x00\x01\x02\x03" * 16) is None
    truncated = _png(64, 32)
    # A bare PNG signature with no IHDR can never yield dimensions, on any
    # loader version (half-file still decodes on lenient loaders).
    assert decode_size(truncated[:8]) is None


def test_decode_size_rejects_oversize_width():
    assert decode_size(_png(4100, 2)) is None


def test_decode_size_rejects_huge_area(monkeypatch):
    class FakePix:
        def get_width(self):
            return 4000

        def get_height(self):
            return 5000

    monkeypatch.setattr(mod, "_load_pixbuf", lambda data: FakePix())
    assert decode_size(_png(8, 8)) is None


def test_downscale_wide_image(tmp_path):
    out = downscale_png(_png(256, 64))
    assert out is not None and out[:4] == b"\x89PNG"
    assert decode_size(out) == (128, 32)


def test_downscale_keeps_narrow_image():
    out = downscale_png(_png(64, 32))
    assert out is not None
    assert decode_size(out) == (64, 32)


def test_downscale_rejects():
    assert downscale_png(SVG) is None
    assert downscale_png(b"") is None
    assert downscale_png(_png(4100, 2)) is None


# --- ThumbCache store/lookup ---------------------------------------------


def test_store_lookup_hit_memory_and_disk(tmp_path):
    cache = ThumbCache(str(tmp_path))
    url = _url()
    stored = cache.store(url, _png(64, 32), False)
    assert stored is not None and os.path.isfile(stored)
    assert cache.lookup(url) == stored
    assert cache.lookup(url) == stored
    fresh = ThumbCache(str(tmp_path))
    assert fresh.lookup(url) == stored


def test_store_writes_index_sidecar(tmp_path):
    cache = ThumbCache(str(tmp_path))
    cache.store(_url(), _png(32, 16), True)
    with open(os.path.join(str(tmp_path), "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    (entry,) = index.values()
    assert entry["is_live"] is True
    assert isinstance(entry["fetched_at"], float)
    assert entry["file"].endswith(".png")


def test_lookup_miss_absent(tmp_path):
    assert ThumbCache(str(tmp_path)).lookup(_url()) is None


def test_store_invalid_leaves_no_partials(tmp_path):
    cache = ThumbCache(str(tmp_path))
    assert cache.store(_url(), HTML, False) is None
    assert cache.store(_url(1), b"", False) is None
    assert cache.store(_url(2), None, False) is None
    assert _leftover_files(str(tmp_path)) == []


def test_lookup_corrupt_file_returns_none_and_cleans(tmp_path):
    cache = ThumbCache(str(tmp_path))
    url = _url()
    stored = cache.store(url, _png(32, 16), False)
    with open(stored, "wb") as f:
        f.write(b"garbage-not-an-image")
    assert cache.lookup(url) is None
    assert not os.path.exists(stored)


def test_lookup_missing_file_returns_none(tmp_path):
    cache = ThumbCache(str(tmp_path))
    url = _url()
    stored = cache.store(url, _png(32, 16), False)
    os.remove(stored)
    assert cache.lookup(url) is None


def test_corrupt_index_never_raises(tmp_path):
    with open(os.path.join(str(tmp_path), "index.json"), "w", encoding="utf-8") as f:
        f.write("not json{")
    cache = ThumbCache(str(tmp_path))
    assert cache.lookup(_url()) is None
    assert cache.store(_url(), _png(16, 8), False) is not None
    assert cache.lookup(_url()) is not None


def test_store_never_raises_on_io_error(tmp_path, monkeypatch):
    cache = ThumbCache(str(tmp_path))
    monkeypatch.setattr(mod.os, "replace", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert cache.store(_url(), _png(16, 8), False) is None


# --- TTL -----------------------------------------------------------------


def test_ttl_vod_expiry(tmp_path, monkeypatch):
    clock = FakeClock()
    _patch_time(monkeypatch, clock)
    cache = ThumbCache(str(tmp_path), ttl_vod_s=100, ttl_live_s=100)
    url = _url()
    cache.store(url, _png(16, 8), False)
    clock.advance(50)
    assert cache.lookup(url) is not None
    clock.advance(60)
    assert cache.lookup(url) is None


def test_ttl_live_expiry_and_vod_survives(tmp_path, monkeypatch):
    clock = FakeClock()
    _patch_time(monkeypatch, clock)
    cache = ThumbCache(str(tmp_path), ttl_vod_s=1000, ttl_live_s=10)
    live_url, vod_url = _url(1), _url(2)
    cache.store(live_url, _png(16, 8), True)
    cache.store(vod_url, _png(16, 8), False)
    clock.advance(20)
    assert cache.lookup(live_url) is None
    assert cache.lookup(vod_url) is not None


# --- prune / eviction ----------------------------------------------------


def test_prune_enforces_max_files_lru(tmp_path, monkeypatch):
    clock = FakeClock()
    _patch_time(monkeypatch, clock)
    cache = ThumbCache(str(tmp_path), max_files=2)
    urls = [_url(i) for i in range(3)]
    for url in urls:
        cache.store(url, _png(16, 8), False)
        clock.advance(10)
    cache.prune(set(urls))
    assert cache.lookup(urls[0]) is None
    assert cache.lookup(urls[1]) is not None
    assert cache.lookup(urls[2]) is not None
    with open(os.path.join(str(tmp_path), "index.json"), encoding="utf-8") as f:
        assert len(json.load(f)) == 2


def test_prune_enforces_max_bytes(tmp_path, monkeypatch):
    clock = FakeClock()
    _patch_time(monkeypatch, clock)
    cache = ThumbCache(str(tmp_path))
    urls = [_url(i) for i in range(2)]
    sizes = []
    for url in urls:
        sizes.append(os.path.getsize(cache.store(url, _png(32, 16), False)))
        clock.advance(10)
    total = sum(sizes)
    assert total > sizes[-1]
    capped = ThumbCache(str(tmp_path), max_bytes=total - 1)
    capped.prune(set(urls))
    assert capped.lookup(urls[0]) is None
    assert capped.lookup(urls[1]) is not None


def test_prune_drops_non_live_and_rewrites_index(tmp_path):
    cache = ThumbCache(str(tmp_path))
    keep, drop = _url(1), _url(2)
    kept_path = cache.store(keep, _png(16, 8), False)
    dropped_path = cache.store(drop, _png(16, 8), False)
    cache.prune({keep})
    assert not os.path.exists(dropped_path)
    assert cache.lookup(keep) == kept_path
    with open(os.path.join(str(tmp_path), "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    assert list(index) == [canonical_url(keep)]


def test_prune_removes_orphans(tmp_path):
    cache = ThumbCache(str(tmp_path))
    url = _url()
    kept = cache.store(url, _png(16, 8), False)
    orphan_png = os.path.join(str(tmp_path), "deadbeef12345678.png")
    with open(orphan_png, "wb") as f:
        f.write(b"junk")
    orphan_tmp = kept + ".tmp"
    with open(orphan_tmp, "wb") as f:
        f.write(b"partial")
    cache.prune({url})
    assert not os.path.exists(orphan_png)
    assert not os.path.exists(orphan_tmp)
    assert cache.lookup(url) == kept


def test_prune_never_raises(tmp_path):
    cache = ThumbCache(os.path.join(str(tmp_path), "no-such-dir"))
    cache.prune(None)
    cache.prune({"http://[invalid"})
    ThumbCache("/proc/forbidden-cache-dir-xyz").prune(set())


# --- ThumbFetcher --------------------------------------------------------


def _run_threads(n, target):
    threads = [threading.Thread(target=target, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(not t.is_alive() for t in threads)


def test_fetch_ok_then_hit(tmp_path):
    cache = ThumbCache(str(tmp_path))
    fetcher = ThumbFetcher(cache)
    calls = []

    def fake(url):
        calls.append(url)
        return _png(64, 32)

    seen = []
    fetcher.fetch(_url(), fake, lambda p, r: seen.append((p, r)))
    assert len(seen) == 1 and seen[0][1] == "ok" and os.path.isfile(seen[0][0])
    fetcher.fetch(_url(), fake, lambda p, r: seen.append((p, r)))
    assert len(seen) == 2 and seen[1] == (seen[0][0], "hit")
    assert len(calls) == 1


def test_dedup_five_concurrent_single_fetch(tmp_path):
    cache = ThumbCache(str(tmp_path))
    fetcher = ThumbFetcher(cache)
    png = _png(64, 32)
    calls = []
    lock = threading.Lock()

    def fake(url):
        with lock:
            calls.append(url)
        time.sleep(0.3)
        return png

    barrier = threading.Barrier(5)
    results = []

    def worker(_i):
        barrier.wait(timeout=10)
        fetcher.fetch(
            "http://example.com/shared.png?utm_source=x",
            fake,
            lambda p, r: (lock.acquire(), results.append((p, r)), lock.release()),
        )

    _run_threads(5, worker)
    assert len(calls) == 1
    assert len(results) == 5
    assert {r for _, r in results} == {"ok"}
    assert len({p for p, _ in results}) == 1
    assert os.path.isfile(results[0][0])


def test_peak_concurrency_capped_at_two(tmp_path):
    cache = ThumbCache(str(tmp_path))
    fetcher = ThumbFetcher(cache, max_concurrent=2)
    state = {"current": 0, "peak": 0}
    lock = threading.Lock()

    def fake(url):
        with lock:
            state["current"] += 1
            state["peak"] = max(state["peak"], state["current"])
        try:
            time.sleep(0.05)
            return _png(32, 16)
        finally:
            with lock:
                state["current"] -= 1

    results = []

    def worker(i):
        fetcher.fetch(
            _url(i),
            fake,
            lambda p, r: (lock.acquire(), results.append((p, r)), lock.release()),
        )

    _run_threads(6, worker)
    assert len(results) == 6
    assert all(r == "ok" and p is not None for p, r in results)
    assert 1 <= state["peak"] <= 2


def test_error_table(tmp_path):
    cases = [
        ("value-error", ValueError("404"), "error"),
        ("timeout", TimeoutError("slow"), "timeout"),
        ("corrupt", b"\x00\x01\x02\x03" * 64, "bad_magic"),
        ("html", HTML, "bad_magic"),
        ("svg", SVG, "bad_magic"),
        ("truncated", _png(64, 32)[:40], "decode_failed"),
        ("oversize", _png(4100, 2), "too_big"),
        ("none-data", None, "fallback"),
        ("empty", b"", "bad_magic"),
    ]
    for name, payload, reason in cases:
        subdir = os.path.join(str(tmp_path), name)
        cache = ThumbCache(subdir)
        fetcher = ThumbFetcher(cache)
        seen = []

        def fake(url, _payload=payload):
            if isinstance(_payload, BaseException):
                raise _payload
            return _payload

        fetcher.fetch(
            f"http://example.com/{name}.png",
            fake,
            lambda p, r, _seen=seen: _seen.append((p, r)),
            timeout_note="note-ok",
        )
        assert seen == [(None, reason)], name
        assert _leftover_files(subdir) == [], name


def test_on_done_called_once_on_unexpected_error(tmp_path):
    fetcher = ThumbFetcher(ThumbCache(str(tmp_path)))
    seen = []
    fetcher.fetch(_url(), lambda url: 1 / 0, lambda p, r: seen.append((p, r)))
    assert seen == [(None, "error")]


def test_fetch_invalid_url_type_calls_error(tmp_path):
    fetcher = ThumbFetcher(ThumbCache(str(tmp_path)))
    seen = []
    fetcher.fetch(None, lambda url: b"x", lambda p, r: seen.append((p, r)))
    assert seen == [(None, "error")]


def test_inflight_slot_released_after_error(tmp_path):
    fetcher = ThumbFetcher(ThumbCache(str(tmp_path)))
    seen = []
    fetcher.fetch(
        _url(), lambda url: (_ for _ in ()).throw(RuntimeError("x")), lambda p, r: seen.append(r)
    )
    fetcher.fetch(_url(), lambda url: _png(16, 8), lambda p, r: seen.append((p, r)))
    assert seen[0] == "error"
    assert seen[1][1] == "ok" and os.path.isfile(seen[1][0])


# --- is_live_meta --------------------------------------------------------


def test_is_live_meta_table():
    assert is_live_meta({"is_live": True}) is True
    assert is_live_meta({"live_status": "is_live"}) is True
    assert is_live_meta({"is_live": False, "live_status": "is_live"}) is True
    assert is_live_meta({}) is False
    assert is_live_meta(None) is False
    assert is_live_meta("is_live") is False
    assert is_live_meta({"is_live": False}) is False
    assert is_live_meta({"is_live": 1}) is False
    assert is_live_meta({"is_live": None}) is False
    assert is_live_meta({"live_status": "is_upcoming"}) is False
    assert is_live_meta({"live_status": None}) is False


# --- logging redaction ---------------------------------------------------


def test_logs_never_contain_query(tmp_path, caplog):
    cache = ThumbCache(str(tmp_path))
    fetcher = ThumbFetcher(cache)
    url = "http://example.com/img.png?token=SECRET123&utm_source=x"
    with caplog.at_level(logging.INFO, logger="Apricus"):
        fetcher.fetch(url, lambda u: _png(32, 16), lambda p, r: None)
    assert "SECRET123" not in caplog.text
    assert "utm_source" not in caplog.text
    assert "example.com" in caplog.text


# --- headless / no-UI construction ---------------------------------------


def test_module_imports_no_ui_toolkit():
    # sys.modules is process-global, so verify in a fresh interpreter:
    # importing the module must not pull Gtk/Adw (UI updates stay caller-side).
    import subprocess

    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; sys.path.insert(0, 'src');"
            "import apricus.stream_thumbs;"
            "bad = [n for n in sys.modules if n.startswith('gi.repository.') and ('Gtk' in n or 'Adw' in n)];"
            "print('BAD:' + ','.join(bad));"
            "raise SystemExit(1 if bad else 0)",
        ],
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
        timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_module_source_has_no_ui_or_net_imports():
    with open(mod.__file__, encoding="utf-8") as f:
        src = f.read()
    for token in ("idle_add", "import Gtk", "Adw", "gtk4app"):
        assert token not in src
