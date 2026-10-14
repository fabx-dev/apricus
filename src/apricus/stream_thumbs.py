# Copyright (C) 2026 fabx-dev
# SPDX-License-Identifier: GPL-3.0-or-later

"""Disk cache and deduped fetcher for stream thumbnail images.

Headless-safe by construction: the only display-related import is
``gi.repository.GdkPixbuf`` used purely for decoding/scaling image bytes.
No widgets, no main-loop scheduling (this module never posts callbacks:
the caller runs ``ThumbFetcher.fetch`` on a worker thread and applies
results on the UI thread itself). Network access is fully injected via
a ``fetcher`` callable, so this module performs no I/O on sockets itself.
"""

import hashlib
import json
import logging
import os
import threading
import time
import urllib.parse

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import GdkPixbuf  # noqa: E402

logger = logging.getLogger("Apricus")

INDEX_FILENAME = "index.json"

#: Recognisable image file signatures. WEBP has no fixed prefix: it starts
#: with ``RIFF`` and carries a ``WEBP`` marker at offset 8 (see check_magic).
MAGIC = {
    "PNG": b"\x89\x50\x4e\x47",
    "JPEG": b"\xff\xd8\xff",
    "GIF": b"GIF8",
    "ICO": b"\x00\x00\x01\x00",
    "WEBP": b"RIFF",
}

_WEBP_MARKER = b"WEBP"
_WEBP_MARKER_OFFSET = 8

#: Query parameters stripped by canonical_url (pure trackers, they never
#: identify a different resource). Anything else is preserved verbatim.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "utm_content",
        "utm_term",
        "si",
        "fbclid",
        "gclid",
    }
)

#: Upper bound accepted by decode_size/downscale_png.
MAX_PIXELS = 16 * 1024 * 1024
MAX_WIDTH = 4096

#: Thumbnail width produced by downscale_png.
THUMB_WIDTH = 128

REASONS = frozenset(
    {
        "hit",
        "ok",
        "timeout",
        "error",
        "bad_magic",
        "decode_failed",
        "too_big",
        "fallback",
    }
)


def _redact(url):
    """Return a log-safe ``host/path`` view of *url* (no query/fragment)."""
    try:
        parts = urllib.parse.urlsplit(str(url))
        host = parts.hostname or parts.netloc or "?"
        path = parts.path or "/"
        return f"{host}{path}"
    except Exception:  # noqa: BLE001
        return "?"


def canonical_url(url):
    """Return the normalised identity of a thumbnail *url*.

    Strips whitespace, lowercases scheme+host, drops the fragment and removes
    only known tracking query parameters. Every other parameter is kept
    because it may identify a different resource.
    """
    if not isinstance(url, str):
        raise TypeError(f"url must be str, got {type(url).__name__}")
    parts = urllib.parse.urlsplit(url.strip())
    scheme = parts.scheme.lower()
    netloc = parts.netloc
    if netloc:
        userinfo, at, hostport = netloc.rpartition("@")
        host, colon, port = hostport.partition(":")
        host = host.lower()
        hostport = host + (colon + port if colon else "")
        netloc = (userinfo + at if at else "") + hostport
    pairs = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    kept = [(k, v) for k, v in pairs if k.lower() not in _TRACKING_PARAMS]
    query = urllib.parse.urlencode(kept, doseq=True)
    return urllib.parse.urlunsplit((scheme, netloc, parts.path, query, ""))


def cache_path_for(url, cache_dir):
    """Return the cache file path for *url* inside *cache_dir*.

    The filename derives only from the sha256 of the canonical URL, so raw
    user input can never cause path traversal.
    """
    digest = hashlib.sha256(canonical_url(url).encode("utf-8")).hexdigest()[:16]
    return os.path.join(cache_dir, digest + ".png")


def check_magic(data):
    """Return True when *data* starts with a known image signature."""
    if not isinstance(data, (bytes, bytearray)) or len(data) < 3:
        return False
    blob = bytes(data)
    for name, prefix in MAGIC.items():
        if len(blob) < len(prefix):
            continue
        if not blob.startswith(prefix):
            continue
        if name == "WEBP":
            if len(blob) < _WEBP_MARKER_OFFSET + len(_WEBP_MARKER):
                return False
            return blob[_WEBP_MARKER_OFFSET : _WEBP_MARKER_OFFSET + 4] == _WEBP_MARKER
        return True
    return False


def _load_pixbuf(data):
    """Decode *data* with PixbufLoader; return a Pixbuf or None."""
    try:
        loader = GdkPixbuf.PixbufLoader.new()
    except Exception:  # noqa: BLE001
        return None
    try:
        loader.write(bytes(data))
        loader.close()
    except Exception:  # noqa: BLE001
        try:
            loader.close()
        except Exception:  # noqa: BLE001
            pass
        return None
    try:
        return loader.get_pixbuf()
    except Exception:  # noqa: BLE001
        return None


def _check_limits(width, height):
    if width <= 0 or height <= 0:
        return False
    if width > MAX_WIDTH:
        return False
    return width * height <= MAX_PIXELS


def decode_size(data):
    """Return ``(width, height)`` for image *data*, or None if unusable.

    Uses PixbufLoader (decode only, nothing display-bound). Rejects
    non-images, corrupt/truncated payloads and absurd dimensions. Anything
    without a known image signature (SVG/text included) is refused up front
    so behaviour does not depend on optional loaders present on the system.
    """
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None
    if not check_magic(data):
        return None
    pixbuf = _load_pixbuf(data)
    if pixbuf is None:
        return None
    try:
        width = pixbuf.get_width()
        height = pixbuf.get_height()
    except Exception:  # noqa: BLE001
        return None
    if not _check_limits(width, height):
        return None
    return (width, height)


def downscale_png(data, max_w=THUMB_WIDTH):
    """Decode *data* and return PNG bytes at most *max_w* wide, else None."""
    if not isinstance(data, (bytes, bytearray)) or not data:
        return None
    if not check_magic(data):
        return None
    pixbuf = _load_pixbuf(data)
    if pixbuf is None:
        return None
    try:
        width = pixbuf.get_width()
        height = pixbuf.get_height()
    except Exception:  # noqa: BLE001
        return None
    if not _check_limits(width, height):
        return None
    try:
        if width > max_w:
            new_h = max(1, round(height * max_w / width))
            scaled = pixbuf.scale_simple(max_w, new_h, GdkPixbuf.InterpType.BILINEAR)
            if scaled is None:
                return None
            pixbuf = scaled
        ok, buf = pixbuf.save_to_bufferv("png", [], [])
    except Exception:  # noqa: BLE001
        return None
    if not ok or buf is None:
        return None
    return bytes(buf)


def is_live_meta(meta):
    """Return True only for an actually-live stream metadata dict."""
    try:
        if not isinstance(meta, dict):
            return False
        if meta.get("is_live") is True:
            return True
        return meta.get("live_status") == "is_live"
    except Exception:  # noqa: BLE001
        return False


class ThumbCache:
    """TTL disk cache of downscaled thumbnails with a sidecar index.

    Layout: ``<cache_dir>/<sha256(canonical)[:16]>.png`` files plus an
    ``index.json`` sidecar mapping canonical URL to
    ``{"file": basename, "fetched_at": epoch, "is_live": bool}``.
    Never raises on OSError or corrupt JSON: failures yield None and the
    operation is skipped.
    """

    def __init__(
        self,
        cache_dir,
        ttl_vod_s=30 * 86400,
        ttl_live_s=12 * 3600,
        max_files=64,
        max_bytes=8 * 1024 * 1024,
    ):
        self.cache_dir = cache_dir
        self.ttl_vod_s = ttl_vod_s
        self.ttl_live_s = ttl_live_s
        self.max_files = max_files
        self.max_bytes = max_bytes
        self._index = None
        self._index_mtime = None

    def _index_path(self):
        return os.path.join(self.cache_dir, INDEX_FILENAME)

    def _load_index(self):
        try:
            mtime = os.stat(self._index_path()).st_mtime
            if self._index is not None and self._index_mtime == mtime:
                return self._index
            with open(self._index_path(), encoding="utf-8") as f:
                data = json.load(f)
            index = data if isinstance(data, dict) else {}
            self._index = index
            self._index_mtime = mtime
            return index
        except (OSError, ValueError):
            if self._index is None:
                self._index = {}
            return self._index
        except Exception:  # noqa: BLE001
            if self._index is None:
                self._index = {}
            return self._index

    def _save_index(self, index):
        try:
            os.makedirs(self.cache_dir, exist_ok=True)
            tmp = self._index_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(index, f)
            os.replace(tmp, self._index_path())
            try:
                self._index_mtime = os.stat(self._index_path()).st_mtime
            except OSError:
                self._index_mtime = None
        except (OSError, ValueError, TypeError):
            logger.warning("[StreamThumbs] index save failed")
        except Exception:  # noqa: BLE001
            logger.warning("[StreamThumbs] index save failed")

    def _remove_entry(self, canon, index):
        index.pop(canon, None)
        try:
            path = cache_path_for(canon, self.cache_dir)
        except Exception:  # noqa: BLE001
            return
        try:
            os.remove(path)
        except OSError:
            pass

    def lookup(self, url):
        """Return the cached thumbnail path for *url*, or None on miss.

        A hit requires a fresh index entry plus an existing, non-corrupt
        file. Stale, missing or corrupt entries yield None and the garbage
        (file and/or index entry) is removed.
        """
        try:
            canon = canonical_url(url)
        except Exception:  # noqa: BLE001
            return None
        try:
            index = self._load_index()
            entry = index.get(canon)
            if not isinstance(entry, dict):
                return None
            fetched_at = entry.get("fetched_at")
            if not isinstance(fetched_at, (int, float)):
                self._remove_entry(canon, index)
                self._save_index(index)
                return None
            ttl = self.ttl_live_s if entry.get("is_live") is True else self.ttl_vod_s
            try:
                age = time.time() - float(fetched_at)
            except Exception:  # noqa: BLE001
                return None
            if age < 0:
                age = 0
            if age > ttl:
                self._remove_entry(canon, index)
                self._save_index(index)
                return None
            path = cache_path_for(canon, self.cache_dir)
            try:
                with open(path, "rb") as f:
                    blob = f.read()
            except OSError:
                self._remove_entry(canon, index)
                self._save_index(index)
                return None
            if not blob or not check_magic(blob):
                self._remove_entry(canon, index)
                self._save_index(index)
                return None
            return path
        except Exception:  # noqa: BLE001
            return None

    def store(self, url, data, is_live):
        """Validate, downscale and atomically store *data*; path or None."""
        try:
            canon = canonical_url(url)
        except Exception:  # noqa: BLE001
            return None
        try:
            if isinstance(data, bytearray):
                data = bytes(data)
            if not isinstance(data, bytes) or not data:
                return None
            if not check_magic(data):
                return None
            if decode_size(data) is None:
                return None
            png = downscale_png(data)
            if png is None:
                return None
            os.makedirs(self.cache_dir, exist_ok=True)
            path = cache_path_for(canon, self.cache_dir)
            tmp = path + ".tmp"
            try:
                with open(tmp, "wb") as f:
                    f.write(png)
                os.replace(tmp, path)
            except OSError:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                return None
            index = self._load_index()
            try:
                now = time.time()
            except Exception:  # noqa: BLE001
                now = 0.0
            index[canon] = {
                "file": os.path.basename(path),
                "fetched_at": now,
                "is_live": bool(is_live),
            }
            self._save_index(index)
            return path
        except Exception:  # noqa: BLE001
            return None

    def prune(self, live_urls):
        """Drop cached files not in *live_urls* and enforce size caps.

        Eviction beyond ``max_files``/``max_bytes`` follows LRU on
        ``fetched_at`` (oldest first). Orphan ``*.png`` files and stale
        ``*.tmp`` leftovers are removed too, and the index is rewritten.
        """
        try:
            live = set()
            try:
                candidates = set(live_urls) if live_urls else set()
            except TypeError:
                candidates = set()
            for raw in candidates:
                try:
                    live.add(canonical_url(raw))
                except Exception:  # noqa: BLE001
                    continue
            index = self._load_index()
            for canon in list(index.keys()):
                if canon not in live:
                    self._remove_entry(canon, index)

            def _ts(canon):
                entry = index.get(canon)
                ts = entry.get("fetched_at") if isinstance(entry, dict) else None
                return ts if isinstance(ts, (int, float)) else 0.0

            ordered = sorted(list(index.keys()), key=_ts)
            while len(ordered) > max(0, self.max_files):
                self._remove_entry(ordered.pop(0), index)

            def _size(canon):
                try:
                    return os.path.getsize(cache_path_for(canon, self.cache_dir))
                except (OSError, TypeError, ValueError):
                    return 0

            ordered = sorted(list(index.keys()), key=_ts)
            total = sum(_size(c) for c in ordered)
            while ordered and total > self.max_bytes:
                victim = ordered.pop(0)
                total -= _size(victim)
                self._remove_entry(victim, index)

            try:
                names = os.listdir(self.cache_dir)
            except OSError:
                names = []
            referenced = set()
            for canon in index:
                try:
                    referenced.add(os.path.basename(cache_path_for(canon, self.cache_dir)))
                except Exception:  # noqa: BLE001
                    continue
            for name in names:
                if name == INDEX_FILENAME or name in referenced:
                    continue
                if name.endswith(".png") or name.endswith(".tmp"):
                    try:
                        os.remove(os.path.join(self.cache_dir, name))
                    except OSError:
                        continue
            for canon in list(index.keys()):
                try:
                    if not os.path.isfile(cache_path_for(canon, self.cache_dir)):
                        index.pop(canon, None)
                except Exception:  # noqa: BLE001
                    continue
            self._save_index(index)
        except Exception:  # noqa: BLE001
            pass


class ThumbFetcher:
    """Deduped, concurrency-limited thumbnail fetcher.

    :meth:`fetch` runs entirely in the calling (worker) thread: it checks
    the cache, coalesces concurrent callers for the same canonical URL and
    bounds network use with a semaphore. ``fetcher`` is a caller-provided
    callable ``fetcher(url) -> bytes`` (already size-limited by the caller;
    it may raise). ``on_done(path_or_None, reason)`` is always invoked
    exactly once per :meth:`fetch` call, with ``reason`` in ``REASONS``.
    This class never touches UI toolkits: the caller schedules workers and
    applies results on the UI thread.
    """

    def __init__(self, cache, max_concurrent=2):
        self.cache = cache
        self._sem = threading.Semaphore(max_concurrent)
        self._lock = threading.Lock()
        self._inflight = {}

    @staticmethod
    def _safe_done(on_done, path, reason):
        try:
            on_done(path, reason)
        except Exception:  # noqa: BLE001
            logger.warning("[StreamThumbs] on_done callback failed")

    def _do_fetch(self, url, canon, fetcher, is_live, timeout_note):
        try:
            data = fetcher(url)
        except TimeoutError:
            extra = f" ({timeout_note})" if timeout_note else ""
            logger.warning(f"[StreamThumbs] timeout for {_redact(url)}{extra}")
            return None, "timeout"
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[StreamThumbs] fetch failed for {_redact(url)}: {e}")
            return None, "error"
        if data is None:
            logger.info(f"[StreamThumbs] fallback (empty) for {_redact(url)}")
            return None, "fallback"
        if isinstance(data, bytearray):
            data = bytes(data)
        if not isinstance(data, bytes) or not data or not check_magic(data):
            logger.info(f"[StreamThumbs] fallback bad_magic for {_redact(url)}")
            return None, "bad_magic"
        pixbuf = _load_pixbuf(data)
        if pixbuf is None:
            logger.info(f"[StreamThumbs] fallback decode_failed for {_redact(url)}")
            return None, "decode_failed"
        try:
            width = pixbuf.get_width()
            height = pixbuf.get_height()
        except Exception:  # noqa: BLE001
            logger.info(f"[StreamThumbs] fallback decode_failed for {_redact(url)}")
            return None, "decode_failed"
        if not _check_limits(width, height):
            logger.info(f"[StreamThumbs] fallback too_big for {_redact(url)}")
            return None, "too_big"
        try:
            stored = self.cache.store(url, data, is_live)
        except Exception:  # noqa: BLE001
            stored = None
        if stored is None:
            logger.info(f"[StreamThumbs] fallback store_failed for {_redact(url)}")
            return None, "fallback"
        logger.info(f"[StreamThumbs] ok for {_redact(url)}")
        return stored, "ok"

    def fetch(self, url, fetcher, on_done, is_live=False, timeout_note=None):
        """Fetch the thumbnail for *url* in the calling thread."""
        try:
            canon = canonical_url(url)
        except Exception:  # noqa: BLE001
            self._safe_done(on_done, None, "error")
            return
        try:
            hit = self.cache.lookup(url)
        except Exception:  # noqa: BLE001
            hit = None
        if hit is not None:
            logger.debug(f"[StreamThumbs] hit for {_redact(url)}")
            self._safe_done(on_done, hit, "hit")
            return
        with self._lock:
            waiters = self._inflight.get(canon)
            if waiters is not None:
                waiters.append((on_done, is_live))
                return
            self._inflight[canon] = [(on_done, is_live)]
        owner_is_live = is_live
        self._sem.acquire()
        try:
            try:
                path, reason = self._do_fetch(url, canon, fetcher, owner_is_live, timeout_note)
            except Exception:  # noqa: BLE001
                path, reason = None, "error"
        finally:
            with self._lock:
                waiters = self._inflight.pop(canon, [(on_done, owner_is_live)])
            self._sem.release()
        for callback, _live in waiters:
            self._safe_done(callback, path, reason)
